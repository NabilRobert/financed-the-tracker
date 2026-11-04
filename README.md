# WhatsApp finance tracker

A personal finance tracker that lives in WhatsApp, for Arthur and a few invited
people in Indonesia. You send "makan 25rb" and it is logged in exact rupiah.

**It runs on [WAHA](https://waha.devlike.pro), an unofficial WhatsApp client.** WhatsApp's
terms don't allow that, and the number can be banned without warning. Arthur chose this
knowingly: [ADR-022](docs/planning/phase-5-architecture-decisions.md#adr-022-waha-replaces-metas-cloud-api)
records the decision and its risks. The records live in Postgres and survive a ban.

**Status: slice 4 of 8.** The parser, the ledger, the WhatsApp channel and its privacy
controls exist, and the channel is proved with signed fixtures only. No number has been linked yet, there's
no server, and no model is used.

**Security comes first:** no real person's data enters the system before
[this gate](docs/planning/slice-plan.md#the-real-data-gate) passes. See
[the threat model](docs/planning/threat-model.md).
[The plan](docs/planning/slice-plan.md) lists what comes next.

## Run it

```bash
uv sync && uv run pytest
```

That needs only [uv](https://docs.astral.sh/uv/). It costs nothing and calls no
service. The run ends with the parser's accuracy on the corpus. CI also runs
`ruff format --check .`, `ruff check .`, `pyright`, a secret scan (gitleaks) and
a dependency audit (`uv audit`).

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
| `tracker/parsing/spelled.py` | Reads number words such as `lima puluh ribu` |
| `tracker/parsing/message.py` | `parse_message`: a whole message to an entry, a question, or nothing |
| `tracker/parsing/keywords.py` | The word lists: categories, income words, commands |
| `tracker/channel/webhook.py` | The HTTP route: size, signature, dispatch, and the only place that sends |
| `tracker/channel/gateway.py` | The one interface to whatever carries messages |
| `tracker/channel/waha.py` | The only file that knows WAHA's API |
| `tracker/channel/conversation.py` | What the bot does with a message |
| `tracker/channel/commands.py` | `rekap`, `budget`, `kategori`, `bantuan` |
| `tracker/channel/replies.py` | Everything the bot says, including the consent notice |
| `tracker/channel/leaving.py` | `ekspor` and `hapus data saya` |
| `tracker/channel/tracing.py` | Spans, with no content |
| `tracker/channel/settings.py` | Settings that refuse to start unsafely |
| `tracker/cli.py` | Arthur's commands: add, suspend, resume, export, delete and purge |
| `tracker/serve.py` | Runs the server |
| `ops/compose.yaml`, `Dockerfile` | The two containers, with nothing public |
| `ops/backup/` | The nightly encrypted backup, and the deletion list copied off the server |
| `ops/waha/` | WAHA's session config |
| `docs/runbook-deploy.md` | Deploying, and checking the gate on the server |
| `tracker/ledger/scope.py` | `person_transaction`: the one way in, limited to one person |
| `tracker/ledger/entries.py` | `write_entry` and `void_entry` |
| `tracker/ledger/categories.py` | Choosing a category, and the words a person teaches it |
| `tracker/ledger/budgets.py` | Limits, what's used, and warnings that arrive once |
| `tracker/ledger/summaries.py` | Totals for a month or a week, in SQL |
| `tracker/ledger/pending.py` | Entries waiting for a person's answer |
| `tracker/ledger/people.py` | The allow-list, finding a webhook's sender, and consent |
| `tracker/ledger/export.py` | A person's records as CSV, with no channel needed |
| `tracker/ledger/retention.py` | The purge job |
| `migrations/` | The schema, row-level security and grants |
| `ops/db/roles.sql` | The database logins |
| `tracker/parsing/sensitive.py` | Spots card, account and ID numbers |
| `tracker/corpus/` | The labelled messages, their loader and the rules for labelling them. In the package so other projects can import it |
| `tests/corpus_scoring.py` | Scores the rules against a corpus |
| `tests/test_*.py` | The specification, one behaviour per test |
| `docs/guide.md` | A plain-English walk through the code |
| `docs/parser-accuracy.md` | What was measured, and how far to trust it |
| `docs/planning/` | The brief, requirements, threat model, decisions and slice plan |

Start with [the guide](docs/guide.md): it follows one message from start to
finish in about 10 minutes.

## Licence

[MIT](LICENSE)
