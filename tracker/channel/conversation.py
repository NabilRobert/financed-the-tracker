"""What the bot does with one message or button press. Knows nothing of WhatsApp.

Each handler returns the Reply to send, or None for silence. Everything happens in one
transaction that commits before the caller sends anything, so a crash can't lose an
entry that a person was told about (ADR-013).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.channel import replies
from tracker.channel.buttons import ButtonAction, ButtonPress, read_payload
from tracker.channel.commands import run_command
from tracker.channel.ratelimit import Admission, MessageLimiter
from tracker.channel.replies import CONSENT_NOTICE_VERSION, Reply
from tracker.ledger.categories import resolve_category
from tracker.ledger.entries import NewEntry, VoidOutcome, claim_message, insert_entry, void_entry
from tracker.ledger.pending import NewPendingEntry, cancel_pending_entry, confirm_pending_entry, create_pending_entry
from tracker.ledger.people import count_unknown_sender, has_consented, record_consent, resolve_sender
from tracker.ledger.scope import PersonScope, person_transaction
from tracker.ledger.summaries import today_in_jakarta
from tracker.parsing.amount import AmbiguityReason
from tracker.parsing.message import AmbiguousEntry, ParsedEntry, parse_message

MAX_UNCONFIRMED_RUPIAH: Final = 10_000_000  # L6.
MAX_READINGS_ON_BUTTONS: Final = 2  # Meta allows three reply buttons, and Batal takes one.


@dataclass(frozen=True)
class Sender:
    """Who a signed webhook says this is (I1). Nothing else may name a person."""

    bsuid: str
    phone: str | None


@dataclass(frozen=True)
class Conversation:
    engine: AsyncEngine
    limiter: MessageLimiter
    human_contact: str

    async def _admit(self, sender: Sender) -> int | Reply | None:
        """The person's id, a reply that ends here, or None for silence."""
        person_id = await resolve_sender(self.engine, sender.bsuid, sender.phone)
        if person_id is None:
            await count_unknown_sender(self.engine)
            return None
        admission = await self.limiter.admit(person_id)
        if admission is Admission.ALLOWED:
            return person_id
        return Reply(replies.RATE_LIMITED) if admission is Admission.REFUSED_WITH_NOTICE else None

    async def handle_text(self, sender: Sender, meta_message_id: str, text: str, sent_at: datetime) -> Reply | None:
        admitted = await self._admit(sender)
        if not isinstance(admitted, int):
            return admitted
        async with person_transaction(self.engine, admitted) as scope:
            if not await has_consented(scope):
                return replies.consent_request()
            inbound_message_id = await claim_message(scope, meta_message_id)
            if inbound_message_id is None:
                return None
            today = today_in_jakarta(sent_at)
            command_reply = await run_command(scope, text, today, self.human_contact)
            if command_reply is not None:
                return command_reply
            return await _log_message(scope, inbound_message_id, text, today)

    async def handle_other_message(self, sender: Sender, meta_message_id: str) -> Reply | None:
        """A photo, sticker or voice note. It's never downloaded, and nothing of it is kept."""
        admitted = await self._admit(sender)
        if not isinstance(admitted, int):
            return admitted
        async with person_transaction(self.engine, admitted) as scope:
            if not await has_consented(scope):
                return replies.consent_request()
            is_new = await claim_message(scope, meta_message_id) is not None
        return Reply(replies.NOT_TEXT) if is_new else None

    async def handle_button(self, sender: Sender, meta_message_id: str, payload: str) -> Reply | None:
        press = read_payload(payload)
        if press is None:
            return None
        admitted = await self._admit(sender)
        if not isinstance(admitted, int):
            return admitted
        async with person_transaction(self.engine, admitted) as scope:
            if press.action in (ButtonAction.AGREE, ButtonAction.DECLINE):
                return await _answer_consent(scope, press)
            if not await has_consented(scope) or await claim_message(scope, meta_message_id) is None:
                return None
            return await _press_button(scope, press)


