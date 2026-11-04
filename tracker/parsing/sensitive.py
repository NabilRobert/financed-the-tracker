"""Spots what looks like a card, account or ID number, so it's never stored (PR3, T13).

This errs towards refusing. A false alarm costs one retyped message, and a stored card
number can't be taken back. WhatsApp's Business policy was the first reason for this,
and an unofficial channel makes it matter more, not less.

People group these numbers with dots, dashes, colons, spaces or anything else, and
phones type look-alike characters. So the check doesn't list separators: any short run
of characters that aren't letters or digits joins two groups of digits. It runs on a view
where every digit, of any script, is 0-9.
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

# A rupiah amount in thousands groups, up to 999.999.999.999: "1.500.000". It never starts
# with a zero, which is how "0123.456.789" is told apart, and it's taken out before digits
# are joined, so "125.000 250.000 300.000" stays three prices, not one long number.
_AMOUNT_IN_GROUPS: Final = re.compile(
    r"(?<![0-9.,])[1-9][0-9]{0,2}(?:[.,][0-9]{3}){1,3}(?:[.,][0-9]{1,2})?(?![0-9]|[.,][0-9])"
)
# Between groups: one to three characters that aren't letters or digits, at least one of
# them not a space ("0123 - 0456", "0123:0456", "0123..0456").
_PUNCTUATION_GAP: Final = r"[ ]?[^0-9a-z ]{1,3}[ ]?"
# One number: groups of 3 to 6 digits split by spaces or punctuation, at least three of
# them as printed on a card, or any groups split by punctuation alone. Spaces alone need
# three groups, because two would glue a phone number to the amount after it.
_DIGIT_RUN: Final = re.compile(
    rf"[0-9]{{3,6}}(?:(?:[ ]|{_PUNCTUATION_GAP})[0-9]{{3,6}}){{2,}}|[0-9]+(?:{_PUNCTUATION_GAP}[0-9]+)*"
)
_ACCOUNT_WORD: Final = re.compile(r"\b(?:rekening|rek|norek|kartu|ktp|nik|cvv|pin)\b")
# A number in thousands groups right after one of these, or after one short word such as
# a bank's name, reads like an amount, so it would sit in a question as one:
# "tf ke rek bca 123.456.789". Checked before amounts go, and only from 8 digits. Not
# "kartu": a card number is caught by its length, and "bayar kartu kredit 12.500.000"
# is a bill.
_GROUPED_NUMBER_AFTER_ACCOUNT_WORD: Final = re.compile(
    r"\b(?:rekening|rek|norek)\b(?:[^0-9a-z]+[a-z]{2,8})?[^0-9a-z]{0,3}([0-9][0-9.,]*)"
)
# A PIN, CVV or one-time code is short, so a number of 3 or more digits counts after the
# word, "pinnya" too, even with up to three words between ("pin atm bca saya 0123").
# "pinjam" isn't "pin", and a number with a unit glued on, "pin 150rb", is a price.
_SECRET_CODE: Final = re.compile(
    r"\b(?:m?pin|cvv|otp|sandi|password)(?:nya)?\b(?:[^0-9a-z]+[a-z]+){0,3}[^0-9a-z]{0,3}[0-9]{3,}(?![0-9]*[a-z])"
)
# In a description: digits of any script, joined across up to three characters that
# aren't letters or digits. The amounts are already gone, so joining across spaces is safe.
_NUMBER_IN_TEXT: Final = re.compile(r"\d+(?:(?:[^\w]|_){1,3}\d+)*")


def _digit_view(text: str) -> str:
    """The text cleaned as the parser sees it, with every digit made 0-9."""
    characters: list[str] = []
    for character in clean_text(text):
        characters.append(str(unicodedata.decimal(character)) if character.isdecimal() else character)
    return "".join(characters)


def _has_grouped_account_number(view: str) -> bool:
    for match in _GROUPED_NUMBER_AFTER_ACCOUNT_WORD.finditer(view):
        if len(re.sub("[^0-9]", "", match.group(1))) >= MIN_ACCOUNT_DIGITS:
            return True
    return False


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
    if _SECRET_CODE.search(view) or _has_grouped_account_number(view):
        return True
    has_account_word = _ACCOUNT_WORD.search(view) is not None
    for run in _DIGIT_RUN.findall(_AMOUNT_IN_GROUPS.sub(" x ", view)):
        if _is_sensitive_number(re.sub("[^0-9]", "", run), has_account_word):
            return True
    return False


def _hide_if_long(number: re.Match[str]) -> str:
    text = number.group()
    digit_count = sum(character.isdecimal() for character in text)
    has_foreign_digit = any(character.isdecimal() and not character.isascii() for character in text)
    return "#" if digit_count >= MIN_HIDDEN_DIGITS or has_foreign_digit else text


def hide_long_numbers(description: str) -> str:
    """Replaces any long number, however it's grouped, and any non-0-9 digit with "#" (PR1).

    The backstop doesn't depend on separators at all: if 8 or more digits are left in the
    description, split by anything ("0812 o 0000 o 0000"), every number goes.
    """
    hidden = _NUMBER_IN_TEXT.sub(_hide_if_long, description)
    if sum(character.isdecimal() for character in hidden) >= MIN_HIDDEN_DIGITS:
        return re.sub(r"\d+", "#", hidden)
    return hidden
