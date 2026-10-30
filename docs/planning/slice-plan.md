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
| **4. Privacy and operations** | `hapus data saya`; `ekspor`; card-number refusal; reply counter and cap; spans; `/healthz`; no-content tests | Deletion leaves zero rows; the cap fires in a test; no message text in logs or spans | $0 |
| **5. Deploy and dogfood** | Container; Caddy; backups included; probes through the systems-monitoring session; Arthur alone for 2 weeks | Alerts fired on purpose and received; a restart loses nothing; misses are counted and turned into corpus cases | $0 |
| **6. Invite and monthly push** | Dedicated number; the approved template; the monthly job; up to 4 invitations, sent by Arthur | The push can't send twice; deletion checked with a real invited person's agreement | Under Rp3,000 a month |
| **7. Model fallback (Should)** | The typed proposal; validation; the Inspect task | Zero invented amounts in the eval; Arthur's OK for each paid run | About $0.10, not budgeted yet |
| **8. Evidence and write-up** | Results from records; JOURNEY; portfolio entry (with the portfolio session) | Every number traces to a file; a privacy check passes | $0 |

## Slice 1 in detail: the first slice

**Scope:** pure Python, no I/O. It needs nothing from Meta, Sumopod or the
server, so it can start the moment Arthur OKs the plan.

**Delivers:**
- `pyproject.toml` (uv), ruff with the complexity rules, pyright strict;
- `.github/workflows/ci.yml`, a README with a layout section, `docs/guide.md`;
- `tracker/parsing/amount.py`: `parse_amount(text) -> int | Ambiguous | None`;
- `tracker/parsing/message.py`: `parse_message(text) -> ParsedEntry | ...`;
- `tests/corpus/dev.jsonl` and `tests/corpus/holdout.jsonl`, about 300
  hand-written messages in all, none from a real person;
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

**Branch:** `feat/parser`, from `main`, once `docs/planning` is merged.

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
