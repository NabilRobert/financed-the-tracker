# Slice Plan and Cost Model

## Prerequisites (outside this repo)

| # | Prerequisite | Needed for | Who |
|---|---|---|---|
| P1 | Arthur OKs Phases 1, 2 and 5 | Slice 1 | Arthur |
| P2 | The GitHub repo exists | Pushing anything | Arthur |
| P3 | A spare SIM on a phone, never Arthur's personal number, for a local WAHA test | Spike 2, the live check | Arthur |
| P4 | Juku is deployed and the server is ready for this data, with encrypted backups verified | Slice 5 | Arthur |
| P5 | ~~A subdomain for the webhook~~ Not needed: with WAHA nothing is public | — | — |
| P6 | A decision on WhatsApp's terms (Phase 5, open questions) | Slice 5, real data | Arthur |
| P7 | A dedicated SIM for the bot, which can be the spare one | Slice 5 | Arthur |
| P8 | Sumopod's data terms read, and a top-up separate from Juku's $5 | Slice 7 | Arthur |

## Spikes (local, $0)

| # | Question | Method | Pass | Time |
|---|---|---|---|---|
| 1 | How does pywa behave at the edges? | Done in slice 3; pywa has since gone | Recorded in ADR-004 | Done |
| 2 | What does WAHA really send? | On the spare number, with invented messages: read each event's shape, and how often a LID comes | [The live check](#the-live-check) passes | 1 h |
| 3 | Does it fit? | Idle and busy memory of the app and WAHA, in capped containers, on the server | Under 256 MB and 512 MB, or the caps are revised | 30 min |

Spike 3 can't run on this Mac: the pinned WAHA image is amd64-only, and emulated memory
figures mean nothing.

Spikes 4 and 5 asked about Meta's local storage and template categories, and no longer apply.

## Slices

**Evaluation-driven order, as in Juku:** the tests and corpus come first and
fail. Then the code is written until they pass. Each new test is
mutation-checked once. Each slice updates `docs/guide.md`.

