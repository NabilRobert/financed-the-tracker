"""Reads one Indonesian money amount from text into whole rupiah.

parse_amount answers for a text that should be exactly one amount, and
find_amounts locates amounts inside a message for message.py. Both run on the
same pattern, so anything that later passes a model's `amount_text` through
parse_amount (ADR-007) meets the rules that read a person's own message.

Money never passes through a float: digits go from the text into a Decimal and
come out as an int. When a text has two honest readings, the result is
Ambiguous, and the caller asks the person. Nothing here guesses (P3).
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

MIN_UNITLESS_RUPIAH: Final = 1_000  # P3: "kopi 25" could be Rp25 or Rp25.000.
MAX_ENTRY_RUPIAH: Final = 1_000_000_000  # L2.
MAX_TEXT_CHARS: Final = 4_096  # WhatsApp's limit on a text message; longer text isn't a real message.
# Longer numbers are over the limit whatever they say. Stopping here also keeps
# every Decimal product exact under the default 28-digit context.
MAX_NUMBER_CHARS: Final = 20
THOUSAND: Final = 1_000
THREE_DIGITS: Final = 3  # A thousands group, and a remainder such as the 500 in 2jt500.
TWO_DIGITS: Final = 2  # Cents, as in 25.000,00.

UNIT_MULTIPLIERS: Final[dict[str, int]] = {
    "rb": 1_000,
    "ribu": 1_000,
    "rebu": 1_000,
    "k": 1_000,
    "jt": 1_000_000,
    "juta": 1_000_000,
    "miliar": 1_000_000_000,
    "milyar": 1_000_000_000,
}
MILLION_OR_BILLION_UNIT: Final = "m"  # Miliar to an Indonesian reader, million in English.
SIGN_CHARACTERS: Final = "-\u2212\u2013+"  # Hyphen, minus and en dash, which phones swap, and plus.

SPELLED_AMOUNTS: Final[dict[str, int]] = {
    "seribu": 1_000,
    "sejuta": 1_000_000,
    # Jakarta slang from Hokkien numbers. P1 lists goceng, ceban and gopek, and cepek below.
    "gopek": 500,
    "seceng": 1_000,
    "ceceng": 1_000,
    "noceng": 2_000,
    "goceng": 5_000,
    "ceban": 10_000,
    "goban": 50_000,
}

# Words whose meaning depends on who's speaking: some mean Rp100,000 by cepek.
AMBIGUOUS_SPELLED_AMOUNTS: Final[dict[str, tuple[int, ...]]] = {"cepek": (100, 100_000)}
SPELLED_WORDS: Final = frozenset(SPELLED_AMOUNTS) | frozenset(AMBIGUOUS_SPELLED_AMOUNTS)


class AmbiguityReason(StrEnum):
    # From reading one amount.
    BELOW_MIN_UNITLESS = "below_min_unitless"
    UNCLEAR_SEPARATORS = "unclear_separators"
    UNCLEAR_FORM = "unclear_form"
    UNCLEAR_UNIT = "unclear_unit"
    UNCLEAR_SLANG = "unclear_slang"
    NOT_WHOLE = "not_whole"
    NOT_POSITIVE = "not_positive"
    SIGNED_AMOUNT = "signed_amount"
    OVER_LIMIT = "over_limit"
    FOREIGN_DIGITS = "foreign_digits"
    # From reading a whole message.
    SEVERAL_AMOUNTS = "several_amounts"
    PER_ITEM_PRICE = "per_item_price"
    SPELLED_NUMBER = "spelled_number"
    FOREIGN_CURRENCY = "foreign_currency"
    NOT_A_TRANSACTION = "not_a_transaction"


@dataclass(frozen=True)
class Ambiguous:
    """Not safe to write as it stands.

    `candidates` holds the readings a person could confirm. Empty means a
    refusal: there is nothing to offer, so the bot explains instead.
    """

    reason: AmbiguityReason
    candidates: tuple[int, ...] = ()


@dataclass(frozen=True)
class FoundAmount:
    """One amount inside a cleaned text, with where it sits."""

    value: int | Ambiguous
    start: int
    end: int
    is_bare: bool  # Digits alone: no unit, no currency mark.


_UNIT = "|".join([*sorted(UNIT_MULTIPLIERS, key=len, reverse=True), MILLION_OR_BILLION_UNIT])
_SPELLED = "|".join(sorted(SPELLED_WORDS))
# All of a number or none of it: no backing off "1.500" to "1", and no unit left
# behind a stray separator as in "1.000.rb".
_NUMBER = rf"(?!0[0-9])[0-9]+(?:[.,][0-9]+)*(?![.,]?[0-9])(?![.,][ ]?(?:{_UNIT})(?![a-z]))"

# Whitespace is a single plain space by now, because clean_text ran first.
_AMOUNT_PATTERN = re.compile(
    rf"""
    (?<![a-z0-9])(?<![0-9][.,])         # not the tail of a word or of a longer number
    (?<![ ][.,])(?<!^[.,])              # ".5rb" has lost a digit, so it isn't 5rb
    (?P<outer_sign>[{SIGN_CHARACTERS}]+)?    # "kopi-25rb" never gets here: the first check stops it
    (?:
        (?P<spelled>{_SPELLED})
      |
        (?:
            (?P<currency>(?:rp|idr)\.?[ ]?)
            (?P<inner_sign>[{SIGN_CHARACTERS}][ ]?)?
        )?
        (?P<number>{_NUMBER})
        (?:
            [ ]?(?P<unit>{_UNIT})
            (?:
                [ ]?(?P<lower_number>{_NUMBER})[ ]?(?P<lower_unit>{_UNIT})
              |
                (?P<remainder>[0-9]+)(?![.,]?[0-9])
            )?
        )?
        (?:[.,]-)?                      # the ",-" of a price tag
        (?:[ ]?(?P<suffix>rupiah|perak))?
    )
    (?![a-z0-9])                        # not the head of a word
    """,
    re.VERBOSE,
)


def clean_text(text: str) -> str:
    """Lower-cases, drops invisible characters and turns any whitespace run into one space."""
    visible_characters = [character for character in text if unicodedata.category(character) != "Cf"]
    return " ".join("".join(visible_characters).lower().split())


def has_foreign_digits(cleaned: str) -> bool:
    """True for digits outside 0-9, such as Arabic-Indic or full-width ones.

    They are refused rather than converted: NFKC would turn "25²" into 252, and
    the rule "type 0 to 9" is one a person can act on.
    """
    return any(character.isnumeric() and not character.isascii() for character in cleaned)


def _is_grouped(groups: list[str]) -> bool:
    """True for thousands groups as in 1.500.000."""
    first_group, *other_groups = groups
    first_group_fits = len(first_group) <= THREE_DIGITS and not first_group.startswith("0")
    return first_group_fits and all(len(group) == THREE_DIGITS for group in other_groups)


def _read_mixed_separators(groups: list[str], separators: list[str]) -> list[Decimal]:
    """Reads 1.500,50 or 1,500.50: the last separator divides, the others group."""
    grouping_separators = separators[:-1]
    if separators[-1] in grouping_separators or not _is_grouped(groups[:-1]):
        return []
    whole = "".join(groups[:-1])
    fraction = groups[-1]
    if len(fraction) == THREE_DIGITS:
        return [Decimal(f"{whole}.{fraction}"), Decimal(whole + fraction)]
    if len(fraction) <= TWO_DIGITS:
        return [Decimal(f"{whole}.{fraction}")]
    return []


def _read_one_separator(whole: str, fraction: str, has_unit: bool) -> list[Decimal]:
    """Reads a.b, where a unit decides how much can be known: 1,5jt divides, 1.500 groups."""
    as_decimal = Decimal(f"{whole}.{fraction}")
    is_grouping = _is_grouped([whole, fraction])
    if has_unit:
        return [as_decimal, Decimal(whole + fraction)] if is_grouping else [as_decimal]
    if is_grouping:
        return [Decimal(whole + fraction)]
    return [as_decimal] if len(fraction) == TWO_DIGITS else []


def _number_readings(number: str, has_unit: bool) -> list[Decimal]:
    """Every honest reading of a written number: none means it fits no form, two mean ask."""
    groups = re.split("[.,]", number)
    separators = re.findall("[.,]", number)
    if not separators:
        return [Decimal(number)]
    if len(set(separators)) > 1:
        return _read_mixed_separators(groups, separators)
    if len(groups) > TWO_DIGITS:
        return [Decimal("".join(groups))] if _is_grouped(groups) else []
    return _read_one_separator(groups[0], groups[1], has_unit)


def _ambiguous(reason: AmbiguityReason, *readings: Decimal) -> Ambiguous:
    """Keeps only the readings a person could actually confirm."""
    candidates: list[int] = []
    for reading in readings:
        is_valid = reading == reading.to_integral_value() and 0 < reading <= MAX_ENTRY_RUPIAH
        if is_valid:
            candidates.append(int(reading))
    return Ambiguous(reason, tuple(candidates))


def _checked_rupiah(value: Decimal, is_bare: bool) -> int | Ambiguous:
    if value != value.to_integral_value():
        return Ambiguous(AmbiguityReason.NOT_WHOLE)
    if value <= 0:
        return Ambiguous(AmbiguityReason.NOT_POSITIVE)
    if value > MAX_ENTRY_RUPIAH:
        return Ambiguous(AmbiguityReason.OVER_LIMIT)
    if is_bare and value < MIN_UNITLESS_RUPIAH:
        return _ambiguous(AmbiguityReason.BELOW_MIN_UNITLESS, value, value * THOUSAND)
    return int(value)


def _read_remainder(remainder: str, multiplier: int, base: Decimal) -> Decimal | Ambiguous:
    """Reads the 500 in 2jt500. Only three digits are sure: 2jt5 might be 2,5jt or a slip."""
    if len(remainder) == THREE_DIGITS:
        return Decimal(remainder) * multiplier / THOUSAND
    if len(remainder) > THREE_DIGITS:
        return Ambiguous(AmbiguityReason.UNCLEAR_FORM)
    return _ambiguous(AmbiguityReason.UNCLEAR_FORM, base + Decimal(f"0.{remainder}") * multiplier)


def _read_lower_part(match: re.Match[str], multiplier: int, base: Decimal) -> Decimal | Ambiguous:
    """Reads what follows the main unit: the 500rb in 2jt500rb, or the 500 in 2jt500."""
    remainder = match["remainder"]
    if remainder is not None:
        return _read_remainder(remainder, multiplier, base)
    lower_number = match["lower_number"]
    if lower_number is None:
        return Decimal(0)
    lower_multiplier = UNIT_MULTIPLIERS.get(match["lower_unit"], 0)
    readings = _number_readings(lower_number, has_unit=True)
    lower_value = readings[0] * lower_multiplier if len(readings) == 1 else None
    if lower_value is None or not 0 < lower_value < multiplier:
        return Ambiguous(AmbiguityReason.UNCLEAR_FORM)
    return lower_value


def _read_digits(match: re.Match[str], is_bare: bool) -> int | Ambiguous:
    """Reads a written number with its unit, once the sign and size checks have passed."""
    unit = match["unit"]
    multiplier = UNIT_MULTIPLIERS.get(unit, 1)
    readings = _number_readings(match["number"], has_unit=unit is not None)
    if not readings:
        return Ambiguous(AmbiguityReason.UNCLEAR_SEPARATORS)
    if unit == MILLION_OR_BILLION_UNIT:
        million, billion = UNIT_MULTIPLIERS["jt"], UNIT_MULTIPLIERS["miliar"]
        return _ambiguous(AmbiguityReason.UNCLEAR_UNIT, readings[0] * million, readings[0] * billion)
    if len(readings) > 1:
        return _ambiguous(AmbiguityReason.UNCLEAR_SEPARATORS, *(reading * multiplier for reading in readings))
    base = readings[0] * multiplier
    lower_part = _read_lower_part(match, multiplier, base)
    if isinstance(lower_part, Ambiguous):
        return lower_part
    return _checked_rupiah(base + lower_part, is_bare)


def _read_match(match: re.Match[str], is_bare: bool) -> int | Ambiguous:
    if match["outer_sign"] or match["inner_sign"]:
        return Ambiguous(AmbiguityReason.SIGNED_AMOUNT)  # L2: the direction is never a sign.
    spelled = match["spelled"]
    if spelled in AMBIGUOUS_SPELLED_AMOUNTS:
        return Ambiguous(AmbiguityReason.UNCLEAR_SLANG, AMBIGUOUS_SPELLED_AMOUNTS[spelled])
    if spelled is not None:
        return SPELLED_AMOUNTS[spelled]
    if len(match["number"]) > MAX_NUMBER_CHARS:
        return Ambiguous(AmbiguityReason.OVER_LIMIT)
    return _read_digits(match, is_bare)


def find_amounts(cleaned: str) -> list[FoundAmount]:
    """Finds every amount in a text that clean_text has already been through."""
    amounts: list[FoundAmount] = []
    for match in _AMOUNT_PATTERN.finditer(cleaned):
        has_mark = match["spelled"] or match["unit"] or match["currency"] or match["suffix"]
        is_bare = not has_mark
        amounts.append(FoundAmount(_read_match(match, is_bare), match.start(), match.end(), is_bare))
    return amounts


def parse_amount(text: str) -> int | Ambiguous | None:
    """Reads text that should be exactly one amount.

    Returns whole rupiah if sure, Ambiguous if a person must decide, and None
    if the text is not one amount: "makan 25rb" is None here, because finding
    an amount inside a sentence is parse_message's job.
    """
    cleaned = clean_text(text)
    if not cleaned or len(cleaned) > MAX_TEXT_CHARS:
        return None
    if has_foreign_digits(cleaned):
        return Ambiguous(AmbiguityReason.FOREIGN_DIGITS)
    amounts = find_amounts(cleaned)
    if len(amounts) != 1:
        return None
    only_amount = amounts[0]
    if only_amount.start != 0 or only_amount.end != len(cleaned.rstrip(".,!")):
        return None
    return only_amount.value
