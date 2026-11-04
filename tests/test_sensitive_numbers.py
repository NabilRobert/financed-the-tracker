"""T13 for numbers written the way people really type them: dotted, dashed, or in other scripts.

test_privacy.py holds the first T13 cases. These were found by the independent review:
a card or account number written with dots, or behind a leading zero, got past the
filter and into an entry's description.
"""

import time

import pytest
from conftest import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.channel.replies import SENSITIVE_NUMBER
from tracker.parsing.amount import MAX_TEXT_CHARS
from tracker.parsing.message import AmbiguousEntry, ParsedEntry, parse_message
from tracker.parsing.sensitive import hide_long_numbers, looks_sensitive

MAX_CHECK_SECONDS = 0.5  # Far above a normal check; only runaway backtracking gets near it.


@pytest.mark.parametrize(
    "message",
    [
        "makan 25rb kartu 0411.1111.1111.1111",
        "tf 50rb ke 0123.4567.8901.2345",
        "bayar 4111/1111/1111/1111",
        "bayar 4111_1111_1111_1111",
        "bayar 4111 1111.1111 1111",
        "bayar 500.000 4111 1111 1111 1111",
        "tf 50rb ke 0123:0456:0789:0123",
        "tf 50rb ke 0123|0456|0789|0123",
        "tf 50rb ke 0123*0456*0789*0123",
        "tf 50rb ke 0123~0456~0789~0123",
        "tf 50rb no 0123\u00b74567\u00b78901\u00b72345",
        "tf 50rb no 0123\u20224567\u20228901\u20222345",
        "tf 50rb no 0123\u30fb4567\u30fb8901\u30fb2345",
        "tf 50rb no 0123\u20094567\u20098901\u20092345",
        "tf 50rb no 0123\u30004567\u30008901\u30002345",
        "no 4.111.111.111.111.111 beli 50rb",
        "bayar 4111\u00a01111\u00a01111\u00a01111",
        "bayar 4111\u20131111\u20131111\u20131111",
        "bayar 4111\u200b1111\u200b1111\u200b1111",
        "bayar \uff14\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11\uff11",
    ],
)
def test_a_card_number_with_any_separator_or_digits_is_caught(message: str) -> None:
    assert looks_sensitive(message)


@pytest.mark.parametrize(
    "message",
    [
        "bayar 50rb ke rek 0123.456.789",
        "tf 50rb rek 1234.567.890",
        "tf 50rb rek 012.345.678",
        "tf 50rb ke rek bri 0123-01-012345-50-1",
        "tf 50rb ke rek mandiri 123-00-1234567-8",
        "tf 50rb ke rek 0123 - 0456 - 0789",
        "tf 50rb ke rek 0123 . 0456 . 0789",
        "tf 50rb ke rek 0123..0456..0789",
        "beli 50rb no kartu 41111111",
    ],
)
def test_an_account_number_written_in_groups_is_caught(message: str) -> None:
    assert looks_sensitive(message)


@pytest.mark.parametrize(
    "message",
    [
        "tf 50rb pin 0123.45",
        "pin: 482913",
        "cvv 123",
        "PIN 0457",
        "tarik 500rb pin=0123",
        "tarik 500rb pin atm 0123",
        "tarik 500rb pin ke 0123",
        "tarik 500rb pinnya 0123",
        "beli 50rb cvv nya 012",
    ],
)
def test_a_number_right_after_pin_or_cvv_is_caught(message: str) -> None:
    assert looks_sensitive(message)


@pytest.mark.parametrize(
    "message",
    [
        "125.000 250.000 300.000",
        "tf 15.000.000 ke rekening bca",
        "harga 1.500.000-2.000.000",
        "kos 1,200,000",
        "beli pin 150rb",
        "pinjam uang 500000",
        "pin 25.000",
    ],
)
def test_amounts_in_thousands_groups_are_not_mistaken_for_a_number_to_hide(message: str) -> None:
    assert not looks_sensitive(message)


@pytest.mark.parametrize(
    ("message", "description"),
    [
        ("pulsa 08.1200.0000 50rb", "pulsa #"),
        ("pulsa 0812.0000 50rb", "pulsa #"),
        ("pulsa 0812-0000-0000 50rb", "pulsa #"),
        ("pulsa 0812 0000 0000 50rb", "pulsa #"),
        ("pulsa 0812 - 0000 - 0000 50rb", "pulsa #"),
        ("pulsa 0812:0000:0000 50rb", "pulsa #"),
    ],
)
def test_a_long_number_in_any_grouping_is_kept_out_of_the_description(message: str, description: str) -> None:
    parsed = parse_message(message)
    assert isinstance(parsed, ParsedEntry)
    assert parsed.description == description


def test_digits_from_another_script_are_kept_out_of_the_description() -> None:
    parsed = parse_message("kopi \uff12\uff15 25rb")
    assert isinstance(parsed, AmbiguousEntry)
    assert parsed.description == "kopi #"


def test_a_short_number_stays_in_the_description() -> None:
    parsed = parse_message("tgl 25 makan 25rb")
    assert isinstance(parsed, ParsedEntry)
    assert parsed.description == "tgl 25 makan"


@pytest.mark.parametrize(
    "filler", ["1.", "1 ", "1234 ", "123-", "\u00a01", "pin 1", "pin a ", "1.000 ", "1 - ", "0 . ", "1:"]
)
def test_a_long_run_of_digits_and_separators_is_checked_quickly(filler: str) -> None:
    message = (filler * MAX_TEXT_CHARS)[:MAX_TEXT_CHARS]
    started = time.perf_counter()
    looks_sensitive(message)
    hide_long_numbers(message)
    assert time.perf_counter() - started < MAX_CHECK_SECONDS


async def _descriptions_of(owner_engine: AsyncEngine, whatsapp_id: str) -> list[str]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT e.description FROM entry e JOIN person p ON p.id = e.person_id WHERE p.whatsapp_id = :id
                UNION ALL
                SELECT q.description FROM pending_entry q JOIN person p ON p.id = q.person_id WHERE p.whatsapp_id = :id
            """),
            {"id": whatsapp_id},
        )
        return [row.description for row in rows]


@pytest.mark.parametrize(
    "message",
    ["makan 25rb kartu 0411.1111.1111.1111", "bayar 50rb ke rek 0123.456.789", "kopi 25 rek 0123.456.789"],
)
async def test_a_dotted_card_or_account_number_is_refused_and_never_stored(
    owner_engine: AsyncEngine, bot: Bot, member: str, message: str
) -> None:
    await bot.say(member, message)
    assert bot.waha.texts == [SENSITIVE_NUMBER]
    assert await _descriptions_of(owner_engine, member) == []
