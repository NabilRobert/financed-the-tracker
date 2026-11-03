"""parse_message as a specification: what is written, what asks, what is left alone."""

import random
import string

import pytest

from tracker.parsing.amount import MAX_ENTRY_RUPIAH, AmbiguityReason
from tracker.parsing.message import (
    MAX_DESCRIPTION_CHARS,
    AmbiguousEntry,
    Direction,
    ParsedEntry,
    parse_message,
)

FUZZ_SEED = 20261102
FUZZ_ROUNDS = 5_000
FUZZ_ALPHABET = string.digits * 3 + ".,-+ rbjtkm@x/" + "rp" + "٢５²​ "


def asked(text: str) -> AmbiguousEntry:
    result = parse_message(text)
    assert isinstance(result, AmbiguousEntry)
    return result


def written(text: str) -> ParsedEntry:
    result = parse_message(text)
    assert isinstance(result, ParsedEntry)
    return result


def test_a_plain_message_becomes_an_entry() -> None:
    assert parse_message("makan 25rb") == ParsedEntry(25_000, Direction.SPENDING, "Makan", "makan")


def test_a_bare_small_number_asks_with_both_readings_and_keeps_the_details() -> None:
    entry = asked("kopi 25")
    assert entry.ambiguity.reason is AmbiguityReason.BELOW_MIN_UNITLESS
    assert entry.ambiguity.candidates == (25, 25_000)
    assert (entry.direction, entry.category, entry.description) == (Direction.SPENDING, "Makan", "kopi")


def test_two_amounts_ask_and_offer_both() -> None:
    entry = asked("kopi 25rb roti 10rb")
    assert entry.ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS
    assert entry.ambiguity.candidates == (25_000, 10_000)


def test_an_item_count_is_not_a_second_amount() -> None:
    assert written("beli 2 kopi 50rb").amount_rupiah == 50_000


@pytest.mark.parametrize("text", ["kopi 25 roti 10rb", "pertamax 92 100rb", "warung 99 25rb", "iphone 15 10jt"])
def test_a_small_number_after_a_word_could_be_a_price_so_it_asks(text: str) -> None:
    assert asked(text).ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS


@pytest.mark.parametrize(
    "text", ["tgl 25 makan 50rb", "jam 7 makan 50rb", "jam 12.30 makan 50rb", "makan 3 orang 50rb", "2 hari lalu 50rb"]
)
def test_dates_times_and_measures_are_not_amounts(text: str) -> None:
    assert written(text).amount_rupiah == 50_000


def test_a_measure_alone_is_no_amount() -> None:
    assert parse_message("bensin 5 liter") is None


@pytest.mark.parametrize(
    "text", ["2 x 25rb", "2 x kopi 25rb", "2x25rb", "2x 25rb", "2×25rb", "25rb x 2", "@25rb", "25rb/orang", "12x 1jt"]
)
def test_a_price_per_item_asks_and_never_offers_a_total_it_cannot_know(text: str) -> None:
    ambiguity = asked(text).ambiguity
    assert ambiguity.reason is AmbiguityReason.PER_ITEM_PRICE
    assert ambiguity.candidates == ()


@pytest.mark.parametrize("text", ["$2500", "100 usd", "5000 yen", "50 ringgit"])
def test_a_foreign_currency_is_refused_because_v1_is_rupiah_only(text: str) -> None:
    ambiguity = asked(text).ambiguity
    assert ambiguity.reason is AmbiguityReason.FOREIGN_CURRENCY
    assert ambiguity.candidates == ()


@pytest.mark.parametrize("text", ["kopi ٢٥", "kopi ２５ｒｂ", "halo ٢"])
def test_foreign_digits_are_refused_even_with_no_ascii_amount(text: str) -> None:
    assert asked(text).ambiguity.reason is AmbiguityReason.FOREIGN_DIGITS


@pytest.mark.parametrize("text", ["2 juta setengah", "sejuta setengah", "lima 25rb", "dua juta lima ratus"])
def test_an_amount_inside_a_spelled_out_number_asks(text: str) -> None:
    assert asked(text).ambiguity.reason is AmbiguityReason.SPELLED_NUMBER


@pytest.mark.parametrize(
    "text",
    [
        "mau beli hp 3jt",
        "besok bayar kos 1jt",
        "gak jadi beli 25rb",
        "gak dapat 25rb",
        "tidak beli 25rb",
        "saldo 500rb",
        "saldo minus 25rb",
        "dapat diskon 5rb",
        "kembalian 5rb",
        "utang 50rb",
        "kopi 25rb?",
    ],
)
def test_money_that_may_not_have_moved_asks_with_the_amount_offered(text: str) -> None:
    ambiguity = asked(text).ambiguity
    assert ambiguity.reason is AmbiguityReason.NOT_A_TRANSACTION
    assert len(ambiguity.candidates) == 1


def test_a_shop_name_with_a_spelled_number_asks_rather_than_adding_it() -> None:
    assert asked("makan di seribu rasa 50rb").ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS


