"""parse_amount as a specification: one amount in, whole rupiah or a refusal out."""

import ast
from pathlib import Path

import pytest

from tracker.parsing import amount as amount_module
from tracker.parsing.amount import (
    MAX_ENTRY_RUPIAH,
    MIN_UNITLESS_RUPIAH,
    AmbiguityReason,
    Ambiguous,
    clean_text,
    parse_amount,
)


def is_refused(result: int | Ambiguous | None, reason: AmbiguityReason) -> bool:
    return result == Ambiguous(reason)


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        # Every form in P1.
        ("25rb", 25_000),
        ("25 ribu", 25_000),
        ("25k", 25_000),
        ("25.000", 25_000),
        ("25,000", 25_000),
        ("Rp25.000", 25_000),
        ("1,5jt", 1_500_000),
        ("1.5jt", 1_500_000),
        ("2jt500", 2_500_000),
        ("sejuta", 1_000_000),
        ("goceng", 5_000),
        ("ceban", 10_000),
        ("gopek", 500),
        ("cepek", 100),
    ],
)
def test_every_amount_form_in_p1_reads_exactly(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("25 rb", 25_000),
        ("25rebu", 25_000),
        ("25 K", 25_000),
        ("100k", 100_000),
        ("2,5k", 2_500),
        ("1,25jt", 1_250_000),
        ("1,2345jt", 1_234_500),
        ("0,5jt", 500_000),
        ("0.5k", 500),
        ("2 jt", 2_000_000),
        ("2 juta", 2_000_000),
        ("1 miliar", 1_000_000_000),
        ("1 milyar", 1_000_000_000),
        ("seribu", 1_000),
        ("seceng", 1_000),
        ("ceceng", 1_000),
        ("noceng", 2_000),
        ("goban", 50_000),
    ],
)
def test_units_and_slang_read_exactly(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("1.500.000", 1_500_000),
        ("1,500,000", 1_500_000),
        ("100.000", 100_000),
        ("100.000.000", 100_000_000),
        ("1.500.000,00", 1_500_000),
        ("1,500,000.00", 1_500_000),
        ("1.500", 1_500),
        ("1,500", 1_500),
        ("1000", 1_000),
        ("1500000", 1_500_000),
        ("Rp 25.000", 25_000),
        ("Rp. 25.000", 25_000),
        ("Rp.25.000", 25_000),
        ("RP25.000", 25_000),
        ("IDR 25.000", 25_000),
        ("Rp25.000,-", 25_000),
        ("Rp25.000.-", 25_000),
        ("Rp25.000,00", 25_000),
        ("25.000,00", 25_000),
        ("1,500.00", 1_500),
        ("25000.00", 25_000),
        ("25.000 rupiah", 25_000),
        ("25.000 perak", 25_000),
        ("25.000.", 25_000),
        ("25rb!", 25_000),
    ],
)
def test_written_numbers_read_exactly(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("Rp25", 25),
        ("Rp 500", 500),
        ("25 rupiah", 25),
        ("500 perak", 500),
    ],
)
def test_a_small_number_marked_as_rupiah_is_sure(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "rupiah"),
    [
        ("2jt500", 2_500_000),
        ("1jt250", 1_250_000),
        ("2jt050", 2_050_000),
        ("25rb500", 25_500),
        ("2jt500rb", 2_500_000),
        ("2jt 500rb", 2_500_000),
        ("1 juta 500 ribu", 1_500_000),
        ("1 miliar", 1_000_000_000),
    ],
)
def test_a_lower_part_after_a_unit_is_added(text: str, rupiah: int) -> None:
    assert parse_amount(text) == rupiah


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        ("25", (25, 25_000)),
        ("999", (999, 999_000)),
        ("1", (1, 1_000)),
    ],
)
def test_a_bare_number_under_the_minimum_asks_with_both_readings(text: str, candidates: tuple[int, ...]) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.BELOW_MIN_UNITLESS, candidates)


def test_the_unitless_minimum_is_a_thousand_rupiah_and_is_itself_sure() -> None:
    assert MIN_UNITLESS_RUPIAH == 1_000
    assert parse_amount("999") != 999
    assert parse_amount("1000") == 1_000


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        ("1.500,000", (1_500, 1_500_000)),
        ("1,500.000", (1_500, 1_500_000)),
        ("1.500rb", (1_500, 1_500_000)),
        ("25.000rb", (25_000, 25_000_000)),
        ("1,000jt", (1_000_000, 1_000_000_000)),
        ("1.250jt", (1_250_000,)),  # 1,250 juta is over the limit, so only one reading is offered.
    ],
)
def test_a_separator_that_could_group_or_divide_asks(text: str, candidates: tuple[int, ...]) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.UNCLEAR_SEPARATORS, candidates)


@pytest.mark.parametrize(
    "text",
    [
        "25.5",
        "25,5",
        "1.5",
        "1.50.000",
        "1.0000",
        "1.500,5000",
        "1.5,00.00",
        "1.2.3",
        "0.500",
        "1,5.000.000",
        "1,5rupiah",
        "Rp1,5",
        "0.000,00",
    ],
)
def test_a_number_that_fits_no_written_form_is_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.UNCLEAR_SEPARATORS)