| Slice | Delivers | Done when | Cost |
|---|---|---|---|
| **1. Parser and corpus** | Repo scaffold, README, CI, complexity rules; `parse_amount`; `parse_message`; the corpus | See below | $0 |
| **2. Ledger** | Schema and migrations; row-level security; write, void, categories, keywords; summaries and budgets in SQL; a test that no money is held in a float | Fixture summaries match to the rupiah; the isolation suite passes, mutation-checked | $0 |
| **3. WhatsApp channel** | WAHA adapter behind one interface; signature check; message-id idempotency; allow-list; consent; typed answers; commands; `bantuan`; `tracker export` | Signed fixtures cover every path; replays add nothing; a forged request is refused; one live round trip on the spare number | $0 |
| **4. Privacy and operations** | `hapus data saya`; `ekspor`; card-number refusal; the reply limit; the purge job; send retries (R3); message outcomes; spans; `/healthz` with `version`; no-content tests for spans | Deletion leaves zero rows; no message text in logs or spans | $0 |
| **5. Deploy, gate, then dogfood** | Containers for the app and WAHA, with nothing public; backups included; probes through the systems-monitoring session; [the gate](#the-real-data-gate); then Arthur alone for 2 weeks | Alerts fired on purpose and received; a restart loses nothing; the gate passes; misses are counted and turned into corpus cases | SIM only |
| **6. Invite and monthly summary** | The summary with the first reply of the month; up to 4 invitations, sent by Arthur | The summary can't send twice; deletion checked with a real invited person's agreement | SIM only |
| **7. Model fallback (Should)** | The typed proposal; validation; the Inspect task | Zero invented amounts in the eval; Arthur's OK for each paid run | About $0.10, not budgeted yet |
| **8. Evidence and write-up** | Results from records; JOURNEY; portfolio entry (with the portfolio session) | Every number traces to a file; a privacy check passes | $0 |

## The live check

Slice 3's code is finished, and proved with signed fixtures only. Its one live round
trip waits for P3: a spare SIM that Arthur links to a local WAHA himself. Only invented
messages go through it. This session never links a number.

It has to confirm what the fixtures assume about WAHA, on the pinned image:

- the `message` event's shape on GOWS: `id`, `from`, `fromMe`, `body`, `hasMedia`, and the
  timestamp's units;
- that the session config's `hmac.key` produces the header we check;
- how often a sender arrives as `@lid`, and what `GET /api/{session}/lids/...` returns,
  including for a number that isn't a contact;
- that `@lid` works as a chat id for sending;
- that a new person's messages arrive at all;
- that `session.status` reports a stopped session;
- that `POST /api/sendFile` takes our body, so `ekspor` arrives as a file.

## Security done-when for each slice
## Security done-when for each slice

Security comes first ([ADR-020](phase-5-architecture-decisions.md#adr-020-security-comes-first)).
A slice isn't done until these pass as well, each shown by a test or a command.
T numbers are in the [threat model](threat-model.md).

**Already done:** the secret scan and dependency audit in CI (T30, T31), and
slice 2's isolation, least grants and no-delete rules (T3, T7 to T9, T20).

| Slice | Security done-when |
|---|---|
| **3** | Signature checks fail closed: no secret stops startup, and every bad signature gets 401 or 403 and writes nothing (T1, T2). Unknown senders get nothing (T4). Rate limits work (T5). No secret in a URL or a log (T6). Typed answers can't reach an old question or an older entry (T10). Suspension works (T11). No-content tests pass on every path, failures included (T14). Consent is recorded with its notice version, and nothing is stored before it (T17). The notice warns of the unofficial channel, and its text is pinned (T18). The number is dropped once a LID is known (T21). WAHA is pinned by digest and bound to loopback, with its dashboard off (T36, T38). A public `WAHA_URL` stops startup (T36). Only replies are sent (T41). Own messages and groups are dropped (T42). `tracker export` works with no channel (T40). Only invented messages go through the spare number |
| **4** | Card numbers are refused before any write (T13). No-content tests cover spans and alerts (T14). The purge job meets PR6 (T15). `hapus data saya` leaves zero rows through one narrow database function, and needs a typed confirmation (T11, T16). `ekspor` carries only that person's data (T19) |
| **5** | Non-root, capped containers with no owner login in the app's environment (T21). Postgres and WAHA can't be reached from outside, shown by a probe (T22, T36). Secret rotation rehearsed (T23). WAHA's session files are owner-only and absent from backups (T35). WAHA's running key is checked (T37). Backups are encrypted, and a restore is tested and timed (T25, T26). A deletion survives a restore (T27). Replay survives a restart (T3). A stopped session alerts (T43). Then the gate |
| **6** | The monthly summary stays a reply, never a message the bot starts (T41). The gate's checks are re-run on the deployed commit before the first invitation, and each invited person's consent is recorded |
| **7** | The provider's written terms are read, and Arthur agrees (T29). The injection cases pass, and zero invented amounts (T28). The notice is updated, and existing people consent again, before it's turned on |
| **8** | The privacy check covers every published number, screenshot and log. Nothing real, and no live finding, reaches the public repo |

## The real-data gate

**No real person's data, Arthur's included, enters the system until every line
here passes on the deployed commit.** Until then, only invented messages are
used. Arthur signs it, in writing. A failing control can't be accepted to save
time.

| # | Must be true | Proof | Today |
|---|---|---|---|
| 1 | Webhook signatures fail closed, including a missing secret | Tests, and a forged request against the deployed app | Tested; deployed run in slice 5 |
| 2 | Row-level security holds on the deployed database | The isolation suite passes, and is mutation-checked | Passes in CI; deployed run in slice 5 |
| 3 | No message text, names, numbers or amounts in logs, spans or alerts, including WAHA's own logs | No-content tests, and a sentinel message searched for on the server | Logs tested; the rest in slices 4 and 5 |
| 4 | Backups are encrypted, and a restore works | A timed restore into an empty database ([runbook](../runbook-deploy.md)) | Script tested; drill on the server |
| 5 | Retention and `hapus data saya` work, and deletion survives a restore | Tests, and one real run with invented data | Tested; restore in slice 5 |
| 6 | Rate limits work, and only replies are sent | Tests | Tested |
| 7 | Secret scan and dependency audit pass | CI, green on the deployed commit | In CI since this branch |
| 8 | Database logins have the least privilege | Grant tests, and a check of the app's environment | App login done; the rest in slices 5 and 6 |
| 9 | Consent is recorded, with its notice version, and the notice warns of the unofficial channel | Tests | Tested |
| 10 | An adversarial pass is done, with every finding fixed | A written pass over bad input, failure paths and restarts, plus the threat model's Planned rows | Planned, slice 5 |
| 11 | The independent review is done, with every finding fixed | Its report, kept private until fixed ([who does it](threat-model.md#independent-review)) | Planned, slice 5 |
| 12 | The server is ready for this data (P4) | Confirmed by the server work | Open |
| 13 | **WhatsApp's terms are decided (P6):** the question section 4.2 asked, under WhatsApp's own terms | Arthur's written decision | Open |
| 14 | The threat model is current | Every row is Done, or is listed under "What we accept" | Open |
| 15 | WAHA's API and dashboard are never public | A probe from outside finds nothing; dashboard and Swagger off | Checked locally; probe on the server |
| 16 | WAHA runs with a strong API key | Checked on the running container | Planned, slice 5 |
| 17 | The session files are owner-only, and absent from backups | A permissions check, and a search of a backup | Planned, slice 5 |
| 18 | WAHA's and the app's images are pinned by digest | Tests | Tested |
| 19 | **A dedicated SIM, never Arthur's personal number** | Arthur's written confirmation | Open |
| 20 | No bulk or unsolicited messaging | The send path's test, and no broadcast in the code | Tested |
| 21 | The ledger works without WhatsApp | With WAHA stopped: a restore, then `tracker export` | Export tested; restore in slice 5 |

## Slice 1 in detail: the first slice

**Scope:** pure Python, no I/O. It needs nothing from WhatsApp, Sumopod or the
server, so it can start the moment Arthur OKs the plan.

**Delivers:**
- `pyproject.toml` (uv), ruff with the complexity rules, pyright strict;
- `.github/workflows/ci.yml`, a README with a layout section, `docs/guide.md`;
- `tracker/parsing/amount.py`: `parse_amount(text) -> int | Ambiguous | None`;
- `tracker/parsing/message.py`: `parse_message(text) -> ParsedEntry | ...`;
- `tests/corpus/dev.jsonl` and `tests/corpus/holdout.jsonl`, about 300
  written messages in all, none from a real person;
- a report of coverage, wrong amounts and false writes.

**Order:**
1. Write the corpus and its loader. Tests fail.
2. Write `parse_amount` to pass the dev set. Measure the held-out set once.
3. Add category and direction keywords.
4. Spend a pass trying to break it: unicode digits, huge numbers, empty
   text, several amounts, a number glued to a word.

**Done when:**
- wrong amounts on the held-out set: **0**;
- rules coverage is measured and reported, and G2's guess of 85% is
  confirmed or changed, in writing;
- every amount form in P1 is pinned by a test;
- CI is green, and the new tests are mutation-checked.

**Branch:** `feat/amount-parser`, stacked on `docs/planning`, which merges first.

## Cost model

| Item | Monthly |
|---|---|
| WhatsApp, through WAHA | Rp0: there's no per-message price |
| WAHA | Rp0 |
| Model | $0 in v1; about $0.003–0.017 if on |
| Hosting | Rp0: the existing server |
| Dedicated SIM | A small top-up to keep it active; not priced yet |

**The Rp25,000 cap and the reply counter are gone,** with Meta's prices (ADR-022). What
they protected against now has a different shape: not a bill, but a ban. A reply limit
(R7), R6's rate limits and the reply-only rule replace them.

**Nothing in slices 1 to 5 costs money,** apart from the SIM. Slice 7's estimate: 100
eval cases across 3 runs with gpt-5-mini is about $0.07, plus a margin. It isn't funded,
and Juku's $5 can't be used for it.
