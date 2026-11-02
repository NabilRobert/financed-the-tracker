"""The word lists behind message.py, kept apart so changing a word touches no logic.

Whole words only, matched on the lowered message. Per-person keywords (C3)
arrive with the ledger in slice 2 and sit on top of these starter lists.
"""

from typing import Final

DEFAULT_SPENDING_CATEGORY: Final = "Lainnya"
SALARY_CATEGORY: Final = "Gaji"
OTHER_INCOME_CATEGORY: Final = "Pemasukan Lain"

SPENDING_KEYWORDS: Final[dict[str, frozenset[str]]] = {
    "Makan": frozenset(
        "makan makanan sarapan minum kopi ngopi nasi bakso soto sate mie ayam roti teh jajan gorengan kerupuk "
        "permen warteg warung gofood grabfood shopeefood".split()
    ),
    "Transport": frozenset(
        "grab gojek ojol ojek taksi bensin pertamax pertalite parkir tol krl mrt lrt busway transjakarta kereta "
        "angkot bus pesawat motor mobil".split()
    ),
    "Belanja": frozenset(
        "belanja belanjaan indomaret alfamart shopee tokopedia lazada pasar supermarket baju sepatu tas laptop "
        "beras sabun sayur".split()
    ),
    "Tagihan": frozenset(
        "tagihan listrik pln pdam wifi internet indihome pulsa kuota kos kost sewa kontrakan cicilan bpjs "
        "asuransi pajak".split()
    ),
    "Hiburan": frozenset("hiburan nonton bioskop netflix spotify youtube karaoke konser game liburan wisata".split()),
    "Kesehatan": frozenset("obat dokter apotek klinik rs sakit gigi vitamin periksa".split()),
}

SALARY_WORDS: Final = frozenset("gaji gajian thr bonus upah honor lembur".split())
OTHER_INCOME_WORDS: Final = frozenset(
    "terima nerima diterima dapat dapet mendapat pemasukan pendapatan angpao cashback refund".split()
)
# "bayar gaji art" is money leaving, whatever the word "gaji" suggests.
SPENDING_VERBS: Final = frozenset("bayar beli".split())

# Slice 3 routes these before parsing. Refusing here too means a routing slip
# can't turn "budget makan 2jt" into a Rp2,000,000 entry.
COMMAND_WORDS: Final = frozenset("rekap budget kategori hapus ekspor bantuan".split())

# A number right after one of these isn't money: "tgl 25", "jam 7".
NUMBER_LABEL_WORDS: Final = frozenset("tgl tanggal jam pukul tahun thn no nomor kode lantai kamar meja".split())
# A number right before one of these counts something else: "5 kg", "2 orang".
MEASURE_WORDS: Final = frozenset(
    "kg kilo gram gr ons liter l km meter ml pcs buah biji butir bungkus botol gelas porsi kotak lembar ekor "
    "orang kali hari minggu bulan tahun jam menit".split()
)
# What may come right before an item count: "beli 2 kopi 50rb".
COUNTING_VERBS: Final = frozenset("beli".split())

NUMBER_WORDS: Final = frozenset(
    "satu dua tiga empat lima enam tujuh delapan sembilan sepuluh sebelas belas puluh ratus ribu juta "
    "seratus setengah seperempat".split()
)

FOREIGN_CURRENCY_PATTERN: Final = r"[$€£¥]|\b(?:usd|sgd|myr|eur|jpy|rm|yen|dollar|dolar|ringgit|euro)\b"
# "2 x 25rb" and "@25rb": the total still needs working out.
PER_ITEM_PATTERN: Final = (
    r"@|(?<![a-z])[0-9]+ ?[x\u00d7](?![a-z])|(?<![a-z0-9])[x\u00d7] ?[0-9]"
    r"|\b(?:per|tiap|setiap|masing|each)\b|(?:rb|ribu|k|jt|juta|[0-9])/[a-z]"
)
# Money that hasn't moved, isn't the person's, or is being denied: "gak dapat 25rb".
NOT_A_TRANSACTION_PATTERN: Final = (
    r"\b(?:saldo|sisa|harga|mau|akan|rencana|besok|nanti|target|batal|belum|kalau|kalo|utang|hutang|pinjam|minjem"
    r"|minus|diskon|kembalian|ga|gak|nggak|ngga|enggak|tidak|tak|kagak)\b|\?$"
)
