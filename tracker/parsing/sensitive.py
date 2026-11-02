"""Spots what looks like a card, account or ID number, so it's never stored (PR3, T13).

This errs towards refusing. A false alarm costs one retyped message, and a stored card
number can't be taken back. WhatsApp's Business policy was the first reason for this,
and an unofficial channel makes it matter more, not less.
"""

import re
from typing import Final

MIN_CARD_DIGITS: Final = 13
MAX_CARD_DIGITS: Final = 19
MIN_LUHN_DIGITS: Final = 12  # Below this, one number in ten passes by chance, which would refuse prices.
MIN_ACCOUNT_DIGITS: Final = 8  # A short account number only counts next to a word that says so.

# One run of digits, or at least three groups as printed on a card: "4111 1111 1111 1111".
# Three, because two would glue a phone number to the amount after it. Amounts use dots
# and commas, so they never match.
_DIGIT_RUN: Final = re.compile(r"[0-9]{3,6}(?:[ -][0-9]{3,6}){2,}|[0-9]+")
_ACCOUNT_WORD: Final = re.compile(r"\b(?:rekening|rek|norek|ktp|nik|cvv|pin)\b", re.IGNORECASE)


def _passes_luhn(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character)
        if position % 2 == 1:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit  # Luhn's rule: double every second digit, sum its digits.
        total += digit
    return total % 10 == 0


def _is_sensitive_run(digits: str, has_account_word: bool) -> bool:
    if MIN_CARD_DIGITS <= len(digits) <= MAX_CARD_DIGITS:
        return True
    if MIN_LUHN_DIGITS <= len(digits) < MIN_CARD_DIGITS and _passes_luhn(digits):
        return True
    return has_account_word and len(digits) >= MIN_ACCOUNT_DIGITS


def looks_sensitive(text: str) -> bool:
    has_account_word = _ACCOUNT_WORD.search(text) is not None
    for run in _DIGIT_RUN.findall(text):
        digits = re.sub("[ -]", "", run)
        if _is_sensitive_run(digits, has_account_word):
            return True
    return False
