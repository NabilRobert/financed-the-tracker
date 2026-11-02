# WhatsApp finance tracker

A personal finance tracker that lives in WhatsApp, for Arthur and a few invited
people in Indonesia. You send "makan 25rb" and it is logged in exact rupiah.

**Status: slice 2 of 8.** The parser, its measured corpus and the ledger exist.
There is no WhatsApp channel or server yet, and no model is used.
[The plan](docs/planning/slice-plan.md) lists what comes next.

## Run it

```bash
uv sync && uv run pytest
```

That needs only [uv](https://docs.astral.sh/uv/). It costs nothing and calls no
service. The run ends with the parser's accuracy on the corpus. CI also runs
`ruff format --check .`, `ruff check .` and `pyright`.

The ledger tests need a throwaway Postgres, and skip without one. Start it with:

```bash
docker run -d --rm --name tracker-test-db -p 127.0.0.1:55441:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
```

It listens on this machine only, has no password, and never holds real data.

To try one message:

```bash
uv run python -c "from tracker.parsing.message import parse_message; print(parse_message('makan 25rb'))"
```

## Layout

| Path | What it holds |
|---|---|
| `tracker/parsing/amount.py` | `parse_amount`: one amount's text to whole rupiah, or a refusal |
| `tracker/parsing/message.py` | `parse_message`: a whole message to an entry, a question, or nothing |
| `tracker/parsing/keywords.py` | The word lists: categories, income words, commands |
| `tracker/ledger/scope.py` | `person_transaction`: the one way in, limited to one person |
| `tracker/ledger/entries.py` | `write_entry` and `void_entry` |
| `tracker/ledger/categories.py` | Choosing a category, and the words a person teaches it |
| `tracker/ledger/budgets.py` | Limits, what's used, and warnings that arrive once |
| `tracker/ledger/summaries.py` | Totals for a month or a week, in SQL |
| `tracker/ledger/people.py` | Adding a person, and finding one from a webhook's id |
| `migrations/` | The schema, row-level security and grants |
| `ops/db/roles.sql` | The database logins |
| `tests/corpus/` | The labelled messages and the rules for labelling them |
| `tests/corpus_scoring.py` | Scores the rules against a corpus |
| `tests/test_*.py` | The specification, one behaviour per test |
| `docs/guide.md` | A plain-English walk through the code |
| `docs/parser-accuracy.md` | What was measured, and how far to trust it |
| `docs/planning/` | The brief, requirements, decisions and slice plan |

Start with [the guide](docs/guide.md): it follows one message from start to
finish in about 10 minutes.

## Licence

[MIT](LICENSE)
