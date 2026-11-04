"""Spelled-out amounts as a specification: "lima puluh ribu" is Rp50.000, and a doubt asks.

A spelled number counts as money only with a scale word (ribu, juta, miliar) or
a se- form (seribu, sejuta). "lima puluh" alone could be Rp50 or Rp50.000, and
"dua" is far more often a count than a price, so neither is read.
"""

import time

import pytest

from tracker.parsing.amount import MAX_TEXT_CHARS, AmbiguityReason, Ambiguous, parse_amount
from tracker.parsing.message import AmbiguousEntry, ParsedEntry, parse_message

MAX_PARSE_SECONDS = 0.5  # Far above a normal parse; only runaway backtracking gets near it.


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("setengah juta", 500_000),
        ("lima puluh ribu", 50_000),
        ("satu juta dua ratus ribu", 1_200_000),
        ("dua ribu", 2_000),
        ("sepuluh ribu", 10_000),
        ("sebelas ribu", 11_000),
        ("lima belas ribu", 15_000),
        ("dua puluh lima ribu", 25_000),
        ("seratus ribu", 100_000),
        ("tiga ratus lima puluh ribu", 350_000),
        ("seratus dua puluh tiga ribu", 123_000),
        ("satu ribu", 1_000),
        ("seribu", 1_000),
        ("sejuta", 1_000_000),
        ("satu juta", 1_000_000),
        ("dua juta lima ratus ribu", 2_500_000),
        ("sejuta lima ratus ribu", 1_500_000),
        ("satu setengah juta", 1_500_000),
        ("dua setengah juta", 2_500_000),
        ("setengah ribu", 500),
        ("semiliar", 1_000_000_000),
        ("satu miliar", 1_000_000_000),
        ("lima puluh rb", 50_000),
        ("dua jt", 2_000_000),
        ("dua puluh rebu", 20_000),
        ("Lima Puluh Ribu", 50_000),
        ("lima puluh ribu rupiah", 50_000),
    ],
)
def test_a_spelled_amount_with_a_scale_word_reads_exactly(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("seribu lima ratus", 1_500),
        ("dua ribu lima ratus", 2_500),
        ("dua puluh lima ribu lima ratus", 25_500),
        ("seribu dua ratus lima puluh", 1_250),
        ("sembilan ratus sembilan puluh sembilan ribu sembilan ratus sembilan puluh sembilan", 999_999),
    ],
)
def test_hundreds_after_ribu_are_part_of_the_amount(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        # "2jt500" is sure in writing, but said aloud the "ribu" is often dropped.
        ("dua juta lima ratus", (2_500_000, 2_000_500)),
        ("satu juta dua ratus", (1_200_000, 1_000_200)),
    ],
)
def test_hundreds_after_juta_ask_because_ribu_may_be_dropped(text: str, candidates: tuple[int, ...]) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.SPELLED_NUMBER, candidates)


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        ("lima puluh ribu dua", (50_002, 50_000)),
        ("dua ribu lima puluh", (2_050, 2_000)),
    ],
)
def test_a_small_tail_after_ribu_asks_because_it_may_be_a_count(text: str, candidates: tuple[int, ...]) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.SPELLED_NUMBER, candidates)


@pytest.mark.parametrize(
    "text",
    [
        "lima lima ribu",
        "dua ribu tiga ribu",
        "ribu dua ribu",
        "puluh ribu",
        "dua belas puluh ribu",
        "sejuta setengah",
        "seratus setengah juta",
        "setengah setengah juta",
        "dua ribu juta",
        "satu dua",
    ],
)
def test_a_malformed_spelled_amount_is_refused_rather_than_guessed(text: str) -> None:
    result = parse_amount(text)
    assert result is None or result == Ambiguous(AmbiguityReason.SPELLED_NUMBER)


@pytest.mark.parametrize("text", ["lima puluh ribu dua ribu", "dua ribu juta"])
def test_scale_words_out_of_order_are_refused(text: str) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.SPELLED_NUMBER)


@pytest.mark.parametrize("text", ["dua miliar", "satu miliar seratus ribu", "sembilan ratus miliar"])
def test_a_spelled_amount_over_the_limit_is_refused(text: str) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.OVER_LIMIT)


@pytest.mark.parametrize("text", ["lima puluh", "dua", "seratus", "setengah", "dua puluh lima"])
def test_a_spelled_number_with_no_scale_word_is_not_read(text: str) -> None:
    assert parse_amount(text) is None


@pytest.mark.parametrize("text", ["-lima puluh ribu", "+sejuta"])
def test_a_signed_spelled_amount_is_refused(text: str) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.SIGNED_AMOUNT)


@pytest.mark.parametrize("text", ["limapuluh ribu", "lima puluhribu", "dualima ribu"])
def test_number_words_glued_together_are_not_read(text: str) -> None:
    result = parse_amount(text)
    assert not isinstance(result, int)


@pytest.mark.parametrize(
    ("text", "rupiah", "category"),
    [
        ("makan siang lima puluh ribu", 50_000, "Makan"),
        ("periksa gigi setengah juta", 500_000, "Kesehatan"),
        ("bayar kontrakan satu juta dua ratus ribu", 1_200_000, "Tagihan"),
        ("beli dua kopi lima puluh ribu", 50_000, "Makan"),
        ("dua puluh lima ribu buat bensin", 25_000, "Transport"),
    ],
)
def test_a_message_with_a_spelled_amount_is_written(text: str, rupiah: int, category: str) -> None:
    entry = parse_message(text)
    assert isinstance(entry, ParsedEntry)
    assert (entry.amount_rupiah, entry.category) == (rupiah, category)


def test_the_spelled_amount_is_left_out_of_the_description() -> None:
    entry = parse_message("makan siang lima puluh ribu")
    assert isinstance(entry, ParsedEntry)
    assert entry.description == "makan siang"


@pytest.mark.parametrize("text", ["kopi lima puluh ribu roti 10rb", "1 juta dua ratus ribu", "seribu 25rb"])
def test_a_spelled_amount_beside_another_amount_asks(text: str) -> None:
    entry = parse_message(text)
    assert isinstance(entry, AmbiguousEntry)


def test_a_planned_spelled_amount_asks_like_a_written_one() -> None:
    entry = parse_message("mau beli hp tiga juta")
    assert isinstance(entry, AmbiguousEntry)
    assert entry.ambiguity == Ambiguous(AmbiguityReason.NOT_A_TRANSACTION, (3_000_000,))


@pytest.mark.parametrize("filler", ["lima ", "dua puluh ", "ribu ", "setengah "])
def test_a_long_run_of_number_words_parses_quickly(filler: str) -> None:
    text = (filler * MAX_TEXT_CHARS)[: MAX_TEXT_CHARS - 1]
    started = time.perf_counter()
    parse_message(text)
    assert time.perf_counter() - started < MAX_PARSE_SECONDS
