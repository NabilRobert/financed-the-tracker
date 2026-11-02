# Slice Plan and Cost Model

## Prerequisites (outside this repo)

| # | Prerequisite | Needed for | Who |
|---|---|---|---|
| P1 | Arthur OKs Phases 1, 2 and 5 | Slice 1 | Arthur |
| P2 | The GitHub repo exists | Pushing anything | Arthur |
| P3 | A Meta developer app and its test number | Spike 2, slice 3's live check | Arthur |
| P4 | Juku is deployed and the server is ready for this data, with encrypted backups verified | Slice 5 | Arthur |
| P5 | A subdomain for the webhook | Slice 5 | Arthur |
| P6 | A decision on section 4.2 and how to sign up (Phase 5, open questions) | Slice 5, real data | Arthur |
| P7 | A dedicated SIM and a payment method at Meta | Slice 6 | Arthur |
| P8 | Sumopod's data terms read, and a top-up separate from Juku's $5 | Slice 7 | Arthur |

## Spikes (local, $0)

| # | Question | Method | Pass | Time |
|---|---|---|---|---|
| 1 | How does pywa behave at the edges? | Read and run its webhook path with signed fixtures: signature comparison, duplicate default, handler timing, handler errors, async | Traps in ADR-004 confirmed or corrected; bugs reported upstream by Arthur | 1 h |
| 2 | What does the test number allow? | Read Meta's dashboard; send a reply with buttons | Recipient limit known; buttons work | 30 min |
| 3 | Does it fit? | Idle and busy memory of the app with pywa, in a capped container | Under 256 MB, or the cap is revised | 30 min |
| 4 | Is local storage available for Indonesia? | Read Meta's registration docs for `data_localization_region` | Answered from Meta's docs | 15 min |
| 5 | Will Meta class the summary as utility? | Submit the template with the test account | Category known; ADR-012 updated | 15 min |

## Slices

**Evaluation-driven order, as in Juku:** the tests and corpus come first and
fail. Then the code is written until they pass. Each new test is
mutation-checked once. Each slice updates `docs/guide.md`.

