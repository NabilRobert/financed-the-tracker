"""What a person sees: logging, questions, buttons and commands (scenarios 2 to 6 in Phase 2)."""

from datetime import date

import pytest
from conftest import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from webhook_fixtures import HUMAN_CONTACT, button_titles, button_update, image_update, new_message_id, text_update

from tracker.channel.buttons import confirm_payload, undo_payload
from tracker.channel.commands import MONTHS, recap_period
from tracker.ledger.summaries import Period


async def stored_amounts(owner_engine: AsyncEngine, bsuid: str, *, live_only: bool = True) -> list[int]:
    voided_filter = "AND e.voided_at IS NULL" if live_only else ""
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text(f"""
                SELECT e.amount_rupiah FROM entry e JOIN person p ON p.id = e.person_id
                WHERE p.bsuid = :bsuid {voided_filter} ORDER BY e.id
            """),
            {"bsuid": bsuid},
        )
    return [row.amount_rupiah for row in rows]


async def test_a_plain_message_is_logged_and_says_what_it_was(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    assert bot.meta.texts() == ["Tercatat pengeluaran: Makan Rp25.000"]
    assert await stored_amounts(owner_engine, member) == [25_000]


async def test_income_says_so(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "gaji 8jt"))
    assert bot.meta.texts() == ["Tercatat pemasukan: Gaji Rp8.000.000"]


