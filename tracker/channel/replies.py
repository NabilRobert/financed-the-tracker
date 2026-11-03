"""Everything the bot says, in one place, in Indonesian.

Reply buttons don't work on WAHA, so every choice is a word the person types (ADR-022).
Changing the consent notice means changing CONSENT_NOTICE_VERSION too: consent is
recorded against the version, and a test pins the text to it (I7, T18).
"""

from typing import Final

from tracker.ledger.budgets import BudgetUse, BudgetWarning
from tracker.ledger.summaries import Summary
from tracker.parsing.amount import AmbiguityReason
from tracker.parsing.message import Direction

CONSENT_NOTICE_VERSION: Final = "20261105"
CONSENT_NOTICE: Final = """Halo! Ini bot pencatat keuangan pribadi. Sebelum mulai, mohon baca dulu.

Bot ini memakai jalur tidak resmi. WhatsApp tidak mengizinkannya, jadi nomor bot bisa diblokir kapan saja tanpa pemberitahuan. Kalau itu terjadi, catatanmu tetap aman di server, dan Arthur bisa mengirimkannya kepadamu.

Yang disimpan: jumlah, kategori, catatan singkat, dan tanggal, plus ID WhatsApp kamu. Isi pesan lengkap tidak disimpan.

Siapa yang bisa melihat:
- Arthur, pengelola bot, bisa melihat semua isi database, dan pesanmu sampai ke servernya.
- WhatsApp (Meta) bisa melihat siapa mengirim pesan kepada siapa, dan kapan.
- Bot ini tidak memakai model AI.

Jangan kirim nomor kartu, rekening, atau KTP.

Mau salinan catatanmu? Tulis: ekspor. Mau menghapus semuanya? Tulis: hapus data saya. Cadangan terenkripsi disimpan paling lama 14 hari, jadi data yang dihapus hilang sepenuhnya dari cadangan setelah itu.

Ini bukan layanan keuangan. Bot hanya mencatat apa yang kamu tulis.

Kalau setuju, balas: setuju
Kalau tidak, balas: tidak. Tidak ada pesanmu yang disimpan."""

CONSENT_THANKS: Final = 'Terima kasih! Coba kirim: makan 25rb. Tulis "bantuan" untuk melihat caranya.'
CONSENT_DECLINED: Final = "Oke. Tidak ada pesanmu yang disimpan."
NOT_UNDERSTOOD: Final = 'Belum paham. Coba tulis seperti ini: makan 25rb. Tulis "bantuan" untuk melihat caranya.'
NOT_TEXT: Final = "Bot ini baru bisa membaca teks. Coba tulis: makan 25rb."
ALREADY_UNDONE: Final = "Catatan terakhir sudah dibatalkan."
NOTHING_TO_UNDO: Final = "Belum ada catatan untuk dibatalkan."
CANCELLED: Final = "Oke, tidak dicatat."
QUESTION_EXPIRED: Final = "Pertanyaan itu sudah kedaluwarsa. Kirim lagi ya."
ANSWER_ONE_OR_TWO: Final = "Balas 1, 2, atau batal."
ANSWER_YES_OR_CANCEL: Final = "Balas ya atau batal."
SENSITIVE_NUMBER: Final = (
    "Pesan itu sepertinya berisi nomor kartu, rekening, atau KTP, jadi tidak dicatat. Hapus pesan itu dari chat ini ya."
)
DELETE_CONFIRM: Final = "Ini akan menghapus semua catatanmu dan tidak bisa dikembalikan. Yakin? Balas: hapus semua"
DELETE_HOW: Final = "Untuk menghapus semua catatanmu, tulis: hapus data saya"
DELETED: Final = "Semua datamu sudah dihapus. Terima kasih sudah memakai bot ini."
EXPORT_CAPTION: Final = "Ini semua catatanmu."
EXPORT_FILENAME: Final = "catatan-keuangan.csv"
RATE_LIMITED: Final = "Pesanmu terlalu banyak. Coba lagi nanti ya."
BUDGET_HOW: Final = "Tulis seperti ini: budget makan 2jt"
BUDGET_UNKNOWN_CATEGORY: Final = "Kategori itu tidak ada, atau bukan kategori pengeluaran."
BUDGET_EMPTY: Final = "Belum ada budget. Tulis: budget makan 2jt"
RECAP_UNKNOWN: Final = "Tulis: rekap, rekap minggu ini, atau rekap maret."
RECAP_EMPTY: Final = "Belum ada catatan untuk periode itu."
CATEGORY_HOW: Final = "Tulis seperti ini: kategori transport"
CATEGORY_UNKNOWN: Final = "Kategori itu tidak ada untuk catatan ini."
NO_ENTRY_YET: Final = "Belum ada catatan untuk diubah."

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


