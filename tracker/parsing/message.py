"""Turns one WhatsApp message into a sure entry, a question, or nothing.

amount.py reads the numbers. This module decides which number is the money and
whether the message as a whole is safe to write. Every doubt becomes an
AmbiguousEntry, because asking costs one tap and a wrong amount costs the
person's trust in every total after it (P3).

Several lines in one message (P5) and dates (P6) are Should items and aren't
built: several amounts ask, and an entry's date is left to the caller.
"""

import re
import string
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from tracker.parsing.amount import (
    MAX_TEXT_CHARS,
    SPELLED_AMOUNTS,
    AmbiguityReason,
    Ambiguous,
    FoundAmount,
    clean_text,
    find_amounts,
    has_foreign_digits,
)
from tracker.parsing.keywords import (
    COMMAND_WORDS,
    COUNTING_VERBS,
    DEFAULT_SPENDING_CATEGORY,
    FOREIGN_CURRENCY_PATTERN,
    MEASURE_WORDS,
    NOT_A_TRANSACTION_PATTERN,
    NUMBER_LABEL_WORDS,
    NUMBER_WORDS,
    OTHER_INCOME_CATEGORY,
    OTHER_INCOME_WORDS,
    PER_ITEM_PATTERN,
    SALARY_CATEGORY,
    SALARY_WORDS,
    SPENDING_KEYWORDS,
    SPENDING_VERBS,
)

MAX_DESCRIPTION_CHARS: Final = 80  # PR1: the entry keeps a short note, not the message.

WORD_PATTERN: Final = re.compile("[a-z]+")
_GLUED_TIMES_PATTERN: Final = re.compile(r"(?<=[0-9])[x\u00d7](?=[0-9])")
_FOREIGN_CURRENCY: Final = re.compile(FOREIGN_CURRENCY_PATTERN)
_PER_ITEM: Final = re.compile(PER_ITEM_PATTERN)
_NOT_A_TRANSACTION: Final = re.compile(NOT_A_TRANSACTION_PATTERN)
_DESCRIPTION_EDGES: Final = string.punctuation + " "


class Direction(StrEnum):
    SPENDING = "spending"
    INCOME = "income"


@dataclass(frozen=True)
class ParsedEntry:
    """Sure enough to write. `amount_rupiah` is above 0 and within MAX_ENTRY_RUPIAH."""

    amount_rupiah: int
    direction: Direction
    category: str
    description: str


@dataclass(frozen=True)
class AmbiguousEntry:
    """Must not be written until the person answers. Everything but the amount is known."""

    ambiguity: Ambiguous
    direction: Direction
    category: str
    description: str


def _word_next_to(amount: FoundAmount, cleaned: str) -> tuple[str, str]:
    """The tokens either side of an amount, without punctuation; empty at the text's edge."""
    words_before = cleaned[: amount.start].split()
    words_after = cleaned[amount.end :].split()
    word_before = words_before[-1].strip(string.punctuation) if words_before else ""
    word_after = words_after[0].strip(string.punctuation) if words_after else ""
    return word_before, word_after


def _is_clear_amount(amount: FoundAmount) -> bool:
    return not amount.is_bare or isinstance(amount.value, int)


def _is_item_count(amount: FoundAmount, cleaned: str) -> bool:
    """True for the 2 in "beli 2 kopi 50rb": small, leading, and followed by the item."""
    if isinstance(amount.value, int):
        return False
    is_small_number = amount.value.reason is AmbiguityReason.BELOW_MIN_UNITLESS
    word_before, word_after = _word_next_to(amount, cleaned)
    is_leading = not word_before or word_before in COUNTING_VERBS
    is_before_item = word_after.isalpha() and word_after not in SPELLED_AMOUNTS
    return is_small_number and is_leading and is_before_item


def _is_labelled_number(amount: FoundAmount, cleaned: str) -> bool:
    """True for numbers that say what they count: "tgl 25", "5 kg"."""
    word_before, word_after = _word_next_to(amount, cleaned)
    return word_before in NUMBER_LABEL_WORDS or word_after in MEASURE_WORDS


