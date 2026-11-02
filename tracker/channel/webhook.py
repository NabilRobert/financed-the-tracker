"""The HTTP surface: one verification GET and one signed POST, and nothing else.

These are our routes, not pywa's. pywa's would answer 200 when a handler failed, and
read only the first message of a webhook. Order matters in `receive`: nothing is parsed
until the size and the signature have passed, so forged input reaches no code (T1, T5).

The docs routes are off, because nobody but Meta has a reason to be here.
"""

import hmac
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Final

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from pywa import utils
from pywa_async.client import WhatsApp

from tracker.channel.bot import register_handlers
from tracker.channel.conversation import Conversation
from tracker.channel.failures import begin_request, contain_library_logs, has_failed, logger, note_failure
from tracker.channel.ratelimit import MessageLimiter
from tracker.channel.settings import Settings
from tracker.ledger.engine import create_engine

WEBHOOK_PATH: Final = "/webhook"
MAX_BODY_BYTES: Final = 256 * 1024  # Meta's text webhooks are a few KB. Caddy enforces this too.
GRAPH_TIMEOUT_SECONDS: Final = 10.0
SIGNATURE_PREFIX: Final = "sha256="


def _ok(body: str = "ok") -> Response:
    return PlainTextResponse(body)


def _is_signed(body: bytes, signature: str, app_secret: str) -> bool:
    """False for anything but an exact match. compare_digest raises on non-ASCII text."""
    if not signature.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(app_secret.encode(), body, "sha256").hexdigest()
    return hmac.compare_digest(expected.encode(), signature.removeprefix(SIGNATURE_PREFIX).encode())


async def _read_capped_body(request: Request) -> bytes | None:
    """The body, or None past the limit, without trusting Content-Length."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _contact_for(message: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The sender of one message in a batch. A guess here could write to the wrong ledger."""
    sender_bsuid, sender_phone = message.get("from_user_id"), message.get("from")
    if len(contacts) == 1:
        return contacts[0]
    for contact in contacts:
        # Only a real id may match: two missing phone numbers are not the same person.
        is_same_bsuid = sender_bsuid is not None and contact.get("user_id") == sender_bsuid
        is_same_phone = sender_phone is not None and contact.get("wa_id") == sender_phone
        if is_same_bsuid or is_same_phone:
            return contact
    return None


def split_into_single_messages(payload: dict[str, Any]) -> tuple[list[bytes], int]:
    """One body per message, since pywa reads only the first. Also counts unmatched senders."""
    bodies: list[bytes] = []
    unmatched = 0
    for entry in payload["entry"]:
        for change in entry["changes"]:
            value = change["value"]
            contacts = value.get("contacts", [])
            for message in value.get("messages", []):
                contact = _contact_for(message, contacts)
                is_system_message = message.get("type") == "system"  # Meta sends these with no contact.
                if contact is None and not is_system_message:
                    unmatched += 1
                    continue
                single_value = {**value, "messages": [message], "contacts": [contact] if contact else []}
                single = {**payload, "entry": [{**entry, "changes": [{**change, "value": single_value}]}]}
                bodies.append(json.dumps(single).encode())
    return bodies, unmatched


async def _dispatch(wa: WhatsApp, body: bytes) -> Response:
    """Hands each message to pywa. Any failure answers 500, so Meta sends the webhook again."""
    try:
        bodies, unmatched = split_into_single_messages(json.loads(body))
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("webhook_malformed error_type=%s", type(error).__name__)
        return Response(status_code=400)
    begin_request()
    if unmatched:
        note_failure("UnmatchedSender", "split")
    for single_body in bodies:
        await wa.webhook_update_handler(single_body)
    return Response(status_code=500) if has_failed() else _ok()


def create_graph_client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    """Sends happen inside the webhook, so a hung call to Meta must not hold it open."""
    return httpx.AsyncClient(transport=transport, timeout=GRAPH_TIMEOUT_SECONDS)


def create_app(settings: Settings, graph_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Builds the app. `graph_transport` lets tests stand in for Meta's API, so none reach it."""
    contain_library_logs()
    engine = create_engine(settings.database_url.get_secret_value())
    http_client = create_graph_client(graph_transport)
    # skip_duplicate_updates stays off: pywa's cache would swallow Meta's retry after a
    # failure. The database's unique message id is what stops repeats (L5).
    wa = WhatsApp(
        phone_id=settings.phone_number_id,
        token=settings.access_token.get_secret_value(),
        session=http_client,
        skip_duplicate_updates=False,
    )
    register_handlers(wa, Conversation(engine, MessageLimiter(), settings.human_contact))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        await http_client.aclose()
        await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get(WEBHOOK_PATH)
    async def verify(request: Request) -> Response:
        token = request.query_params.get(utils.HUB_VT, "")
        expected = settings.verify_token.get_secret_value()
        if not hmac.compare_digest(token.encode(), expected.encode()):
            return Response(status_code=403)
        return _ok(request.query_params.get(utils.HUB_CH, ""))

    @app.post(WEBHOOK_PATH)
    async def receive(request: Request) -> Response:
        body = await _read_capped_body(request)
        if body is None:
            return Response(status_code=413)
        signature = request.headers.get(utils.HUB_SIG)
        if not signature:
            return Response(status_code=401)
        if not _is_signed(body, signature, settings.app_secret.get_secret_value()):
            return Response(status_code=403)
        try:
            return await _dispatch(wa, body)
        except Exception as error:  # a traceback here could hold message text.
            logger.error("webhook_failed stage=dispatch error_type=%s", type(error).__name__)
            return Response(status_code=500)

    return app