| Slice | Delivers | Done when | Cost |
|---|---|---|---|
| **1. Parser and corpus** | Repo scaffold, README, CI, complexity rules; `parse_amount`; `parse_message`; the corpus | See below | $0 |
| **2. Ledger** | Schema and migrations; row-level security; write, void, categories, keywords; summaries and budgets in SQL; a test that no money is held in a float | Fixture summaries match to the rupiah; the isolation suite passes, mutation-checked | $0 |
| **3. WhatsApp channel** | pywa handlers; signature check; message-id idempotency; allow-list; consent; buttons; commands; `bantuan` | Signed fixtures cover every path; replays add nothing; a forged request is refused; one live round trip on the test number through a local tunnel | $0 |
| **4. Privacy and operations** | `hapus data saya`; `ekspor`; card-number refusal; reply counter and cap; send retries (R3); message outcomes; spans; `/healthz`; no-content tests for spans and alerts | Deletion leaves zero rows; the cap fires in a test; no message text in logs or spans | $0 |
| **5. Deploy, gate, then dogfood** | Container; Caddy; backups included; probes through the systems-monitoring session; [the gate](#the-real-data-gate); then Arthur alone for 2 weeks | Alerts fired on purpose and received; a restart loses nothing; the gate passes; misses are counted and turned into corpus cases | $0 |
| **6. Invite and monthly push** | Dedicated number; the approved template; the monthly job; up to 4 invitations, sent by Arthur | The push can't send twice; deletion checked with a real invited person's agreement | Under Rp3,000 a month |
| **7. Model fallback (Should)** | The typed proposal; validation; the Inspect task | Zero invented amounts in the eval; Arthur's OK for each paid run | About $0.10, not budgeted yet |
| **8. Evidence and write-up** | Results from records; JOURNEY; portfolio entry (with the portfolio session) | Every number traces to a file; a privacy check passes | $0 |

## The live check

Slice 3's code is finished, and proved with signed fixtures only. Its one live round
trip waits for P3: a Meta developer app, its test number, and its secrets in a local
`.env`, plus a tunnel. Only invented messages go through it.

It has to confirm what the fixtures assume about Meta's payloads:

- every message has a `user_id` in `contacts`, or pywa can't build the update and we
  answer 500;
- `from_user_id` is on the message, and how a batch pairs messages with contacts;
- the number-change message carries `previous_user_id` and `user_id` as ADR-008 read
  them, and arrives without `contacts`;
- buttons work, and sending to a BSUID works.

## Security done-when for each slice

Security comes first ([ADR-020](phase-5-architecture-decisions.md#adr-020-security-comes-first)).
A slice isn't done until these pass as well, each shown by a test or a command.
T numbers are in the [threat model](threat-model.md).

**Already done:** the secret scan and dependency audit in CI (T30, T31), and
slice 2's isolation, least grants and no-delete rules (T3, T7 to T9, T20).

| Slice | Security done-when |
|---|---|
| **3** | Signature checks fail closed: no app secret stops startup, and every bad signature gets 401 or 403 and writes nothing (T1, T2). Unknown senders get nothing (T4). Rate limits work (T5). No token in logs (T6). Forged button payloads fail (T10). Suspension works (T11). Recycled numbers get nothing (T12). No-content tests pass on every path, failures included (T14). Consent is recorded with its notice version, and nothing is stored before it (T17). The phone number is dropped once the BSUID is known (T21). The notice's text is pinned (T18). Only invented messages go through the test number |
| **4** | Card numbers are refused before any write (T13). No-content tests cover spans and alerts (T14). The purge job meets PR6 (T15). `hapus data saya` leaves zero rows through one narrow database function, and needs the tap (T11, T16). `ekspor` carries only that person's data (T19). The reply cap fires (T5) |
| **5** | Non-root, capped container with no owner login in its environment (T21). Postgres can't be reached from outside (T22). Token and secret rotation rehearsed (T23). Backups are encrypted, and a restore is tested and timed (T25, T26). A deletion survives a restore (T27). Replay survives a restart (T3). Then the gate |
| **6** | The monthly job has its own least-privilege login (T22). The template holds only the total (T19). The gate's checks are re-run on the deployed commit before the first invitation, and each invited person's consent is recorded |
| **7** | The provider's written terms are read, and Arthur agrees (T29). The injection cases pass, and zero invented amounts (T28). The notice is updated, and existing people consent again, before it's turned on |
| **8** | The privacy check covers every published number, screenshot and log. Nothing real, and no live finding, reaches the public repo |

## The real-data gate

**No real person's data, Arthur's included, enters the system until every line
here passes on the deployed commit.** Until then, only invented messages are
used. Arthur signs it, in writing. A failing control can't be accepted to save
time.

| # | Must be true | Proof | Today |
|---|---|---|---|
| 1 | Webhook signatures fail closed, including pywa's missing-secret case | Tests, and a forged request against the deployed app | Planned, slice 3 |
| 2 | Row-level security holds on the deployed database | The isolation suite passes, and is mutation-checked | Passes in CI; deployed run in slice 5 |
| 3 | No message text, names, numbers or amounts in logs, spans or alerts | No-content tests, and a sentinel message searched for on the server | Planned, slices 3 and 4 |
| 4 | Backups are encrypted, and a restore works | A timed restore into an empty database | Planned, slice 5 |
| 5 | Retention and `hapus data saya` work, and deletion survives a restore | Tests, and one real run with invented data | Planned, slices 4 and 5 |
| 6 | Rate limits and the reply cap work | Tests | Planned, slices 3 and 4 |
| 7 | Secret scan and dependency audit pass | CI, green on the deployed commit | In CI since this branch |
| 8 | Database logins have the least privilege | Grant tests, and a check of the app's environment | App login done; the rest in slices 5 and 6 |
| 9 | Consent is recorded, with its notice version | Tests | Planned, slice 3 |
| 10 | An adversarial pass is done, with every finding fixed | A written pass over bad input, failure paths and restarts, plus the threat model's Planned rows | Planned, slice 5 |
| 11 | The independent review is done, with every finding fixed | Its report, kept private until fixed ([who does it](threat-model.md#independent-review)) | Planned, slice 5 |
| 12 | The server is ready for this data (P4) | Confirmed by the server work | Open |
| 13 | **Section 4.2 is decided (P6)** | Arthur's written decision | Open |
| 14 | The threat model is current | Every row is Done, or is listed under "What we accept" | Open |

## Slice 1 in detail: the first slice

**Scope:** pure Python, no I/O. It needs nothing from Meta, Sumopod or the
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

Full tables are in [ADR-012](phase-5-architecture-decisions.md#adr-012-costs-and-a-hard-cap).

| Item | Monthly, 5 people at 5 entries a day |
|---|---|
| Inbound messages | Rp0 |
| Replies (~850, within the free 1,000) | Rp0 |
| Monthly push (5 utility templates) | Rp1,783, plus any tax |
| Model | $0 in v1; about $0.003–0.017 if on |
| Hosting | Rp0: the existing server |
| Dedicated SIM | A small top-up to keep it active; not priced yet |
| **Hard cap on paid replies** | Rp25,000 |

**Nothing in slices 1 to 5 costs money.** Slice 7's estimate: 100 eval cases
across 3 runs with gpt-5-mini is about $0.07, plus a margin. It isn't
funded, and Juku's $5 can't be used for it.
