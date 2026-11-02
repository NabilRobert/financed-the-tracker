"""What the bot does with one message. Knows nothing of WhatsApp or WAHA.

Each handler returns the text to send, or None for silence. Everything happens in one
transaction that commits before the caller sends anything, so a crash can't lose an
entry that a person was told about (ADR-013).

Reply buttons don't work on WAHA, so answers are typed. They carry no id: `ya`, `1`, `2`
and `batal` can only reach the sender's own newest question or newest entry, which is
why a new entry closes any open question, and `batal` never reaches past the newest.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.channel import replies
from tracker.channel.commands import run_command
from tracker.channel.gateway import IncomingMessage, Sender
from tracker.channel.ratelimit import Admission, MessageLimiter
from tracker.channel.replies import CONSENT_NOTICE, CONSENT_NOTICE_VERSION
from tracker.ledger.categories import resolve_category
from tracker.ledger.entries import NewEntry, VoidOutcome, claim_message, insert_entry, void_entry
from tracker.ledger.pending import (
    NewPendingEntry,
    OpenQuestion,
    cancel_pending_entry,
    close_open_questions,
    confirm_pending_entry,
    create_pending_entry,
    find_open_question,
)
from tracker.ledger.people import (
    count_unknown_sender,
    has_consented,
    newest_entry,
    notice_sent_version,
    record_consent,
    record_notice_sent,
    resolve_sender,
)
from tracker.ledger.scope import PersonScope, person_transaction
from tracker.ledger.summaries import today_in_jakarta
from tracker.parsing.amount import AmbiguityReason, clean_text
from tracker.parsing.message import AmbiguousEntry, ParsedEntry, parse_message

MAX_UNCONFIRMED_RUPIAH: Final = 10_000_000  # L6.
MAX_READINGS_TO_OFFER: Final = 2  # More than this can't be answered with one word.
AGREE_WORD: Final = "setuju"
DECLINE_WORD: Final = "tidak"
YES_WORD: Final = "ya"
CANCEL_WORD: Final = "batal"
CHOICE_WORDS: Final[dict[str, int]] = {"1": 0, "2": 1}


@dataclass(frozen=True)
class Conversation:
    engine: AsyncEngine
    limiter: MessageLimiter
    human_contact: str

    async def _admit(self, sender: Sender) -> int | str | None:
        """The person's id, a reply that ends here, or None for silence."""
        person_id = await resolve_sender(self.engine, sender.whatsapp_id, sender.phone)
        if person_id is None:
            await count_unknown_sender(self.engine)
            return None
        admission = await self.limiter.admit(person_id)
        if admission is Admission.ALLOWED:
            return person_id
        return replies.RATE_LIMITED if admission is Admission.REFUSED_WITH_NOTICE else None

    async def handle(self, sender: Sender, message: IncomingMessage) -> str | None:
        admitted = await self._admit(sender)
        if not isinstance(admitted, int):
            return admitted
        async with person_transaction(self.engine, admitted) as scope:
            if not await has_consented(scope):
                return await _ask_for_consent(scope, message.text)
            inbound_message_id = await claim_message(scope, message.message_id)
            if inbound_message_id is None:
                return None
            if message.text is None:
                return replies.NOT_TEXT
            return await self._answer(scope, inbound_message_id, message.text, today_in_jakarta(message.sent_at))

    async def _answer(self, scope: PersonScope, inbound_message_id: int, text: str, today: date) -> str:
        word = clean_text(text)
        question = await find_open_question(scope)
        if question is not None and word in (YES_WORD, CANCEL_WORD, *CHOICE_WORDS):
            return await _answer_question(scope, question, word)
        if word == CANCEL_WORD:
            return await _undo_newest(scope)
        command_reply = await run_command(scope, text, today, self.human_contact)
        if command_reply is not None:
            return command_reply
        await close_open_questions(scope)  # A late `ya` must not answer a question from before this.
        return await _log_message(scope, inbound_message_id, text, today)