@pytest.mark.parametrize(
    "text", ["1234.000", "12345.000,00", "1.5000,00", "1,500.000,00", "1.500,000.000", "1.500,000,00"]
)
def test_separators_that_change_their_job_midway_are_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.UNCLEAR_SEPARATORS)


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        ("2jt5", (2_500_000,)),
        ("2jt50", (2_500_000,)),
        ("25rb5", (25_500,)),
        ("1k5", (1_500,)),
    ],
)
def test_a_short_part_after_a_unit_asks(text: str, candidates: tuple[int, ...]) -> None:
    assert parse_amount(text) == Ambiguous(AmbiguityReason.UNCLEAR_FORM, candidates)


@pytest.mark.parametrize("text", ["2jt5000", "2rb500jt", "2jt 1500rb", "1rb 1rb", "1jt 5m"])
def test_parts_that_do_not_add_up_are_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.UNCLEAR_FORM)


def test_m_could_be_miliar_or_million() -> None:
    assert parse_amount("1M") == Ambiguous(AmbiguityReason.UNCLEAR_UNIT, (1_000_000, 1_000_000_000))
    assert parse_amount("2m") == Ambiguous(AmbiguityReason.UNCLEAR_UNIT, (2_000_000,))


def test_a_long_fraction_is_refused_before_decimal_rounding_could_make_it_whole() -> None:
    assert is_refused(parse_amount("1," + "0" * 30 + "1k"), AmbiguityReason.OVER_LIMIT)


def test_a_candidate_is_never_zero() -> None:
    assert is_refused(parse_amount("0m"), AmbiguityReason.UNCLEAR_UNIT)


@pytest.mark.parametrize("text", ["1,2345678jt", "25.50", "Rp25,50", "1.500,50", "1.500.000,50", "0,0001rb", "1,0005k"])
def test_a_result_that_is_not_whole_rupiah_is_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.NOT_WHOLE)


@pytest.mark.parametrize(
    "text",
    [
        "-25rb",
        "-25.000",
        "Rp-25.000",
        "Rp -25.000",
        "Rp - 25.000",
        "-Rp25.000",
        "−25rb",
        "–25rb",
        "--25rb",
        "-goceng",
        "-sejuta",
    ],
)
def test_a_negative_amount_is_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.SIGNED_AMOUNT)


def test_a_plus_sign_is_refused_because_the_direction_is_never_a_sign() -> None:
    assert is_refused(parse_amount("+25rb"), AmbiguityReason.SIGNED_AMOUNT)


@pytest.mark.parametrize("text", ["0", "0rb", "Rp0", "0,0jt"])
def test_a_zero_amount_is_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.NOT_POSITIVE)


def test_the_entry_limit_is_a_billion_rupiah_and_is_itself_allowed() -> None:
    assert MAX_ENTRY_RUPIAH == 1_000_000_000
    assert parse_amount("1.000.000.000") == MAX_ENTRY_RUPIAH
    assert is_refused(parse_amount("1.000.000.001"), AmbiguityReason.OVER_LIMIT)


@pytest.mark.parametrize("text", ["2 miliar", "1,5 miliar", "9" * 19, "9" * 21, "9" * 4096, "Rp" + "9" * 300])
def test_a_huge_amount_is_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.OVER_LIMIT)


@pytest.mark.parametrize("text", ["٢٥", "２５ｒｂ", "२५", "๒๕", "25²", "½", "٢٥ribu"])
def test_digits_other_than_ascii_are_refused(text: str) -> None:
    assert is_refused(parse_amount(text), AmbiguityReason.FOREIGN_DIGITS)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "\n\t",
        "halo",
        "rp",
        "rb",
        "juta",
        "k",
        "makan 25rb",
        "25rbx",
        "x25rb",
        "abc123",
        "1e9",
        "1_000",
        "0x10",
    ],
)
def test_text_that_is_not_one_amount_is_none(text: str) -> None:
    assert parse_amount(text) is None


@pytest.mark.parametrize("text", ["025", "007", "08120000000", "00", "025rb"])
def test_a_number_with_a_leading_zero_is_not_an_amount(text: str) -> None:
    assert parse_amount(text) is None


def test_text_longer_than_a_whatsapp_message_is_none() -> None:
    assert parse_amount(" " * 5000 + "25rb") == 25_000
    assert parse_amount("9" * 4097) is None


def test_invisible_characters_and_odd_spaces_do_not_change_the_amount() -> None:
    assert parse_amount("Rp 25.000") == 25_000
    assert parse_amount("25​rb") == 25_000
    assert parse_amount("﻿25rb") == 25_000
    assert parse_amount("‮25rb") == 25_000


def test_clean_text_lowers_and_collapses_whitespace() -> None:
    assert clean_text("  Makan\n\t25RB ") == "makan 25rb"


def test_no_float_is_used_to_read_money() -> None:
    tree = ast.parse(Path(amount_module.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "float" not in names
