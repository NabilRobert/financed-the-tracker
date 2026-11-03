"""Reads Indonesian number words, such as "satu juta dua ratus ribu", into rupiah.

amount.py finds a run of number words with SPELLED_NUMBER_PATTERN and hands it
to spelled_number_readings. The run counts as money only with a scale word
(ribu, juta, miliar) or a se- form (seribu, sejuta): "lima puluh" alone could
be Rp50 or Rp50.000, and "dua" is far more often a count than a price.

The readings follow amount.py's rule for written numbers: none means the words
fit no form, one is sure, and two mean the person is asked which they meant.
Everything goes through Decimal, so "setengah juta" never touches a float.
"""

from decimal import Decimal
from typing import Final

DIGIT_WORDS: Final[dict[str, int]] = {
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
}
SE_WORDS: Final[dict[str, int]] = {"sepuluh": 10, "sebelas": 11}
SCALE_WORDS: Final[dict[str, int]] = {
    "ribu": 1_000,
    "rebu": 1_000,
    "rb": 1_000,
    "juta": 1_000_000,
    "jt": 1_000_000,
    "miliar": 1_000_000_000,
    "milyar": 1_000_000_000,
}
SE_SCALE_WORDS: Final[dict[str, int]] = {
    "seribu": 1_000,
    "sejuta": 1_000_000,
    "semiliar": 1_000_000_000,
    "semilyar": 1_000_000_000,
}
HUNDRED_WORD: Final = "seratus"
HUNDREDS_WORD: Final = "ratus"
TENS_WORD: Final = "puluh"
TEENS_WORD: Final = "belas"
HALF_WORD: Final = "setengah"
HUNDRED: Final = 100
TEN: Final = 10
THOUSAND: Final = 1_000
HALF: Final = Decimal("0.5")

_WORDS_BEFORE_A_SCALE: Final = frozenset(
    [*DIGIT_WORDS, *SE_WORDS, HUNDRED_WORD, HUNDREDS_WORD, TENS_WORD, TEENS_WORD, HALF_WORD]
)
NUMBER_VOCABULARY: Final = _WORDS_BEFORE_A_SCALE | frozenset(SCALE_WORDS) | frozenset(SE_SCALE_WORDS)


def _any_word(words: frozenset[str] | dict[str, int]) -> str:
    return f"(?:{'|'.join(sorted(words))})"


# A se- scale word, or number words that reach a scale word, then any number
# words after it. The spaces between words, and amount.py's check that the
# match isn't followed by a letter, keep each word whole: "duabelas" isn't
# "dua". Whatever the run holds, spelled_number_readings judges it, so
# "lima lima ribu" is found here and refused there rather than half read.
SPELLED_NUMBER_PATTERN: Final = (
    rf"(?:{_any_word(SE_SCALE_WORDS)}|(?:{_any_word(_WORDS_BEFORE_A_SCALE)}[ ])+{_any_word(SCALE_WORDS)})"
    rf"(?:[ ]{_any_word(NUMBER_VOCABULARY)})*"
)


def _word_at(words: list[str], index: int) -> str:
    return words[index] if index < len(words) else ""


def _read_tens(words: list[str], index: int) -> tuple[int, int]:
    """Reads 1 to 99 ("dua puluh lima", "lima belas", "sebelas"). Returns the value and the next index."""
    word = _word_at(words, index)
    if word in SE_WORDS:
        return SE_WORDS[word], index + 1
    if word not in DIGIT_WORDS:
        return 0, index
    digit = DIGIT_WORDS[word]
    next_word = _word_at(words, index + 1)
    if next_word == TEENS_WORD:
        return TEN + digit, index + 2
    if next_word != TENS_WORD:
        return digit, index + 1
    ones = DIGIT_WORDS.get(_word_at(words, index + 2), 0)
    return digit * TEN + ones, index + (3 if ones else 2)


def _read_below_thousand(words: list[str], index: int) -> tuple[int, int, bool]:
    """Reads 1 to 999. The flag says whether it had hundreds, which makes a tail sure."""
    hundreds = 0
    if _word_at(words, index) == HUNDRED_WORD:
        hundreds, index = HUNDRED, index + 1
    elif _word_at(words, index) in DIGIT_WORDS and _word_at(words, index + 1) == HUNDREDS_WORD:
        hundreds, index = DIGIT_WORDS[words[index]] * HUNDRED, index + 2
    tens, index = _read_tens(words, index)
    return hundreds + tens, index, hundreds > 0


def _read_multiplier(words: list[str], index: int) -> tuple[Decimal | None, int]:
    """Reads what stands before a scale word: "dua puluh", "setengah", "satu setengah"."""
    if _word_at(words, index) == HALF_WORD:
        return HALF, index + 1
    value, next_index, _ = _read_below_thousand(words, index)
    if value == 0:
        return None, index
    is_digit_and_a_half = next_index == index + 1 and words[index] in DIGIT_WORDS
    if is_digit_and_a_half and _word_at(words, next_index) == HALF_WORD:
        return value + HALF, next_index + 1
    return Decimal(value), next_index


def _read_group(words: list[str], index: int) -> tuple[Decimal, int, int] | None:
    """Reads one "multiplier scale" group. Returns its value, its scale and the next index."""
    word = _word_at(words, index)
    if word in SE_SCALE_WORDS:
        return Decimal(SE_SCALE_WORDS[word]), SE_SCALE_WORDS[word], index + 1
    multiplier, next_index = _read_multiplier(words, index)
    scale_word = _word_at(words, next_index)
    if multiplier is None or scale_word not in SCALE_WORDS:
        return None
    scale = SCALE_WORDS[scale_word]
    return multiplier * scale, scale, next_index + 1


def _tail_readings(total: Decimal, tail: int, has_hundreds: bool, last_scale: int) -> list[Decimal]:
    """Reads what follows the last scale word, which is only sure as hundreds after ribu.

    "seribu lima ratus" is 1.500. "dua juta lima ratus" is often 2,5 juta with
    the ribu dropped, and the "dua" in "lima puluh ribu dua" may count plates.
    """
    literal = total + tail
    if last_scale == THOUSAND:
        return [literal] if has_hundreds else [literal, total]
    return [total + tail * (last_scale // THOUSAND), literal]


def spelled_number_readings(text: str) -> list[Decimal]:
    """Every honest reading of a run of number words: none means it fits no form, two mean ask."""
    words = text.split()
    total = Decimal(0)
    last_scale = 0
    index = 0
    while (group := _read_group(words, index)) is not None:
        value, scale, index = group
        if last_scale and scale >= last_scale:
            return []  # "dua ribu tiga ribu": two amounts, or a slip.
        total, last_scale = total + value, scale
    tail, index, has_hundreds = _read_below_thousand(words, index)
    if not last_scale or index != len(words):
        return []
    if tail == 0:
        return [total]
    return _tail_readings(total, tail, has_hundreds, last_scale)