def help_text(human_contact: str) -> str:
    return (
        "Cara pakai:\n"
        "makan 25rb: catat pengeluaran\n"
        "gaji 8jt: catat pemasukan\n"
        "batal: batalkan catatan terakhir\n"
        "rekap: total bulan ini\n"
        "rekap minggu ini, rekap maret\n"
        "budget makan 2jt: atur budget\n"
        "budget: lihat semua budget\n"
        "kategori transport: ganti kategori catatan terakhir\n"
        "ekspor: kirim semua catatanmu\n"
        "hapus data saya: hapus semua catatanmu\n\n"
        f"Butuh bantuan orang? Hubungi {human_contact}."
    )


def _warning_text(warning: BudgetWarning) -> str:
    used, limit = format_rupiah(warning.used_rupiah), format_rupiah(warning.limit_rupiah)
    return f"Budget {warning.category} sudah {warning.threshold_percent}%: {used} dari {limit}."


def entry_written(direction: Direction, category: str, amount_rupiah: int, warning: BudgetWarning | None) -> str:
    text = f"Tercatat {_DIRECTION_WORDS[direction]}: {category} {format_rupiah(amount_rupiah)}"
    if warning is not None:
        text = f"{text}\n{_warning_text(warning)}"
    return f"{text}\nSalah? Balas: batal"


def entry_undone(category: str, amount_rupiah: int) -> str:
    return f"Dibatalkan: {category} {format_rupiah(amount_rupiah)}"


def question(reason: AmbiguityReason, amounts_rupiah: tuple[int, ...]) -> str:
    """Asks about one or two readings, and says exactly what to type."""
    text = _REASON_TEXT[reason]
    if len(amounts_rupiah) == 1:
        return f"{text} {format_rupiah(amounts_rupiah[0])}?\n{ANSWER_YES_OR_CANCEL}"
    first, second = (format_rupiah(amount) for amount in amounts_rupiah)
    return f"{text}\nBalas 1 untuk {first}, 2 untuk {second}, atau batal."


def large_amount_question(amount_rupiah: int) -> str:
    return f"Jumlahnya besar. Benar {format_rupiah(amount_rupiah)}?\n{ANSWER_YES_OR_CANCEL}"


def refusal(reason: AmbiguityReason) -> str:
    return _REASON_TEXT[reason]


def recap(summary: Summary) -> str:
    if not summary.categories:
        return RECAP_EMPTY
    lines = [
        f"Pengeluaran: {format_rupiah(summary.spending_rupiah)}",
        f"Pemasukan: {format_rupiah(summary.income_rupiah)}",
    ]
    lines.extend(f"{category.category}: {format_rupiah(category.total_rupiah)}" for category in summary.categories)
    return "\n".join(lines)


def budget_list(budgets: list[BudgetUse]) -> str:
    if not budgets:
        return BUDGET_EMPTY
    return "\n".join(
        f"{use.category}: {format_rupiah(use.used_rupiah)} dari {format_rupiah(use.limit_rupiah)}" for use in budgets
    )


def budget_set(category: str, limit_rupiah: int) -> str:
    return f"Budget {category}: {format_rupiah(limit_rupiah)} per bulan."


def category_changed(category: str) -> str:
    return f"Diubah ke {category}. Kata ini akan diingat."
