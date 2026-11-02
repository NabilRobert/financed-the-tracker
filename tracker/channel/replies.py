"""Everything the bot says, in one place, in Indonesian.

Changing the consent notice means changing CONSENT_NOTICE_VERSION too: consent is
recorded against the version, and a test pins the text to it (I7, T18). Interactive
bodies are limited to 1,024 characters and button titles to 20, by Meta.
"""

from dataclasses import dataclass
from typing import Final

from tracker.channel.buttons import ButtonAction, agree_payload, cancel_payload, confirm_payload, undo_payload
from tracker.ledger.budgets import BudgetUse, BudgetWarning
from tracker.ledger.summaries import Summary
from tracker.parsing.amount import AmbiguityReason
from tracker.parsing.message import Direction


@dataclass(frozen=True)
class ReplyButton:
    title: str
    payload: str


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: tuple[ReplyButton, ...] = ()


CONSENT_NOTICE_VERSION: Final = 20261102
CONSENT_NOTICE: Final = """Halo! Ini bot pencatat keuangan pribadi. Sebelum mulai, mohon baca dulu.

Yang disimpan: jumlah, kategori, catatan singkat, dan tanggal, plus ID WhatsApp kamu. Isi pesan lengkap tidak disimpan.

Siapa yang bisa melihat:
- Meta (WhatsApp) bisa membaca semua pesan di sini, dan menyimpannya sampai 30 hari.
- Arthur, pengelola bot, bisa melihat semua isi database.
- Bot ini tidak memakai model AI.

Jangan kirim nomor kartu, rekening, atau KTP.

Ini bukan layanan keuangan. Bot hanya mencatat apa yang kamu tulis.

Kalau setuju, tekan Setuju. Kalau tidak, tidak ada pesanmu yang disimpan."""

CONSENT_THANKS: Final = 'Terima kasih! Coba kirim: makan 25rb. Tulis "bantuan" untuk melihat caranya.'
CONSENT_DECLINED: Final = "Oke. Tidak ada pesanmu yang disimpan."
ALREADY_AGREED: Final = 'Kamu sudah setuju sebelumnya. Mau berhenti atau menghapus data? Tulis "bantuan".'
NOT_UNDERSTOOD: Final = 'Belum paham. Coba tulis seperti ini: makan 25rb. Tulis "bantuan" untuk melihat caranya.'
NOT_TEXT: Final = "Bot ini baru bisa membaca teks. Coba tulis: makan 25rb."
ALREADY_UNDONE: Final = "Sudah dibatalkan."
UNDONE: Final = "Dibatalkan."
ENTRY_NOT_FOUND: Final = "Catatan itu tidak ditemukan."
PENDING_GONE: Final = "Pertanyaan itu sudah selesai atau kedaluwarsa. Kirim lagi ya."
CANCELLED: Final = "Oke, tidak dicatat."
RATE_LIMITED: Final = "Pesanmu terlalu banyak. Coba lagi nanti ya."
BUDGET_HOW: Final = "Tulis seperti ini: budget makan 2jt"
BUDGET_UNKNOWN_CATEGORY: Final = "Kategori itu tidak ada, atau bukan kategori pengeluaran."
BUDGET_EMPTY: Final = "Belum ada budget. Tulis: budget makan 2jt"
RECAP_UNKNOWN: Final = "Tulis: rekap, rekap minggu ini, atau rekap maret."
RECAP_EMPTY: Final = "Belum ada catatan untuk periode itu."
CATEGORY_HOW: Final = "Tulis seperti ini: kategori transport"
CATEGORY_UNKNOWN: Final = "Kategori itu tidak ada untuk catatan ini."
NO_ENTRY_YET: Final = "Belum ada catatan untuk diubah."
TOO_LARGE_TO_TRUST: Final = "Jumlahnya besar. Benar {amount}?"

_REASON_TEXT: Final[dict[AmbiguityReason, str]] = {
    AmbiguityReason.BELOW_MIN_UNITLESS: "Maksudnya yang mana?",
    AmbiguityReason.UNCLEAR_SEPARATORS: "Titik dan komanya kurang jelas. Maksudnya yang mana?",
    AmbiguityReason.UNCLEAR_FORM: "Penulisannya kurang jelas. Maksudnya yang mana?",
    AmbiguityReason.UNCLEAR_UNIT: "M itu juta atau miliar?",
    AmbiguityReason.UNCLEAR_SLANG: "Maksudnya yang mana?",
    AmbiguityReason.NOT_WHOLE: "Jumlahnya harus rupiah utuh. Coba tulis lagi.",
    AmbiguityReason.NOT_POSITIVE: "Jumlahnya harus lebih dari nol.",
    AmbiguityReason.SIGNED_AMOUNT: "Tulis jumlahnya tanpa tanda plus atau minus.",
    AmbiguityReason.OVER_LIMIT: "Jumlahnya terlalu besar untuk dicatat.",
    AmbiguityReason.FOREIGN_DIGITS: "Tulis angkanya dengan 0 sampai 9.",
    AmbiguityReason.SEVERAL_AMOUNTS: "Ada beberapa jumlah. Kirim satu per satu ya.",
    AmbiguityReason.PER_ITEM_PRICE: "Tulis totalnya saja ya, misalnya: kopi 50rb.",
    AmbiguityReason.SPELLED_NUMBER: "Tulis jumlahnya dengan angka ya, misalnya: 1,5jt.",
    AmbiguityReason.FOREIGN_CURRENCY: "Bot ini hanya mencatat rupiah.",
    AmbiguityReason.NOT_A_TRANSACTION: "Mau dicatat sebagai transaksi?",
}

