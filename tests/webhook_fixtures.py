"""Invented, signed WAHA events, and a stand-in for WAHA's API. No real data, no network.

The shapes come from WAHA's docs, not from a live session. The slice plan's live check
confirms them.
"""

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from tracker.channel.settings import Settings

WEBHOOK_KEY: Final = "test-webhook-key-not-a-real-one-0"
API_KEY: Final = "test-api-key-not-a-real-one-00000"
SESSION: Final = "default"
HUMAN_CONTACT: Final = "the-test-contact"
WEBHOOK_PATH: Final = "/webhook"
WAHA_URL: Final = "http://127.0.0.1:3000"


def app_environment(database_url: str) -> dict[str, str]:
    return {
        "TRACKER_DATABASE_URL": database_url,
        "WAHA_URL": WAHA_URL,
        "WAHA_SESSION": SESSION,
        "WAHA_API_KEY": API_KEY,
        "WAHA_WEBHOOK_HMAC_KEY": WEBHOOK_KEY,
        "BANTUAN_CONTACT": HUMAN_CONTACT,
    }


def app_settings(database_url: str) -> Settings:
    return Settings.from_environment(app_environment(database_url))


def signature_for(body: bytes, secret: str = WEBHOOK_KEY) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def signed_headers(body: bytes) -> dict[str, str]:
    return {"X-Webhook-Hmac": signature_for(body), "X-Webhook-Hmac-Algorithm": "sha512"}


def new_lid() -> str:
    return f"{uuid.uuid4().hex}@lid"


def new_message_id() -> str:
    return f"false_{uuid.uuid4().hex}"


def _event(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"id": "evt_test", "timestamp": 1, "event": name, "session": SESSION, "engine": "GOWS", "payload": payload}


def text_event(
    sender: str, text: str, *, message_id: str | None = None, from_me: bool = False, sent_at: int | None = None
) -> dict[str, Any]:
    payload = {
        "id": message_id or new_message_id(),
        "timestamp": sent_at if sent_at is not None else int(time.time()),
        "from": sender,
        "fromMe": from_me,
        "body": text,
        "hasMedia": False,
        "source": "app",
    }
    return _event("message", payload)


def image_event(sender: str, caption: str = "") -> dict[str, Any]:
    event = text_event(sender, caption)
    event["payload"]["hasMedia"] = True
    return event


def status_event(status: str) -> dict[str, Any]:
    return _event("session.status", {"name": SESSION, "status": status})


@dataclass
class FakeWaha:
    """Stands in for WAHA's API, and keeps what the bot sent."""

    pairs: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # (lid, phone digits)
    texts: list[str] = field(default_factory=list[str])
    files: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    failures_left: int = 0
    sent_to: list[str] = field(default_factory=list[str])
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])
    is_down: bool = False
    is_lookup_down: bool = False

    def pair(self, lid: str, phone: str) -> None:
        self.pairs.append((lid, phone))

    def _lookup(self, path: str) -> httpx.Response:
        if self.is_lookup_down:
            return httpx.Response(500)
        key = httpx.URL(path).path.rsplit("/", 1)[-1]
        for lid, phone in self.pairs:
            if key in (lid, phone):
                return httpx.Response(200, json={"lid": lid, "pn": f"{phone}@c.us"})
        return httpx.Response(404)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.raw_path.decode()
        if "/lids/" in path:
            return self._lookup(httpx.URL(path.replace("%40", "@")).path)
        if self.is_down:
            return httpx.Response(500, json={"error": "down"})
        if self.failures_left > 0:
            self.failures_left -= 1
            return httpx.Response(503)
        if path.endswith("/sendFile"):
            self.files.append(json.loads(request.content))
        if path.endswith("/sendText"):
            body = json.loads(request.content)
            self.texts.append(body["text"])
            self.sent_to.append(body["chatId"])
        return httpx.Response(200, json={})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
