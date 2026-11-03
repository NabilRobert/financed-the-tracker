# Parser accuracy

Measured with pytest, at no cost. `uv run pytest` prints these numbers at the
end of every run, so they can't drift from this page without showing up.

## Result

The held-out set was scored once, on commit `16a324b`, with the code frozen
first. **That score is still the honest one.** Three changes came later:

- `cepek` now asks instead of reading as Rp100, because speakers disagree on it.
  No held-out message contains `cepek`.
- `clean_text` now drops control characters, because a NUL byte crashed the
  database write.
- **Spelled-out numbers are now read** (2026-11-03), such as `lima puluh ribu`
  and `setengah juta`. This one *was* found on the held-out set: all three of
  its misses were spelled out, and they were read before the fix. So the
  held-out line CI prints has moved to coverage 100% and straight writes 97.0%,
  and those numbers are not a measurement. Wrong amounts and false writes are
  still 0 there.

The table keeps the figures from `16a324b`. A new honest held-out number needs a
fresh held-out set, written blind to the code and scored once.

| Measure | Held-out (100) | Dev (205 then) | Target |
|---|---|---|---|
| Wrong amounts | **0** | 0 | 0 |
| False writes | **0** | 0 | 0 |
| Coverage (G2) | **96.5%** (82 of 85) | 100% | At least 85% |
| Straight writes | **92.4%** (61 of 66) | 100% | None set |
| Needless questions | 2 | 0 | None set |
| Wrong direction | 2 | 0 | None set |
| Wrong category | 3 | 0 | None set |

**G2 is confirmed, not raised.** The 85% guess stays as the gate in CI. Coverage
of 96.5% on a written corpus says little about real messages, so the dogfood
slice should set the real target.

**Don't read the dev column as accuracy.** The rules were written against that
set, so 100% is what it should show.

## Who wrote and labelled the corpus

**Neither set contains a real user's message, and nobody but Claude has labelled
them.** Arthur hasn't reviewed the labels, and no Indonesian speaker has.

- **Dev set:** written and labelled by Claude Opus 5.5, the same session that
  wrote the parser.
- **Held-out set:** written and labelled by a separate Claude Sonnet agent. It
  worked from the labelling rules and the Phase 2 amount forms only, before the
  parser existed, and was told not to read the code.

Phase 2 says "written by hand". These were written one by one, but by models,
not by a person.

## How far to trust this

- **Same maker.** Both sets, and the labelling rules, come from Claude. A shared
  blind spot about how Indonesians write wouldn't show up here.
- **A small sample.** Zero wrong in 61 written amounts is still consistent with
  a true error rate of up to about 5% (rule of three, 95% confidence).
- **A leak, small and disclosed.** The agent's hand-back quoted a few held-out
  messages before the measurement. No rule was changed because of them.
- **Nine messages are in both sets** (found 2026-11-03). The held-out writer
  reused the examples in the labelling README, and the dev set has them too,
  so the rules saw them. Without them, the held-out set scores 0 wrong amounts,
  0 false writes, coverage 96.2% (75 of 78) and straight writes 92.1% (58 of
  63). The headline figures move by less than half a point, so they stand with
  this note. A test keeps the labels on both copies identical.
- **The order changed.** The plan measures after `parse_amount`. It was measured
  once at the very end instead, which is stricter: nothing changed afterwards.

ADR-014 already names the next check: dogfood misses, counted beside these
numbers.

## What it means

| Term | Meaning |
|---|---|
| **Wrong amount** | The rules wrote an entry with a different number from the label |
| **False write** | The rules wrote an entry the label says must ask, or isn't a log |
| **Coverage** | Of the messages labelled `entry` or `ask`, the share the rules wrote or asked about, so no model is needed |
| **Straight writes** | Of the messages labelled `entry`, the share written correctly with no question |
| **Needless question** | Labelled `entry`, but the rules asked: one extra tap, nothing wrong |
| **Missed** | About money, but the rules returned nothing, so it gets "belum paham" |

## The misses at `16a324b`

Real misses go into the dev set during dogfooding, in fresh wording. The three
spelled-out misses were fixed on 2026-11-03, with fresh wording in the dev set,
which burned the held-out set for coverage (see the result above).

| Message | What happened |
|---|---|
| `beli iphone 15 pro 17jt` | Asked: the 15 could be a second amount |
| `ngopi di warung 88 40rb` | Asked: the 88 could be a second amount |
| `ke dokter anak setengah juta` | Missed: "setengah juta" isn't read |
| `lima puluh ribu makan siang` | Missed: spelled-out numbers aren't read |
| `satu juta dua ratus ribu bayar kos` | Missed: spelled-out numbers aren't read |
| `jual sepeda bekas 1,2jt` | Right amount, written as spending |
| `freelance desain dibayar 750rb` | Right amount, written as spending |
| `martabak manis 45k 😋` | Right amount, category Lainnya, not Makan |

## If the held-out set ever shows a wrong amount

1. Report it as found, with the first number.
2. Fix the whole class of bug, not the one message.
3. Move every message you've read to the dev set.
4. Have a new held-out set written from the rules, and score that once.

## How the checks were tested

- **mutmut:** 502 mutants of `tracker/`; 501 caught. The survivor is equivalent:
  `len(remainder) >= 3` can't differ there, because 3 has already returned.
- **By hand:** mutmut doesn't touch module-level patterns, so 36 changes to the
  regexes and constants were made one at a time. A test caught all 36.
- **Spelled numbers (2026-11-03):** 192 mutants of `spelled.py`; 180 caught.
  Three survivors are equivalent (a made-up word for "no word", `> 1` where
  hundreds are 0 or at least 100, `None` for `0`). The other nine sit in the
  pattern built at import, which mutmut can't reach, so six pattern changes
  were made by hand: four caught, two equivalent and then removed as dead code.
- **Fuzz:** 5,000 seeded random strings, with a fixed seed. No crash, and no
  written amount outside the limits.