async def _answer_consent(scope: PersonScope, press: ButtonPress) -> Reply:
    already_agreed = await has_consented(scope)
    if press.action is ButtonAction.DECLINE:
        # An old "Tidak" must not say nothing is stored once something is.
        return Reply(replies.ALREADY_AGREED if already_agreed else replies.CONSENT_DECLINED)
    if press.target_id != CONSENT_NOTICE_VERSION:
        return replies.consent_request()  # They agreed to an older notice: show them this one.
    await record_consent(scope, str(CONSENT_NOTICE_VERSION))
    return Reply(replies.CONSENT_THANKS)


async def _press_button(scope: PersonScope, press: ButtonPress) -> Reply:
    target_id = press.target_id
    assert target_id is not None  # read_payload guarantees it for these actions.
    if press.action is ButtonAction.UNDO:
        return _undo_reply(await void_entry(scope, target_id))
    if press.action is ButtonAction.CANCEL:
        return Reply(replies.CANCELLED if await cancel_pending_entry(scope, target_id) else replies.PENDING_GONE)
    confirmed = await confirm_pending_entry(scope, target_id, press.choice)
    if confirmed is None:
        return Reply(replies.PENDING_GONE)
    return replies.entry_written(
        confirmed.written.entry_id,
        confirmed.direction,
        confirmed.written.category,
        confirmed.amount_rupiah,
        confirmed.written.budget_warning,
    )


def _undo_reply(outcome: VoidOutcome) -> Reply:
    if outcome is VoidOutcome.VOIDED:
        return Reply(replies.UNDONE)
    return Reply(replies.ALREADY_UNDONE if outcome is VoidOutcome.ALREADY_VOIDED else replies.ENTRY_NOT_FOUND)


async def _ask(
    scope: PersonScope,
    inbound_message_id: int,
    entry: ParsedEntry | AmbiguousEntry,
    today: date,
    amounts: tuple[int, ...],
) -> int:
    category = await resolve_category(scope, entry.description, entry.direction, entry.category)
    pending = NewPendingEntry(amounts, entry.direction, category, entry.description, today)
    return await create_pending_entry(scope, inbound_message_id, pending)


async def _log_message(scope: PersonScope, inbound_message_id: int, text: str, today: date) -> Reply:
    parsed = parse_message(text)
    if parsed is None:
        return Reply(replies.NOT_UNDERSTOOD)
    if isinstance(parsed, AmbiguousEntry):
        return await _ask_about(scope, inbound_message_id, parsed, today)
    if parsed.amount_rupiah > MAX_UNCONFIRMED_RUPIAH:
        pending_id = await _ask(scope, inbound_message_id, parsed, today, (parsed.amount_rupiah,))
        return replies.large_amount_question(pending_id, parsed.amount_rupiah)
    category = await resolve_category(scope, parsed.description, parsed.direction, parsed.category)
    entry = NewEntry(parsed.amount_rupiah, parsed.direction, category, parsed.description, today)
    written = await insert_entry(scope, inbound_message_id, entry)
    return replies.entry_written(
        written.entry_id, parsed.direction, written.category, parsed.amount_rupiah, written.budget_warning
    )


async def _ask_about(scope: PersonScope, inbound_message_id: int, entry: AmbiguousEntry, today: date) -> Reply:
    """Offers the readings when a tap can settle it, and explains when only a new message can."""
    reason, candidates = entry.ambiguity.reason, entry.ambiguity.candidates
    can_offer = 0 < len(candidates) <= MAX_READINGS_ON_BUTTONS and reason is not AmbiguityReason.SEVERAL_AMOUNTS
    if not can_offer:
        return replies.refusal(reason)
    pending_id = await _ask(scope, inbound_message_id, entry, today, candidates)
    return replies.question(reason, pending_id, candidates)
