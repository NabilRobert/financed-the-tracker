"""WAHA: the only file that knows its API. See ADR-022 before changing what it trusts.

The shapes here come from WAHA's docs, not from a live session, so the live check in the
slice plan confirms them. Anything unexpected in a signed event fails rather than being
guessed at, because a wrong sender writes to the wrong ledger.

Only direct chats reach the bot: `@c.us` and `@lid`. Its own messages, groups, broadcasts
and channels are dropped here, so it can't loop or reply where others can read (T42).
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import quote

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from tracker.channel.gateway import (
    Attachment,
    ChannelStopped,
    IncomingMessage,
    MalformedEventError,
    Sender,
    SignatureCheck,
)

HMAC_HEADER: Final = "x-webhook-hmac"
ALGORITHM_HEADER: Final = "x-webhook-hmac-algorithm"
ALGORITHM: Final = "sha512"
API_KEY_HEADER: Final = "X-Api-Key"  # Never the query string: URLs end up in logs (T6).
LID_SUFFIX: Final = "@lid"
PHONE_SUFFIX: Final = "@c.us"
MESSAGE_EVENT: Final = "message"
STATUS_EVENT: Final = "session.status"
STOPPED_STATUSES: Final = frozenset({"STOPPED", "FAILED"})
HTTP_NOT_FOUND: Final = 404
HTTP_SERVER_ERROR: Final = 500
SEND_ATTEMPTS: Final = 3  # R3. The webhook waits on these, so they stay few.
EXPORT_MIMETYPE: Final = "text/csv"


@dataclass(frozen=True)
class WahaGateway:
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    session: str
    webhook_key: str
    retry_wait_ms: int = 200

    def check_signature(self, body: bytes, headers: Mapping[str, str]) -> SignatureCheck:
        signature = headers.get(HMAC_HEADER)
        if not signature:
            return SignatureCheck.MISSING
        if headers.get(ALGORITHM_HEADER) != ALGORITHM:
            return SignatureCheck.INVALID
        expected = hmac.new(self.webhook_key.encode(), body, hashlib.sha512).hexdigest()
        # Bytes, because compare_digest raises on non-ASCII text.
        is_valid = hmac.compare_digest(expected.encode(), signature.encode())
        return SignatureCheck.VALID if is_valid else SignatureCheck.INVALID

    def read_event(self, event: Any) -> IncomingMessage | ChannelStopped | None:
        try:
            if event["session"] != self.session:
                return None
            if event["event"] == STATUS_EVENT:
                return _read_status(event["payload"])
            return _read_message(event["payload"]) if event["event"] == MESSAGE_EVENT else None
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise MalformedEventError(type(error).__name__) from None

    async def identify(self, chat_id: str) -> Sender:
        """The stable id for a chat: its LID when WAHA has one, since `@c.us` is the number."""
        if chat_id.endswith(LID_SUFFIX):
            pairing = await self._read_pairing(f"lids/{quote(chat_id, safe='')}")
            return Sender(chat_id, _phone_digits(pairing.get("pn")), chat_id)
        phone = chat_id.removesuffix(PHONE_SUFFIX)
        pairing = await self._read_pairing(f"lids/pn/{quote(phone, safe='')}")
        return Sender(pairing.get("lid") or chat_id, phone, chat_id)

    async def send_text(self, to: str, text: str) -> None:
        """Marks the chat seen, as WAHA advises, then replies. Link previews stay off."""
        chat = {"session": self.session, "chatId": to}
        await self._post("sendSeen", chat)
        await self._post("sendText", {**chat, "text": text, "linkPreview": False})

    async def send_file(self, to: str, attachment: Attachment) -> None:
        file = {
            "mimetype": EXPORT_MIMETYPE,
            "filename": attachment.filename,
            "data": base64.b64encode(attachment.content).decode(),
        }
        await self._post(
            "sendFile", {"session": self.session, "chatId": to, "file": file, "caption": attachment.caption}
        )

    async def _read_pairing(self, path: str) -> dict[str, Any]:
        """A LID and phone pairing, empty if WAHA doesn't know one. An outage raises, so the
        message isn't lost: the webhook answers 500 and WAHA retries."""
        response = await self.client.get(
            f"{self.base_url}/api/{self.session}/{path}", headers={API_KEY_HEADER: self.api_key}
        )
        if response.status_code == HTTP_NOT_FOUND:
            return {}
        response.raise_for_status()
        pairing: dict[str, Any] = response.json()
        return pairing

    async def _post(self, path: str, body: dict[str, Any]) -> None:
        """Retries only what can't have reached WhatsApp twice: no connection, or WAHA failing."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(SEND_ATTEMPTS),
            wait=wait_exponential(multiplier=self.retry_wait_ms / 1000),
            retry=retry_if_exception(_is_worth_retrying),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                response = await self.client.post(
                    f"{self.base_url}/api/{path}", json=body, headers={API_KEY_HEADER: self.api_key}
                )
                response.raise_for_status()


def _is_worth_retrying(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= HTTP_SERVER_ERROR
    return isinstance(error, httpx.ConnectError | httpx.ConnectTimeout)


def _phone_digits(chat_id: object) -> str | None:
    if not isinstance(chat_id, str) or not chat_id.endswith(PHONE_SUFFIX):
        return None
    return chat_id.removesuffix(PHONE_SUFFIX)


def _read_status(payload: dict[str, Any]) -> ChannelStopped | None:
    status = payload["status"]
    return ChannelStopped(status) if status in STOPPED_STATUSES else None


def _is_direct_chat(chat_id: str) -> bool:
    is_direct = chat_id.endswith((PHONE_SUFFIX, LID_SUFFIX))
    return is_direct and not chat_id.startswith("status@")


def _read_message(payload: dict[str, Any]) -> IncomingMessage | None:
    chat_id, message_id = payload["from"], payload["id"]
    if not isinstance(chat_id, str) or not isinstance(message_id, str) or not message_id:
        raise MalformedEventError("BadIds")
    if payload["fromMe"] or not _is_direct_chat(chat_id):
        return None
    body = payload.get("body")
    is_text = isinstance(body, str) and body != "" and not payload.get("hasMedia", False)
    sent_at = datetime.fromtimestamp(int(payload["timestamp"]), tz=UTC)
    return IncomingMessage(message_id, chat_id, body if is_text else None, sent_at)