_DIRECTION_WORDS: Final[dict[Direction, str]] = {Direction.SPENDING: "pengeluaran", Direction.INCOME: "pemasukan"}


def format_rupiah(amount_rupiah: int) -> str:
    """S2: Indonesian grouping, as in Rp1.500.000."""
    return f"Rp{amount_rupiah:,}".replace(",", ".")


def consent_request() -> Reply:
    return Reply(
        CONSENT_NOTICE,
        (
            ReplyButton("Setuju", agree_payload(CONSENT_NOTICE_VERSION)),
            ReplyButton("Tidak", f"{ButtonAction.DECLINE}"),
        ),
    )


def help_text(human_contact: str) -> str:
    return (
        "Cara pakai:\n"
        "makan 25rb: catat pengeluaran\n"
        "gaji 8jt: catat pemasukan\n"
        "rekap: total bulan ini\n"
        "rekap minggu ini, rekap maret\n"
        "budget makan 2jt: atur budget\n"
        "budget: lihat semua budget\n"
        "kategori transport: ganti kategori catatan terakhir\n\n"
        f"Mau berhenti, menghapus data, atau butuh bantuan orang? Hubungi {human_contact}."
    )


def not_built_yet(human_contact: str) -> str:
    """For `hapus data saya` and `ekspor` until slice 4: a person is never left guessing."""
    return f"Fitur ini belum tersedia. Untuk sekarang, hubungi {human_contact}."


def _warning_text(warning: BudgetWarning) -> str:
    used, limit = format_rupiah(warning.used_rupiah), format_rupiah(warning.limit_rupiah)
    return f"Budget {warning.category} sudah {warning.threshold_percent}%: {used} dari {limit}."


def entry_written(
    entry_id: int, direction: Direction, category: str, amount_rupiah: int, warning: BudgetWarning | None
) -> Reply:
    text = f"Tercatat {_DIRECTION_WORDS[direction]}: {category} {format_rupiah(amount_rupiah)}"
    if warning is not None:
        text = f"{text}\n{_warning_text(warning)}"
    return Reply(text, (ReplyButton("Batal", undo_payload(entry_id)),))


def question(reason: AmbiguityReason, pending_id: int, amounts_rupiah: tuple[int, ...]) -> Reply:
    """Asks with one button per reading, and Batal."""
    titles = [format_rupiah(amount) for amount in amounts_rupiah] if len(amounts_rupiah) > 1 else ["Ya"]
    text = _REASON_TEXT[reason]
    if len(amounts_rupiah) == 1:
        text = f"{text} {format_rupiah(amounts_rupiah[0])}?"
    buttons = [ReplyButton(title, confirm_payload(pending_id, choice)) for choice, title in enumerate(titles)]
    return Reply(text, (*buttons, ReplyButton("Batal", cancel_payload(pending_id))))


def large_amount_question(pending_id: int, amount_rupiah: int) -> Reply:
    text = TOO_LARGE_TO_TRUST.format(amount=format_rupiah(amount_rupiah))
    buttons = (ReplyButton("Ya", confirm_payload(pending_id, 0)), ReplyButton("Batal", cancel_payload(pending_id)))
    return Reply(text, buttons)


def refusal(reason: AmbiguityReason) -> Reply:
    return Reply(_REASON_TEXT[reason])


def recap(summary: Summary) -> Reply:
    if not summary.categories:
        return Reply(RECAP_EMPTY)
    lines = [
        f"Pengeluaran: {format_rupiah(summary.spending_rupiah)}",
        f"Pemasukan: {format_rupiah(summary.income_rupiah)}",
    ]
    lines.extend(f"{category.category}: {format_rupiah(category.total_rupiah)}" for category in summary.categories)
    return Reply("\n".join(lines))


def budget_list(budgets: list[BudgetUse]) -> Reply:
    if not budgets:
        return Reply(BUDGET_EMPTY)
    lines = [
        f"{use.category}: {format_rupiah(use.used_rupiah)} dari {format_rupiah(use.limit_rupiah)}" for use in budgets
    ]
    return Reply("\n".join(lines))


def budget_set(category: str, limit_rupiah: int) -> Reply:
    return Reply(f"Budget {category}: {format_rupiah(limit_rupiah)} per bulan.")


def category_changed(category: str) -> Reply:
    return Reply(f"Diubah ke {category}. Kata ini akan diingat.")