def test_cepek_in_a_message_asks_with_both_readings() -> None:
    ambiguity = asked("permen cepek").ambiguity
    assert ambiguity.reason is AmbiguityReason.UNCLEAR_SLANG
    assert ambiguity.candidates == (100, 100_000)


def test_a_number_before_cepek_multiplies_it_so_it_is_not_an_item_count() -> None:
    assert asked("2 cepek").ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS


def test_several_lines_ask_until_p5_is_built() -> None:
    assert asked("makan 25rb\nkopi 15rb").ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS


@pytest.mark.parametrize("text", ["makan25rb", "kopi25", "abc123", "25rban", "25ribuan"])
def test_a_number_glued_to_a_word_is_not_an_amount(text: str) -> None:
    assert parse_message(text) is None


def test_a_number_glued_to_a_word_does_not_stop_the_real_amount() -> None:
    assert written("pertamax92 50rb").amount_rupiah == 50_000


def test_a_phone_number_is_not_an_amount() -> None:
    assert written("pulsa 08120000000 50rb").amount_rupiah == 50_000


@pytest.mark.parametrize(
    "text",
    [
        ".5rb",  # A digit is missing, so this isn't 5rb.
        "kopi .5rb",
        ",5rb",
        "1.000.rb",  # A unit behind a stray dot must not be dropped.
        "1.500abc",  # Not "1" and a word.
        "2jt500.5",  # Not 2jt500 and a bit.
        "08.500",  # Not the 500 of a number that starts with a zero.
    ],
)
def test_a_broken_number_is_never_read_as_part_of_itself(text: str) -> None:
    assert parse_message(text) is None


@pytest.mark.parametrize("text", ["kopi -25rb", "(-25rb)", "kopi Rp -25.000", "kopi +25rb"])
def test_a_signed_amount_inside_a_message_is_refused(text: str) -> None:
    assert asked(text).ambiguity.reason is AmbiguityReason.SIGNED_AMOUNT


def test_a_hyphen_after_a_word_or_a_number_is_not_a_sign() -> None:
    assert written("makan-25rb").amount_rupiah == 25_000
    assert asked("20-25rb").ambiguity.reason is AmbiguityReason.SEVERAL_AMOUNTS


@pytest.mark.parametrize("text", ["rekap", "budget makan 2jt", "kategori transport 5rb", "HAPUS data saya", "ekspor"])
def test_a_command_is_never_an_entry(text: str) -> None:
    assert parse_message(text) is None


@pytest.mark.parametrize("text", ["gaji 8jt", "terima 500rb", "dapat 100rb", "thr 5jt", "cashback 5rb"])
def test_income_keywords_set_the_direction(text: str) -> None:
    assert written(text).direction is Direction.INCOME


@pytest.mark.parametrize("text", ["makan 25rb", "bayar gaji art 2jt", "beli hadiah terima 5rb", "terima kasih 50rb"])
def test_spending_is_the_default_and_beats_a_misleading_keyword(text: str) -> None:
    assert written(text).direction is Direction.SPENDING


def test_salary_and_other_income_get_their_own_categories() -> None:
    assert written("bonus 2jt").category == "Gaji"
    assert written("dapat 100rb").category == "Pemasukan Lain"


def test_no_keyword_means_lainnya() -> None:
    assert written("kado 100rb").category == "Lainnya"


def test_the_first_keyword_in_the_message_decides_the_category() -> None:
    assert written("parkir makan 5rb").category == "Transport"
    assert written("makan parkir 5rb").category == "Makan"


def test_a_keyword_must_be_a_whole_word() -> None:
    assert written("dimakan 5rb").category == "Lainnya"


def test_the_description_is_the_message_without_the_amount() -> None:
    assert written("makan: 25rb").description == "makan"
    assert written("makan 25rb di warteg").description == "makan di warteg"
    assert written("25rb").description == ""


def test_the_description_is_capped() -> None:
    assert len(written("makan 25rb " + "a" * 500).description) == MAX_DESCRIPTION_CHARS


def test_a_message_longer_than_whatsapp_allows_is_none() -> None:
    assert parse_message("makan 25rb " + "a" * 5000) is None


def test_nasty_long_input_stays_fast() -> None:
    for nasty in ("1" * 4096, "1." * 2048, "1,5" * 1300, "rp " * 1300, "25rb " * 800, "-" * 4096):
        parse_message(nasty)


def test_random_input_never_crashes_or_writes_an_invalid_amount() -> None:
    generator = random.Random(FUZZ_SEED)
    for _ in range(FUZZ_ROUNDS):
        text = "".join(generator.choices(FUZZ_ALPHABET, k=generator.randint(0, 12)))
        result = parse_message(text)
        if isinstance(result, ParsedEntry):
            assert type(result.amount_rupiah) is int
            assert 0 < result.amount_rupiah <= MAX_ENTRY_RUPIAH
