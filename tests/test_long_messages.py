"""L7: a message over WhatsApp's length is refused before anything reads it.

The webhook accepts up to 256 KB, and the sensitive-number check and the text cleaning
each take about half a second on that much. WAHA can't deliver text that long, so this is
defence in depth: the length is checked first, and the rest never runs.
"""

import pytest
from conftest import Bot

from tracker.channel import conversation, replies
from tracker.channel.conversation import answer_without_the_ledger, consent_word
from tracker.ledger.entries import MessageOutcome
from tracker.parsing.amount import MAX_TEXT_CHARS

CARD_NUMBER = "kartu 4111 1111 1111 1111"


def _never_called(_text: str) -> bool:
    raise AssertionError("read a message over the length cap")


def test_a_message_over_the_cap_is_refused_without_the_sensitive_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation, "looks_sensitive", _never_called)
    answer = answer_without_the_ledger("a" * (MAX_TEXT_CHARS + 1))
    assert answer == conversation.Answer(replies.NOT_UNDERSTOOD, MessageOutcome.UNPARSED)


def test_a_message_at_the_cap_still_gets_the_sensitive_check() -> None:
    at_the_cap = CARD_NUMBER + " " * (MAX_TEXT_CHARS - len(CARD_NUMBER))
    answer = answer_without_the_ledger(at_the_cap)
    assert answer == conversation.Answer(replies.SENSITIVE_NUMBER, MessageOutcome.REFUSED)


def test_a_short_ordinary_message_goes_on_to_the_ledger() -> None:
    assert answer_without_the_ledger("makan 25rb") is None


def test_a_consent_answer_over_the_cap_is_never_cleaned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation, "clean_text", _never_called)
    assert consent_word("setuju" + " " * MAX_TEXT_CHARS) == ""


def test_a_consent_answer_is_read_as_its_word() -> None:
    assert consent_word("  Setuju ") == "setuju"
    assert consent_word(None) == ""


async def test_a_long_message_is_answered_as_not_understood(bot: Bot, member: str) -> None:
    await bot.say(member, CARD_NUMBER + " x" * MAX_TEXT_CHARS)
    assert bot.waha.texts == [replies.NOT_UNDERSTOOD]
