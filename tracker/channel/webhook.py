"""The HTTP surface: one signed POST, plus a health check that holds no content.

Order matters in `receive`: nothing is parsed until the size and the signature have
passed, so forged input reaches no code (T1, T5). A failure answers 500 so WAHA sends the
event again, and the message id makes that safe. A failed reply answers 200: the entry
is committed, and a retry would only repeat a message already handled.

This is the only place that sends, and only in reply to an inbound message, so the bot
can't start a conversation or send anything in bulk (T41).

The docs routes are off, because nobody but WAHA has a reason to be here.
"""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.telemetry import TelemetryConfig
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.channel.conversation import Conversation
from tracker.channel.gateway import (
    Attachment,
    ChannelStopped,
    Gateway,
    IncomingMessage,
    MalformedEventError,
    SignatureCheck,
)
from tracker.channel.ratelimit import MessageLimiter
from tracker.channel.settings import Settings
from tracker.channel.tracing import configure_tracing, traced
from tracker.channel.waha import WahaGateway
from tracker.ledger.engine import create_engine

logger: Final = logging.getLogger("tracker.channel")

WEBHOOK_PATH: Final = "/webhook"
HEALTH_PATH: Final = "/healthz"
MAX_BODY_BYTES: Final = 256 * 1024  # A text event is a few KB. Caddy enforces this too.
WAHA_TIMEOUT_SECONDS: Final = 10.0
# FastAPI traces itself whenever OpenTelemetry is installed: URL query strings, and exception
# messages as log events. Only tracing.py's spans, which hold no content, may leave (T14).
_NO_FASTAPI_TELEMETRY: Final[TelemetryConfig] = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}
# httpx logs each URL at INFO, and a sender's id can be in the path (T14).
_CHATTY_LIBRARIES: Final = ("httpx", "httpcore", "sqlalchemy.engine", "asyncio")


async def _read_capped_body(request: Request) -> bytes | None:
    """The body, or None past the limit, without trusting Content-Length."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


async def _send(gateway: Gateway, to: str, reply: str | Attachment) -> None:
    if isinstance(reply, Attachment):
        await gateway.send_file(to, reply)
    else:
        await gateway.send_text(to, reply)


async def _reply_to(gateway: Gateway, conversation: Conversation, message: IncomingMessage) -> None:
    sender = await gateway.identify(message.chat_id)
    reply = await conversation.handle(sender, message)
    if reply is None:
        return
    if not await conversation.limiter.may_reply():
        logger.warning("reply_limited")  # The entry is safe: only the echo stops (R7).
        return
    try:
        with traced("whatsapp.send"):
            await _send(gateway, sender.reply_to, reply)
    except Exception as error:  # Any send failure is reported the same way, by type alone.
        logger.error("reply_failed error_type=%s", type(error).__name__)


async def _dispatch(gateway: Gateway, conversation: Conversation, body: bytes) -> Response:
    try:
        event = gateway.read_event(json.loads(body))
    except (ValueError, MalformedEventError) as error:
        logger.warning("webhook_malformed error_type=%s", type(error).__name__)
        return Response(status_code=400)
    if isinstance(event, ChannelStopped):
        logger.error("channel_stopped status=%s", event.status)  # Perhaps a ban: E3 alerts on this.
    elif event is not None:
        await _reply_to(gateway, conversation, event)
    return PlainTextResponse("ok")


async def _handle_webhook(request: Request, gateway: Gateway, conversation: Conversation) -> Response:
    body = await _read_capped_body(request)
    if body is None:
        return Response(status_code=413)
    signature = gateway.check_signature(body, request.headers)
    if signature is SignatureCheck.MISSING:
        return Response(status_code=401)
    if signature is SignatureCheck.INVALID:
        return Response(status_code=403)
    try:
        return await _dispatch(gateway, conversation, body)
    except Exception as error:  # A traceback here could hold message text.
        logger.error("webhook_failed error_type=%s", type(error).__name__)
        return Response(status_code=500)


async def _health(engine: AsyncEngine, version: str) -> Response:
    """R5: checks the database, and names the build for the drift check. It holds no content."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, TimeoutError, DBAPIError, OperationalError):
        return JSONResponse({"status": "unavailable", "version": version}, status_code=503)
    return JSONResponse({"status": "ok", "version": version})


def create_waha_client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    """Sends happen inside the webhook, so a hung call must not hold it open."""
    return httpx.AsyncClient(transport=transport, timeout=WAHA_TIMEOUT_SECONDS)


def create_app(settings: Settings, waha_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Builds the app. `waha_transport` lets tests stand in for WAHA, so none is needed."""
    for name in _CHATTY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    tracer_provider = configure_tracing(settings.otlp_endpoint, settings.version)
    engine = create_engine(settings.database_url.get_secret_value())
    http_client = create_waha_client(waha_transport)
    gateway = WahaGateway(
        client=http_client,
        base_url=settings.waha_url,
        api_key=settings.waha_api_key.get_secret_value(),
        session=settings.waha_session,
        webhook_key=settings.webhook_key.get_secret_value(),
    )
    conversation = Conversation(engine, MessageLimiter(), settings.human_contact)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        await http_client.aclose()
        await engine.dispose()
        if tracer_provider is not None:
            tracer_provider.shutdown()  # A batch still in memory at SIGTERM would be lost.

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, telemetry=_NO_FASTAPI_TELEMETRY)

    @app.get(HEALTH_PATH)
    async def health() -> Response:
        return await _health(engine, settings.version)

    @app.post(WEBHOOK_PATH)
    async def receive(request: Request) -> Response:
        with traced("whatsapp.webhook", SpanKind.SERVER):
            return await _handle_webhook(request, gateway, conversation)

    return app
