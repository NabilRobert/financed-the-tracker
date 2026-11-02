"""The one interface between the bot and whatever carries its messages.

Everything WhatsApp-specific sits behind this, so WAHA can be replaced without touching
consent, the ledger or the commands. Today waha.py is the only implementation. If
WhatsApp's official API returns, it becomes a second one (ADR-022).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class SignatureCheck(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class Sender:
    """Who a signed webhook says this is (I1). Nothing else may name a person."""

    whatsapp_id: str
    phone: str | None
    reply_to: str  # The address it came from, which is the one known to work.


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    chat_id: str
    text: str | None  # None for a photo, sticker or voice note, which is never downloaded.
    sent_at: datetime


@dataclass(frozen=True)
class ChannelStopped:
    """The account stopped working: perhaps a ban, perhaps a protocol change (T43)."""

    status: str


class MalformedEventError(Exception):
    """A correctly signed event this code can't read."""


class Gateway(Protocol):
    def check_signature(self, body: bytes, headers: Mapping[str, str]) -> SignatureCheck: ...

    def read_event(self, event: Any) -> IncomingMessage | ChannelStopped | None:
        """What an event means, or None for one the bot ignores."""
        ...

    async def identify(self, chat_id: str) -> Sender: ...

    async def send_text(self, to: str, text: str) -> None: ...
