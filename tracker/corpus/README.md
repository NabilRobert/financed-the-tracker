# The corpus

Hand-written Indonesian messages with the answer a careful reader would give.
None comes from a real person, and no phone or card number in it is real.

- `dev.jsonl`: the rules are written against this set.
- `holdout.jsonl`: the rules are never tuned on this set. See
  [parser-accuracy.md](../../docs/parser-accuracy.md) for who wrote it and how
  it is used. Nothing may be trained or tuned on it in another project either.

Nine messages are in both sets: the held-out writer reused examples from this
README. A project training on `dev.jsonl` must drop any text that is also in
`holdout.jsonl`.

`tracker.corpus.load_corpus` reads either file into `CorpusMessage` objects.

## One line, one message

```json
{"text": "makan 25rb", "outcome": "entry", "amount": 25000, "direction": "spending", "category": "Makan"}
{"text": "kopi 25", "outcome": "ask"}
{"text": "halo", "outcome": "unparsed"}
```

`amount`, `direction` and `category` appear only when `outcome` is `entry`. An
optional `note` says why a label isn't obvious.

## Labelling rules

Label what the person meant, as a careful Indonesian reader would. Don't label
what a program could manage.

**`entry`:** the reader is sure this records one transaction that happened, and
sure of its exact total in whole rupiah.

- `amount`: the total, in whole rupiah.
- `direction`: `income` for money received (`gaji`, `terima`, `dapat`, and so
  on), otherwise `spending`.
- `category`: for spending, one of `Makan`, `Transport`, `Belanja`, `Tagihan`,
  `Hiburan`, `Kesehatan`, or `Lainnya` if none fits. For income, `Gaji` for pay
  from work, otherwise `Pemasukan Lain`.
- Quantities don't change the total: "beli 2 kopi 50rb" is 50,000.

**`ask`:** it is about money, but writing it without asking could be wrong.

- A bare number under 1,000 with no unit: "kopi 25" could be Rp25 or Rp25,000.
- Two or more possible amounts: "kopi 25rb roti 10rb", "20-25rb".
- A price per item, so the total needs working out: "2 x 25rb", "@25rb".
- Unclear separators or forms: "25.5", "1.500,000", "2jt5".
- `1m`: miliar to an Indonesian reader, million in English.
- Something that didn't happen or isn't a transaction: "mau beli hp 3jt",
  "gak jadi beli 25rb", "saldo 500rb", a question.
- Anything that can't be written at all: a negative or zero amount, a result
  that isn't whole rupiah, more than Rp1,000,000,000, a foreign currency, or
  digits other than 0 to 9.

**`unparsed`:** no amount in it, or it isn't a log at all: greetings, commands
such as `rekap` and `budget makan 2jt`, a phone number on its own.