async def _ask_for_consent(scope: PersonScope, text: str | None) -> str:
    """I4: stores nothing about the message. `setuju` counts only for a notice this person was sent."""
    word = clean_text(text or "")
    was_sent_this_notice = await notice_sent_version(scope) == CONSENT_NOTICE_VERSION
    if was_sent_this_notice and word == AGREE_WORD:
        await record_consent(scope, CONSENT_NOTICE_VERSION)
        return replies.CONSENT_THANKS
    if was_sent_this_notice and word == DECLINE_WORD:
        return replies.CONSENT_DECLINED
    await record_notice_sent(scope, CONSENT_NOTICE_VERSION)
    return CONSENT_NOTICE


async def _answer_question(scope: PersonScope, question: OpenQuestion, word: str) -> str:
    if word == CANCEL_WORD:
        await cancel_pending_entry(scope, question.pending_id)
        return replies.CANCELLED
    if question.has_two_readings and word == YES_WORD:
        return replies.ANSWER_ONE_OR_TWO
    choice = CHOICE_WORDS.get(word, 0)
    if choice == 1 and not question.has_two_readings:
        return replies.ANSWER_YES_OR_CANCEL
    confirmed = await confirm_pending_entry(scope, question.pending_id, choice)
    if confirmed is None:  # It expired between finding it and answering.
        return replies.QUESTION_EXPIRED
    written = confirmed.written
    return replies.entry_written(confirmed.direction, written.category, confirmed.amount_rupiah, written.budget_warning)


async def _undo_newest(scope: PersonScope) -> str:
    """Undoes the newest entry only, so repeating `batal` can't reach an older one (L4)."""
    entry = await newest_entry(scope)
    if entry is None:
        return replies.NOTHING_TO_UNDO
    if await void_entry(scope, entry.entry_id) is VoidOutcome.VOIDED:
        return replies.entry_undone(entry.category, entry.amount_rupiah)
    return replies.ALREADY_UNDONE


async def _ask(
    scope: PersonScope,
    inbound_message_id: int,
    entry: ParsedEntry | AmbiguousEntry,
    today: date,
    amounts: tuple[int, ...],
) -> None:
    category = await resolve_category(scope, entry.description, entry.direction, entry.category)
    pending = NewPendingEntry(amounts, entry.direction, category, entry.description, today)
    await create_pending_entry(scope, inbound_message_id, pending)


async def _log_message(scope: PersonScope, inbound_message_id: int, text: str, today: date) -> str:
    parsed = parse_message(text)
    if parsed is None:
        return replies.NOT_UNDERSTOOD
    if isinstance(parsed, AmbiguousEntry):
        return await _ask_about(scope, inbound_message_id, parsed, today)
    if parsed.amount_rupiah > MAX_UNCONFIRMED_RUPIAH:
        await _ask(scope, inbound_message_id, parsed, today, (parsed.amount_rupiah,))
        return replies.large_amount_question(parsed.amount_rupiah)
    category = await resolve_category(scope, parsed.description, parsed.direction, parsed.category)
    entry = NewEntry(parsed.amount_rupiah, parsed.direction, category, parsed.description, today)
    written = await insert_entry(scope, inbound_message_id, entry)
    return replies.entry_written(parsed.direction, written.category, parsed.amount_rupiah, written.budget_warning)


async def _ask_about(scope: PersonScope, inbound_message_id: int, entry: AmbiguousEntry, today: date) -> str:
    """Offers the readings when one word can settle it, and explains when only a new message can."""
    reason, candidates = entry.ambiguity.reason, entry.ambiguity.candidates
    can_offer = 0 < len(candidates) <= MAX_READINGS_TO_OFFER and reason is not AmbiguityReason.SEVERAL_AMOUNTS
    if not can_offer:
        return replies.refusal(reason)
    await _ask(scope, inbound_message_id, entry, today, candidates)
    return replies.question(reason, candidates)