def _amounts_that_could_be_money(cleaned: str) -> list[FoundAmount]:
    found = find_amounts(cleaned)
    has_clear_amount = any(_is_clear_amount(amount) for amount in found)
    money: list[FoundAmount] = []
    for amount in found:
        is_not_money = amount.is_bare and (
            _is_labelled_number(amount, cleaned) or (has_clear_amount and _is_item_count(amount, cleaned))
        )
        if not is_not_money:
            money.append(amount)
    return money


def _candidates_of(amounts: list[FoundAmount]) -> tuple[int, ...]:
    candidates: list[int] = []
    for amount in amounts:
        readings = (amount.value,) if isinstance(amount.value, int) else amount.value.candidates
        candidates.extend(readings)
    return tuple(candidates)


def _touches_number_word(amount: FoundAmount, cleaned: str) -> bool:
    """True for "seribu lima ratus": the amount is only part of a spelled-out number."""
    word_before, word_after = _word_next_to(amount, cleaned)
    return word_before in NUMBER_WORDS or word_after in NUMBER_WORDS


def _judge_money(cleaned: str, money: list[FoundAmount]) -> int | Ambiguous:
    """Settles the amount of a message with at least one candidate, or says why it can't."""
    if _FOREIGN_CURRENCY.search(cleaned):
        return Ambiguous(AmbiguityReason.FOREIGN_CURRENCY)
    if _PER_ITEM.search(cleaned):
        return Ambiguous(AmbiguityReason.PER_ITEM_PRICE)
    if len(money) > 1:
        return Ambiguous(AmbiguityReason.SEVERAL_AMOUNTS, _candidates_of(money))
    only_amount = money[0]
    if _touches_number_word(only_amount, cleaned):
        return Ambiguous(AmbiguityReason.SPELLED_NUMBER, _candidates_of(money))
    if isinstance(only_amount.value, int) and _NOT_A_TRANSACTION.search(cleaned):
        return Ambiguous(AmbiguityReason.NOT_A_TRANSACTION, (only_amount.value,))
    return only_amount.value


def _read_direction(words: list[str], cleaned: str) -> Direction:
    """P4: income by keyword, spending otherwise. The reply always says which it was."""
    has_spending_verb = not SPENDING_VERBS.isdisjoint(words)
    has_income_word = not (SALARY_WORDS.isdisjoint(words) and OTHER_INCOME_WORDS.isdisjoint(words))
    if has_income_word and not has_spending_verb and "terima kasih" not in cleaned:
        return Direction.INCOME
    return Direction.SPENDING


def _read_category(words: list[str], direction: Direction) -> str:
    """C2: the first word with a keyword decides; no match means Lainnya."""
    if direction is Direction.INCOME:
        return OTHER_INCOME_CATEGORY if SALARY_WORDS.isdisjoint(words) else SALARY_CATEGORY
    for word in words:
        for category, keywords in SPENDING_KEYWORDS.items():
            if word in keywords:
                return category
    return DEFAULT_SPENDING_CATEGORY


def _describe(cleaned: str, money: list[FoundAmount]) -> str:
    """The message without its amounts, which is all the entry keeps of the text (PR1)."""
    pieces: list[str] = []
    position = 0
    for amount in money:
        pieces.append(cleaned[position : amount.start])
        position = amount.end
    pieces.append(cleaned[position:])
    description = " ".join(" ".join(pieces).split()).strip(_DESCRIPTION_EDGES)
    return description[:MAX_DESCRIPTION_CHARS]


def parse_message(text: str) -> ParsedEntry | AmbiguousEntry | None:
    """Reads a message. None means the rules found nothing to log."""
    cleaned = clean_text(text)
    if not cleaned or len(cleaned) > MAX_TEXT_CHARS or cleaned.split()[0] in COMMAND_WORDS:
        return None
    cleaned = _GLUED_TIMES_PATTERN.sub(" x ", cleaned)
    money = _amounts_that_could_be_money(cleaned)
    if has_foreign_digits(cleaned):
        verdict = Ambiguous(AmbiguityReason.FOREIGN_DIGITS)
    elif money:
        verdict = _judge_money(cleaned, money)
    else:
        return None
    description = _describe(cleaned, money)
    words = WORD_PATTERN.findall(description)
    direction = _read_direction(words, cleaned)
    category = _read_category(words, direction)
    if isinstance(verdict, Ambiguous):
        return AmbiguousEntry(verdict, direction, category, description)
    return ParsedEntry(verdict, direction, category, description)