async def test_the_undo_button_voids_the_entry_and_is_safe_to_repeat(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    undo = bot.meta.payloads()[0]
    await bot.post(button_update(member, undo))
    assert bot.meta.texts()[-1] == "Dibatalkan."
    assert await stored_amounts(owner_engine, member) == []
    await bot.post(button_update(member, undo))
    assert bot.meta.texts()[-1] == "Sudah dibatalkan."
    assert await stored_amounts(owner_engine, member, live_only=False) == [25_000]


async def test_a_repeat_delivery_adds_nothing_and_sends_no_second_reply(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    update = text_update(member, "makan 25rb", message_id=new_message_id())
    await bot.post(update)
    await bot.post(update)
    assert await stored_amounts(owner_engine, member) == [25_000]
    assert len(bot.meta.sent) == 1


async def test_a_repeated_tap_acts_once(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    tap = button_update(member, bot.meta.payloads()[0], message_id=new_message_id())
    await bot.post(tap)
    await bot.post(tap)
    assert len(bot.meta.sent) == 2


async def test_a_small_bare_number_asks_and_writes_nothing_until_answered(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.post(text_update(member, "kopi 25"))
    assert await stored_amounts(owner_engine, member) == []
    first, second, cancel = bot.meta.payloads()
    assert (first.split(":")[0], second.split(":")[2], cancel.split(":")[0]) == ("ya", "1", "batal")
    await bot.post(button_update(member, second))
    assert await stored_amounts(owner_engine, member) == [25_000]
    assert bot.meta.texts()[-1] == "Tercatat pengeluaran: Makan Rp25.000"


async def test_choosing_the_first_reading_writes_that_one(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    await bot.post(button_update(member, bot.meta.payloads()[0]))
    assert await stored_amounts(owner_engine, member) == [25]


async def test_batal_on_a_question_writes_nothing(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    await bot.post(button_update(member, bot.meta.payloads()[2]))
    assert bot.meta.texts()[-1] == "Oke, tidak dicatat."
    assert await stored_amounts(owner_engine, member) == []


async def test_tapping_ya_twice_writes_one_entry(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    ya = bot.meta.payloads()[1]
    await bot.post(button_update(member, ya))
    await bot.post(button_update(member, ya))
    assert await stored_amounts(owner_engine, member) == [25_000]
    assert bot.meta.texts()[-1].startswith("Pertanyaan itu sudah selesai")


async def test_ya_after_batal_writes_nothing(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    ya, _, cancel = bot.meta.payloads()
    await bot.post(button_update(member, cancel))
    await bot.post(button_update(member, ya))
    assert await stored_amounts(owner_engine, member) == []


async def test_an_expired_question_writes_nothing(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    ya = bot.meta.payloads()[1]
    async with owner_engine.begin() as connection:
        await connection.execute(text("UPDATE pending_entry SET expires_at = now() - interval '1 second'"))
    await bot.post(button_update(member, ya))
    assert await stored_amounts(owner_engine, member) == []
    assert bot.meta.texts()[-1].startswith("Pertanyaan itu sudah selesai")


async def test_a_question_expires_after_a_day(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    async with owner_engine.connect() as connection:
        hours = await connection.scalar(
            text("""
                SELECT round(extract(epoch FROM p.expires_at - now()) / 3600)
                FROM pending_entry p JOIN person q ON q.id = p.person_id WHERE q.bsuid = :bsuid
            """),
            {"bsuid": member},
        )
    assert hours == 24


async def test_choosing_a_second_reading_that_does_not_exist_writes_nothing(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.post(text_update(member, "mau beli hp 3jt"))
    pending_id = int(bot.meta.payloads()[0].split(":")[1])
    assert (await bot.post(button_update(member, confirm_payload(pending_id, 1)))).status_code == 200
    assert await stored_amounts(owner_engine, member) == []
    assert bot.meta.texts()[-1].startswith("Pertanyaan itu sudah selesai")


async def test_batal_after_ya_says_it_is_too_late(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    _, second, cancel = bot.meta.payloads()
    await bot.post(button_update(member, second))
    await bot.post(button_update(member, cancel))
    assert bot.meta.texts()[-1].startswith("Pertanyaan itu sudah selesai")
    assert await stored_amounts(owner_engine, member) == [25_000]


@pytest.mark.parametrize(
    ("words", "today", "expected"),
    [
        (["maret"], date(2026, 11, 2), Period(date(2026, 3, 1), date(2026, 4, 1))),
        (["november"], date(2026, 11, 2), Period(date(2026, 11, 1), date(2026, 12, 1))),
        (["desember"], date(2026, 11, 2), Period(date(2025, 12, 1), date(2026, 1, 1))),
    ],
)
def test_rekap_of_a_month_means_the_latest_one_not_in_the_future(
    words: list[str], today: date, expected: Period
) -> None:
    assert recap_period(words, today) == expected


async def test_a_large_amount_always_asks_first(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "gaji 10.000.001"))
    assert await stored_amounts(owner_engine, member) == []
    assert bot.meta.texts()[-1] == "Jumlahnya besar. Benar Rp10.000.001?"
    await bot.post(button_update(member, bot.meta.payloads()[0]))
    assert await stored_amounts(owner_engine, member) == [10_000_001]
    assert bot.meta.texts()[-1] == "Tercatat pemasukan: Gaji Rp10.000.001"


async def test_the_largest_amount_written_without_asking_is_ten_million(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.post(text_update(member, "gaji 10jt"))
    assert await stored_amounts(owner_engine, member) == [10_000_000]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("kopi 25rb roti 10rb", "Ada beberapa jumlah. Kirim satu per satu ya."),
        ("2 x 25rb", "Tulis totalnya saja ya, misalnya: kopi 50rb."),
        ("100 usd", "Bot ini hanya mencatat rupiah."),
        ("-25rb", "Tulis jumlahnya tanpa tanda plus atau minus."),
        ("0rb", "Jumlahnya harus lebih dari nol."),
        ("2 miliar", "Jumlahnya terlalu besar untuk dicatat."),
        ("kopi ٢٥", "Tulis angkanya dengan 0 sampai 9."),
    ],
)
async def test_what_a_tap_cannot_settle_is_explained_and_not_stored(
    owner_engine: AsyncEngine, bot: Bot, member: str, message: str, expected: str
) -> None:
    await bot.post(text_update(member, message))
    assert bot.meta.texts() == [expected]
    assert bot.meta.payloads() == []
    assert await stored_amounts(owner_engine, member) == []


async def test_a_message_with_no_amount_gets_the_example(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "halo"))
    assert bot.meta.texts()[0].startswith("Belum paham.")


async def test_a_photo_is_not_read_and_gets_a_note(bot: Bot, member: str) -> None:
    await bot.post(image_update(member))
    assert bot.meta.texts() == ["Bot ini baru bisa membaca teks. Coba tulis: makan 25rb."]


async def test_a_repeated_photo_gets_one_note(bot: Bot, member: str) -> None:
    update = image_update(member)
    await bot.post(update)
    await bot.post(update)
    assert len(bot.meta.sent) == 1


async def test_a_confirmed_entry_carries_its_budget_warning(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget makan 20rb"))
    await bot.post(text_update(member, "kopi 25"))
    await bot.post(button_update(member, bot.meta.payloads()[1]))
    assert bot.meta.texts()[-1].endswith("Budget Makan sudah 100%: Rp25.000 dari Rp20.000.")


async def test_a_question_uses_a_taught_word_for_its_category(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "martabak 30rb"))
    await bot.post(text_update(member, "kategori hiburan"))
    await bot.post(text_update(member, "martabak 25"))
    await bot.post(button_update(member, bot.meta.payloads()[1]))
    assert bot.meta.texts()[-1] == "Tercatat pengeluaran: Hiburan Rp25.000"


async def test_every_button_says_what_it_does(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    assert button_titles(bot.meta.sent[-1]) == ["Batal"]
    await bot.post(text_update(member, "kopi 25"))
    assert bot.meta.texts()[-1] == "Maksudnya yang mana?"
    assert button_titles(bot.meta.sent[-1]) == ["Rp25", "Rp25.000", "Batal"]
    await bot.post(text_update(member, "mau beli hp 3jt"))
    assert bot.meta.texts()[-1] == "Mau dicatat sebagai transaksi? Rp3.000.000?"
    assert button_titles(bot.meta.sent[-1]) == ["Ya", "Batal"]
    await bot.post(text_update(member, "gaji 20jt"))
    assert button_titles(bot.meta.sent[-1]) == ["Ya", "Batal"]


async def test_a_budget_warning_travels_with_the_entry_reply(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget makan 100rb"))
    assert bot.meta.texts()[-1] == "Budget Makan: Rp100.000 per bulan."
    await bot.post(text_update(member, "makan 80rb"))
    assert (
        bot.meta.texts()[-1] == "Tercatat pengeluaran: Makan Rp80.000\nBudget Makan sudah 80%: Rp80.000 dari Rp100.000."
    )


async def test_a_confirmed_entry_can_be_undone_too(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kopi 25"))
    await bot.post(button_update(member, bot.meta.payloads()[1]))
    await bot.post(button_update(member, bot.meta.payloads()[0]))
    assert bot.meta.texts()[-1] == "Dibatalkan."
    assert await stored_amounts(owner_engine, member) == []


async def test_budget_lists_limits_with_what_is_used(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget makan 100rb"))
    await bot.post(text_update(member, "makan 25rb"))
    await bot.post(text_update(member, "budget"))
    assert bot.meta.texts()[-1] == "Makan: Rp25.000 dari Rp100.000"


async def test_budget_with_nothing_set_says_how(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget"))
    assert bot.meta.texts() == ["Belum ada budget. Tulis: budget makan 2jt"]


@pytest.mark.parametrize("message", ["budget makan", "budget makan banyak", "budget makan 25"])
async def test_a_budget_without_a_clear_amount_says_how(bot: Bot, member: str, message: str) -> None:
    await bot.post(text_update(member, message))
    assert bot.meta.texts() == ["Tulis seperti ini: budget makan 2jt"]


async def test_a_budget_amount_may_be_two_words(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget makan 2 juta"))
    assert bot.meta.texts() == ["Budget Makan: Rp2.000.000 per bulan."]


async def test_kategori_may_be_two_words(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "gaji 1jt"))
    await bot.post(text_update(member, "kategori pemasukan lain"))
    assert bot.meta.texts()[-1] == "Diubah ke Pemasukan lain. Kata ini akan diingat."


async def test_a_budget_on_an_unknown_category_says_so(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "budget gaji 1jt"))
    assert bot.meta.texts() == ["Kategori itu tidak ada, atau bukan kategori pengeluaran."]


async def test_rekap_totals_this_month(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    await bot.post(text_update(member, "gaji 8jt"))
    await bot.post(text_update(member, "rekap"))
    assert bot.meta.texts()[-1] == "Pengeluaran: Rp25.000\nPemasukan: Rp8.000.000\nMakan: Rp25.000\nGaji: Rp8.000.000"


async def test_rekap_leaves_out_undone_entries(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    await bot.post(button_update(member, bot.meta.payloads()[0]))
    await bot.post(text_update(member, "rekap"))
    assert bot.meta.texts()[-1] == "Belum ada catatan untuk periode itu."


async def test_rekap_minggu_ini_covers_today(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    assert (await bot.post(text_update(member, "rekap minggu ini"))).status_code == 200
    assert bot.meta.texts()[-1].startswith("Pengeluaran: Rp25.000")


async def test_rekap_of_a_month_with_no_entries_says_so(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "rekap februari"))
    assert bot.meta.texts() == ["Belum ada catatan untuk periode itu."]


def test_every_month_is_named() -> None:
    assert sorted(MONTHS.values()) == list(range(1, 13))


@pytest.mark.parametrize("message", ["rekap kemarin dulu", "rekap maret lalu"])
async def test_an_unknown_rekap_says_how(bot: Bot, member: str, message: str) -> None:
    await bot.post(text_update(member, message))
    assert bot.meta.texts() == ["Tulis: rekap, rekap minggu ini, atau rekap maret."]


async def test_kategori_changes_the_latest_entry_and_remembers_the_word(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "martabak 30rb"))
    await bot.post(text_update(member, "kategori makan"))
    assert bot.meta.texts()[-1] == "Diubah ke Makan. Kata ini akan diingat."
    await bot.post(text_update(member, "martabak 20rb"))
    assert bot.meta.texts()[-1] == "Tercatat pengeluaran: Makan Rp20.000"


async def test_kategori_with_no_entry_says_so(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kategori makan"))
    assert bot.meta.texts() == ["Belum ada catatan untuk diubah."]


async def test_kategori_with_no_name_says_how(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "kategori"))
    assert bot.meta.texts() == ["Tulis seperti ini: kategori transport"]


async def test_kategori_that_does_not_exist_says_so(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    await bot.post(text_update(member, "kategori liburan panjang"))
    assert bot.meta.texts()[-1] == "Kategori itu tidak ada untuk catatan ini."


async def test_bantuan_gives_a_path_to_a_human(bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "bantuan"))
    assert HUMAN_CONTACT in bot.meta.texts()[0]


@pytest.mark.parametrize("message", ["hapus data saya", "ekspor"])
async def test_a_command_not_built_yet_says_who_to_ask(bot: Bot, member: str, message: str) -> None:
    await bot.post(text_update(member, message))
    assert HUMAN_CONTACT in bot.meta.texts()[0]


async def test_undo_of_an_unknown_entry_says_so(bot: Bot, member: str) -> None:
    await bot.post(button_update(member, undo_payload(999_999_999)))
    assert bot.meta.texts() == ["Catatan itu tidak ditemukan."]
