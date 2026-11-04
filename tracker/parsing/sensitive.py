"""Spots what looks like a card, account or ID number, so it's never stored (PR3, T13).

This errs towards refusing. A false alarm costs one retyped message, and a stored card
number can't be taken back. WhatsApp's Business policy was the first reason for this,
and an unofficial channel makes it matter more, not less.

People group these numbers with dots, dashes, slashes or spaces, and phones type
look-alike characters (a no-break space, an en dash, full-width digits). So the check
runs on a view where every digit is 0-9 and every separator is a plain one.
"""

import re
import unicodedata
from typing import Final

from tracker.parsing.amount import clean_text

MIN_CARD_DIGITS: Final = 13
MAX_CARD_DIGITS: Final = 19
MIN_LUHN_DIGITS: Final = 12  # Below this, one number in ten passes by chance, which would refuse prices.
MIN_ACCOUNT_DIGITS: Final = 8  # A short account number only counts next to a word that says so.
MIN_HIDDEN_DIGITS: Final = 8  # A description never keeps a number this long, such as a phone number (PR1).

_SEPARATOR_LOOK_ALIKES: Final = str.maketrans(
    {
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d", "-"),
        "\uff0e": ".",
        "\uff0c": ",",
        "\uff0f": "/",
        "\uff3f": "_",
    }
)

# One number, glued by separators ("0411.1111.1111.1111"), or at least three groups
# with spaces between them, as printed on a card. Three, because two would glue a
# phone number to the amount after it.
_DIGIT_RUN: Final = re.compile(r"[0-9]{3,6}(?:[ .,/_-][0-9]{3,6}){2,}|[0-9]+(?:[.,/_-][0-9]+)*")
# A rupiah amount in thousands groups, "1.500.000". It never starts with a zero, which
# is how "0123.456.789" is told apart. Checked piece by piece and left out, so
# "125.000 250.000 300.000" stays three prices and isn't read as one long number.
_AMOUNT_IN_GROUPS: Final = re.compile(r"[1-9][0-9]{0,2}(?:[.,][0-9]{3})+(?:[.,][0-9]{1,2})?")
_PIECE_GAP: Final = re.compile(r"[ /_-]")
_ACCOUNT_WORD: Final = re.compile(r"\b(?:rekening|rek|norek|ktp|nik|cvv|pin)\b", re.IGNORECASE)
# A PIN or CVV is short, so any number of 3 or more digits right after the word counts.
# A number with a unit glued on, "pin 150rb", is a price for a pin badge.
_SECRET_CODE: Final = re.compile(r"\b(?:pin|cvv)\b[^0-9a-z]{0,3}[0-9]{3,}(?![0-9]*[a-z])")
# Any digits, of any script, joined by any separator a person might type between groups.
_NUMBER_IN_TEXT: Final = re.compile(r"\d+(?:[ .,/_\-\u00a0\u2010-\u2015\u2212]\d+)*")


def _digit_view(text: str) -> str:
    """The text with invisible characters gone, look-alike separators made plain and every digit 0-9."""
    view = clean_text(text).translate(_SEPARATOR_LOOK_ALIKES)
    characters: list[str] = []
    for character in view:
        characters.append(str(unicodedata.decimal(character)) if character.isdecimal() else character)
    return "".join(characters)


def _digits_without_amounts(run: str) -> str:
    """The run's digits, leaving out any piece that is an amount like 1.500.000."""
    kept_pieces = [piece for piece in _PIECE_GAP.split(run) if not _AMOUNT_IN_GROUPS.fullmatch(piece)]
    return re.sub("[^0-9]", "", "".join(kept_pieces))


def _passes_luhn(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character)
        if position % 2 == 1:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit  # Luhn's rule: double every second digit, sum its digits.
        total += digit
    return total % 10 == 0


def _is_sensitive_number(digits: str, has_account_word: bool) -> bool:
    if MIN_CARD_DIGITS <= len(digits) <= MAX_CARD_DIGITS:
        return True
    if MIN_LUHN_DIGITS <= len(digits) < MIN_CARD_DIGITS and _passes_luhn(digits):
        return True
    return has_account_word and len(digits) >= MIN_ACCOUNT_DIGITS


def looks_sensitive(text: str) -> bool:
    view = _digit_view(text)
    if _SECRET_CODE.search(view):
        return True
    has_account_word = _ACCOUNT_WORD.search(view) is not None
    for run in _DIGIT_RUN.findall(view):
        if _is_sensitive_number(_digits_without_amounts(run), has_account_word):
            return True
    return False


def _hide_if_long(number: re.Match[str]) -> str:
    text = number.group()
    digit_count = sum(character.isdecimal() for character in text)
    has_foreign_digit = any(character.isdecimal() and not character.isascii() for character in text)
    return "#" if digit_count >= MIN_HIDDEN_DIGITS or has_foreign_digit else text


def hide_long_numbers(description: str) -> str:
    """Replaces any long number, however it's grouped, and any non-0-9 digit with "#" (PR1)."""
    return _NUMBER_IN_TEXT.sub(_hide_if_long, description)
