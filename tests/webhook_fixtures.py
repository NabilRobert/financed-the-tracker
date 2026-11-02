"""Invented, signed webhooks in Meta's shape, and a stand-in for Meta's API. No real data, no network."""

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from tracker.channel.buttons import agree_payload
from tracker.channel.replies import CONSENT_NOTICE_VERSION
from tracker.channel.settings import Settings

APP_SECRET: Final = "test-app-secret-not-a-real-one-00"
VERIFY_TOKEN: Final = "test-verify-token-not-a-real-one"
PHONE_NUMBER_ID: Final = "100000000000000"
HUMAN_CONTACT: Final = "the-test-contact"
WEBHOOK_PATH: Final = "/webhook"
AGREE: Final = agree_payload(CONSENT_NOTICE_VERSION)


def app_settings(database_url: str) -> Settings:
    return Settings.from_environment(
        {
            "TRACKER_DATABASE_URL": database_url,
            "WHATSAPP_APP_SECRET": APP_SECRET,
            "WHATSAPP_VERIFY_TOKEN": VERIFY_TOKEN,
            "WHATSAPP_ACCESS_TOKEN": "test-access-token",
            "WHATSAPP_PHONE_NUMBER_ID": PHONE_NUMBER_ID,
            "BANTUAN_CONTACT": HUMAN_CONTACT,
        }
    )


def signature_for(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def new_bsuid() -> str:
    return f"ID.{uuid.uuid4().hex}"


def new_message_id() -> str:
    return f"wamid.{uuid.uuid4().hex}"


def _contact(bsuid: str, phone: str | None) -> dict[str, Any]:
    contact: dict[str, Any] = {"profile": {"name": "Test Person"}, "user_id": bsuid}
    if phone is not None:
        contact["wa_id"] = phone
    return contact


def _envelope(value: dict[str, Any]) -> dict[str, Any]:
    metadata = {"display_phone_number": "15550000000", "phone_number_id": PHONE_NUMBER_ID}
    change = {"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": metadata, **value}}
    return {"object": "whatsapp_business_account", "entry": [{"id": "200000000000000", "changes": [change]}]}


def _message_fields(bsuid: str, phone: str | None, message_id: str | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "from_user_id": bsuid,
        "id": message_id or new_message_id(),
        "timestamp": str(int(time.time())),
    }
    if phone is not None:
        fields["from"] = phone
    return fields


def text_message(bsuid: str, text: str, phone: str | None = None, message_id: str | None = None) -> dict[str, Any]:
    fields = _message_fields(bsuid, phone, message_id)
    return {"type": "text", "text": {"body": text}, **fields}


def text_update(bsuid: str, text: str, phone: str | None = None, message_id: str | None = None) -> dict[str, Any]:
    return _envelope({"contacts": [_contact(bsuid, phone)], "messages": [text_message(bsuid, text, phone, message_id)]})


def reaction_update(bsuid: str) -> dict[str, Any]:
    reaction = {"message_id": new_message_id(), "emoji": "\N{THUMBS UP SIGN}"}
    message = {"type": "reaction", "reaction": reaction, **_message_fields(bsuid, None, None)}
    return _envelope({"contacts": [_contact(bsuid, None)], "messages": [message]})


def batch_update(*senders_and_texts: tuple[str, str]) -> dict[str, Any]:
    contacts = [_contact(bsuid, None) for bsuid, _ in senders_and_texts]
    messages = [text_message(bsuid, text) for bsuid, text in senders_and_texts]
    return _envelope({"contacts": contacts, "messages": messages})


def button_update(bsuid: str, payload: str, phone: str | None = None, message_id: str | None = None) -> dict[str, Any]:
    fields = _message_fields(bsuid, phone, message_id)
    context = {"from": "15550000000", "id": new_message_id()}
    interactive = {"type": "button_reply", "button_reply": {"id": payload, "title": "Button"}}
    message = {"type": "interactive", "interactive": interactive, "context": context, **fields}
    return _envelope({"contacts": [_contact(bsuid, phone)], "messages": [message]})


def image_update(bsuid: str) -> dict[str, Any]:
    image = {"id": "300000000000000", "mime_type": "image/jpeg", "sha256": "x"}
    message = {"type": "image", "image": image, **_message_fields(bsuid, None, None)}
    return _envelope({"contacts": [_contact(bsuid, None)], "messages": [message]})


def number_change_update(old_bsuid: str, new_bsuid: str) -> dict[str, Any]:
    system = {
        "body": "User changed number",
        "type": "user_changed_user_id",
        "previous_user_id": old_bsuid,
        "user_id": new_bsuid,
    }
    message = {"type": "system", "system": system, "from": "6281200000000", **_message_fields(new_bsuid, None, None)}
    return _envelope({"messages": [message]})


def status_update() -> dict[str, Any]:
    status = {"id": new_message_id(), "status": "delivered", "timestamp": "1", "recipient_id": "6281200000000"}
    return _envelope({"statuses": [status]})


@dataclass
class FakeMeta:
    """Stands in for Meta's API, and keeps what the bot sent."""

    sent: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    is_down: bool = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.is_down:
            return httpx.Response(500, json={"error": {"message": "down", "code": 1}})
        self.sent.append(json.loads(request.content))
        contacts = [{"input": "recipient", "wa_id": "recipient"}]
        reply = {"messaging_product": "whatsapp", "contacts": contacts, "messages": [{"id": new_message_id()}]}
        return httpx.Response(200, json=reply)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def texts(self) -> list[str]:
        return [_sent_text(message) for message in self.sent]

    def payloads(self) -> list[str]:
        """The buttons on the latest reply."""
        action = self.sent[-1].get("interactive", {}).get("action", {})
        return [button["reply"]["id"] for button in action.get("buttons", [])]


def button_titles(message: dict[str, Any]) -> list[str]:
    return [button["reply"]["title"] for button in message["interactive"]["action"]["buttons"]]


def _sent_text(message: dict[str, Any]) -> str:
    if "interactive" in message:
        return message["interactive"]["body"]["text"]
    return message["text"]["body"]
