# Phase 5: Architecture Decisions

Short ADRs, 2026-10-30. **Settled** ones were decided with Arthur. He accepted
ADRs 005 to 018 as recommended on 2026-11-02, with the questions under "For
Arthur" still open. The summary table and the first slice are at the end.

```
Person on WhatsApp ─► Meta Cloud API ─► Caddy ─► tracker (FastAPI + pywa)
                                                   │
                         rules parser ─────────────┤  (pure code, $0)
                         model fallback ───────────┤  off in v1; proposes only
                                                   ▼
                                     code validates and writes ─► Postgres
                                                   │              (own database, own login)
monthly job (cron + Typer) ──── template ──► Meta  │
                                                   ▼
                     traces ─► agent-observability    probes ─► systems-monitoring
```

## Reuse map (the folder's "reuse before building" rule)

Arthur's repos were searched first. No Python repo here has a rupiah parser
or a ledger. Two TypeScript pieces touch rupiah: a one-line display formatter,
and `readings()` in Pitstop's `ranking.ts`, which checks figures in float and
knows no slang. Neither fits, so there is nothing to depend on.

| Part | Reused | Built (and why) |
|---|---|---|
| WhatsApp webhooks and messages | **pywa** (ADR-004), approved in Juku's Phase 5 | Handlers only |
| HTTP, validation, data, CLI, tests | Arthur's standard stack | — |
| Model calls, if on | **PydanticAI**, set up as in Juku | The typed proposal |
| Evals | **Inspect AI** for the model; pytest for the rules (ADR-014) | The corpus |
| Traces | **agent-observability**, OpenTelemetry SDK (ADR-015) | Span names only |
| Alerts | **systems-monitoring** (ADR-015) | Its config entries, made in its repo |
| Retries | **tenacity** | — |
| "The model proposes, code decides" | **Pitstop's** pattern, as in Juku | — |
| Spending questions (later) | **MCP-backbone**, **mcp-security-gateway** (ADR-016) | A scoped view |
| The Indonesian money parser | Tried in slice 1: `price-parser` reads `25rb` as 25 and `2jt500` as 2; `number-parser` has no Indonesian. Nothing fits | **Built:** this is what the project demonstrates |
| The ledger | Firefly III and Actual Budget were considered (ADR-005) | **Built:** small, and exactness is the subject |

## ADR-001: Arthur plus a few invited people (settled)

**Decision:** up to 5 people in all, adults, added by Arthur by number.

**Why:** it gives real use beyond Arthur, while staying inside Meta's free
tier at normal use (ADR-012) and needing no public sign-up.

**What it costs:** consent, deletion and isolation between people are now
required (ADR-009, ADR-010). Personal financial data is a specific category
in Indonesia's data protection law (UU PDP, Article 4). The law's exception
for personal or household activity (Article 2) may not cover friends, so
the plan treats the law as applying. This is engineering judgement, not
legal advice.

**Rejected:**
- *Arthur only:* the simplest and safest option, but Arthur chose real
  sharing.
- *Public sign-up:* a verified business, full compliance work and paid
  messages; too much for the small server.

## ADR-002: v1 scope (settled)

**Decision:** text logging with undo; categories; summaries on request;
budgets with a monthly push. Details in Phase 2.

**Rejected:** photo receipts in v1 (ADR-010).

## ADR-003: Meta's official Cloud API (settled); what the terms say

**Decision:** Meta's WhatsApp Cloud API only.

**The general-purpose AI ban doesn't catch this project.** Section 4.7 of
Meta's platform terms (updated 2026-09-23) bars AI providers when AI is the
primary function offered, and Meta alone decides what that means. Here the
function is a ledger, and it runs in v1 with no model at all, so AI is
secondary at most. Section 4.7 also forbids letting WhatsApp data train
models, which limits the provider choice (ADR-007).

**Three risks remain, and none can be closed from here:**

| Risk | What the source says | Handling |
|---|---|---|
| **Section 4.2, Prohibited Information** | A business may not give Meta data that law or regulation protects or restricts. It also says Meta doesn't promise to meet heightened confidentiality duties in financial services | Unclear whether "makan 25rb" counts. It's a person's own note, not a bank record, but it is personal financial data. **Arthur decides; a lawyer's read is the real answer** |
| **"Company"** | The terms address a company or legal entity and those acting for it. They don't mention individuals or sole proprietors, and neither forbid nor allow them | Arthur signs up as himself and declares the use honestly |
| **Business Messaging Policy** | Don't request or share full card, account or ID numbers. Automated replies need a path to a human. Opt-in is required | Handled: PR3, `bantuan`, consent. The policy's financial bans target loans and debt collection, not trackers |

**Rejected:**
- *Twilio for WhatsApp:* a second account and a fee on top of Meta's.
- *Unofficial WhatsApp Web links (Baileys):* see ADR-004.
- *Telegram:* no approval or per-message cost, and the obvious escape if
  Meta becomes a problem. Rejected because Arthur asked for WhatsApp. The
  parser and ledger know nothing of the channel, so that escape stays cheap.

## ADR-004: pywa with FastAPI (settled)

**Decision:** `pywa` 4.5.x (MIT, released 2026-09-24) on Python 3.12, with
FastAPI, Pydantic, SQLAlchemy 2 and Alembic, Typer, pytest, uv, ruff and
pyright, and Postgres. It's Arthur's standard stack, and Juku already chose
pywa for its WhatsApp channel.

**pywa's traps, from reading its server code, with the guards:**
- With no `app_secret`, it turns signature checks **off** and only logs a
  warning. So our startup refuses to run without one (R1).
- Its duplicate check lives in memory and is lost on restart. So the
  database's unique key on Meta's message id is what counts (L5).
- Handler errors are logged and Meta still gets a 200, so a failure can
  vanish. So failures are recorded and alert (R4).
- Handlers appear to run before the response. Spike 1 confirms this, and
  that our handler is fast enough.

**Risk:** PyPI shows one maintainer account. It's a thin layer over Meta's
HTTP API, so moving to httpx later would mean replacing the webhook and
send code, not the parser or ledger.

**Rejected:**

| Option | What was checked (primary source unless marked) | Verdict |
|---|---|---|
| **OpenClaw** | Its WhatsApp docs say it runs on WhatsApp Web through Baileys, with no Cloud API mentioned. Three High advisories (GHSA-hjr6-g723-hmfm, GHSA-9969-8g9h-rxwm, GHSA-575v-8hfq-m3mc; CVSS 8.8, 8.8, 8.4) were fixed in 2026.6.6 on 2026-06-30, and its list has a High "WhatsApp login tool" advisory from 2026-09-11. That those three chain from one WhatsApp message is **secondary** (a Cyber Security News write-up); the advisories don't mention WhatsApp, and they have no CVE ids | Rejected: unofficial link, and a broad exec-capable runtime next to money data |
| **Hermes Agent** | Its docs say its bridge is Baileys, emulating WhatsApp Web, not the official API; they warn of possible restrictions, advise a dedicated number, say protocol updates can break it, and name the Cloud API as the no-ban path. Self-improving skills come from its own description | Rejected: same link, and self-written skills can't be audited for money. It was the fallback only if this were a personal tool (ADR-001 settled otherwise) |
| *Plain httpx* | — | Rejected: we'd hand-write signature checks and payload models the framework already has |

## ADR-005: Build a small ledger; don't adopt a finance app

**Options considered:**

| Option | Verdict |
|---|---|
| **Own tables in Postgres** | **Chosen.** A few tables, and exactness is what's being shown |
| Firefly III | Rejected: AGPL-3.0, a PHP app, 512 MB or more by one guide (secondary), and its features aren't needed |
| Actual Budget | Rejected: a local-first app with its own sync model; a poor fit behind a chat bot. Memory not checked |

## ADR-006: Money is whole rupiah in a BIGINT, and the ledger only grows

**Decision:**
- `amount_rupiah BIGINT`, always above 0, with direction in its own column.
- Python `int` from `Decimal`, never `float`.
- Undo sets `voided_at`. A correction is a void plus a new entry.
- `hapus data saya` is the only hard delete.

**Why:** rupiah has no smaller unit in everyday use, so integers are exact.
Voiding keeps every change visible and makes undo safe to repeat.

**Rejected:**
- *`NUMERIC` or `Decimal` columns:* exact, but it allows fractions that
  don't mean anything here.
- *Floats:* inexact.
- *A double-entry ledger:* the right model for accounts that transfer to
  each other, which a one-person spending log doesn't have.

## ADR-007: Rules first; the model only proposes, and it's off in v1

**Decision:**
- Rules handle the message first. Sure means write, and undo is one tap.
- Ambiguous means ask.
- The fallback, when on, is a PydanticAI agent with no tools. Its output is
  `amount_text`, `direction`, `category` and `description`.
- Code requires `amount_text` to be an exact substring of the message and
  runs it through the same parser. **The model can't invent a number.**
- A person confirms every proposal.

**Off in v1, because:**
- the balance is $0, and the $5 there is reserved for Juku's slice 7;
- Sumopod's data terms could not be found (its pages gave no policy), and
  section 4.7 of Meta's terms forbids letting this data train models.

**Turning it on takes three things:** the provider's written terms read,
Arthur's OK on a separate top-up, and the eval in slice 7.

**Rejected:**
- *Model first:* costs on every message, and sends everyone's spending to a
  provider.
- *The model supplying the amount:* one bad number breaks the whole point.
- *Free-tier model APIs:* those that use inputs for training conflict with
  section 4.7.
- *A local model:* too heavy for a small shared server.

## ADR-008: Identity comes from the signed webhook, keyed on BSUID

**Decision:** `person` is keyed on Meta's business-scoped user id (BSUID).
The phone number is optional.

**Why:** Meta's docs say phone numbers may be left out of webhooks for
people with usernames, and treat them as optional. A number change arrives
as a system message with `previous_user_id`, which links the old record.

**Rejected:** *keying on phone number:* Meta warns against relying on it.

## ADR-009: Isolation in the code and in the database

**Decision:**
- Every query takes the person from the verified webhook.
- Postgres row-level security also limits each transaction to one person,
  through `SET LOCAL`, on a login that doesn't own the tables.
- The monthly job uses its own login, which can read what the push needs.
- An isolation suite tries to cross people and is mutation-checked.

**Why:** a forgotten `WHERE` is the classic bug. This is personal financial
data, so one layer isn't enough. It costs one migration.

**Rejected:**
- *Code scoping alone:* one missed filter exposes everyone.
- *One database per person:* too heavy for five people.

## ADR-010: Privacy: what each party can see

| Who | Sees | Keeps |
|---|---|---|
| **Meta** | All message text, in the clear: Cloud API decrypts it before forwarding, and holds the keys | Up to 30 days, per its docs |
| **Model provider** (only if the fallback is on) | One message, no name or number | Unknown until its terms are read |
| **Arthur's server** | Entries and budgets | Until deleted |
| **Arthur as operator** | Could read everything in the database | Stated in the consent notice |
| **Logs, traces, metrics** | No content | — |

**Decision:**
- The consent notice says all of this in plain Indonesian.
- All stored data stays on Arthur's server. No secrets in the repo.
- No photo receipts in v1: they'd add Meta and a model provider to images
  that can show card details, and Meta's policy bars asking for those.
- **Recommended:** turn on Meta's local storage for Indonesia if it's
  available, so messages at rest stay in-country after up to 60 minutes of
  processing elsewhere. Third-party docs list Indonesia; Meta's own list
  wasn't reachable, so spike 4 checks. It can only be set on an unregistered
  number, so it comes before registration.

**Rejected:** *claiming end-to-end encryption:* it isn't true on Cloud API.
*Storing raw messages to help debugging:* more exposure for little gain.

## ADR-011: Budget warnings travel with the reply; the push is a template

**Decision:**
- A warning goes in the same message as the entry's reply. It's inside the
  24-hour window, needs no template and doesn't hit the 6-second limit.
- The monthly summary is the one message that starts on its own, so it's an
  approved utility template. If Meta classes it as marketing, the price
  changes (ADR-012).
- A Typer command run by cron sends it, as Juku runs its jobs.
- Unique rows stop duplicates after a crash or rerun.

**Rejected:**
- *A template for every warning:* costs money for no benefit.
- *APScheduler:* a scheduler in the app dies with the app, and one monthly
  job doesn't need it.

## ADR-012: Costs, and a hard cap

**Meta's prices** ([calculator](https://whatsappbusiness.com/products/platform-pricing/#rates),
Indonesia, IDR, read 2026-10-30):

| Message | Price |
|---|---|
| Marketing | Rp586.33 |
| Utility | Rp356.65 |
| Authentication | Rp356.65 |
| Service, past the free tier | Rp356.65 |

- Inbound messages are free: Meta charges for delivered business messages.
- Each number gets 1,000 free service messages a month. Since 2026-10-01,
  Meta charges from the 1,001st, and utility inside the window is billable.
- Meta's marketing page still says service is free. The developer docs and
  the calculator are newer and stricter, so this plan uses those.
- A third party reports 11% VAT on top. Meta's page doesn't confirm it.

**Monthly bill** (5 people; replies are the driver):

| Use | Replies | Over the free tier | Meta cost |
|---|---|---|---|
| 5 entries a day each | ~850 | 0 | **Rp0** |
| 10 entries a day each | ~1,700 | ~700 | ~Rp250,000 |
| Monthly push, utility | 5 templates | — | Rp1,783 |
| Monthly push, if classed marketing | 5 templates | — | Rp2,932 |

Earlier estimates of "about 3 active users" assumed 10 entries a day; at 5
a day, all 5 people fit.

**Model, if on** (estimated tokens: 400 in, 60 out; prices from the
harness's `prices.yaml`, checked 2026-10-23):

| Model | One call | 75 calls a month (10% of 750) |
|---|---|---|
| gpt-5-nano | ~$0.00004 | ~$0.003 |
| gpt-5-mini | ~$0.00022 | ~$0.017 |

**Decision:**
- Count delivered replies from status webhooks. Alert Arthur at 800.
- A monthly paid limit, `MONTHLY_PAID_REPLY_CAP_RUPIAH`, default Rp25,000
  (about 70 paid replies). Past it, entries are still logged but without an
  echo, after one notice. Anything needing confirmation is declined.

**Rejected:** *no cap:* a heavy month costs Rp250,000, more than the rest of
the project. *Stopping logging at the cap:* losing records is worse than
losing the echo.

## ADR-013: Reliability

**Decision:**
- The entry and its message id commit in one transaction, before any reply.
- A repeat delivery returns 200 and does nothing.
- Sends retry with tenacity, respecting the 6-second limit.
- A restart loses nothing, and the break-it pass tests it.

**Rejected:** *a queue (Redis, Celery):* another service on a small server,
for a handful of messages an hour.

## ADR-014: Measuring accuracy

**Decision:**
- **Rules:** a pytest corpus in CI, about 300 hand-written messages with
  exact expected output, split into dev and held-out sets. It reports
  coverage, wrong amounts and false writes. It costs nothing, so it can
  gate every commit.
- **Model:** an Inspect AI task over the messages the rules can't parse,
  with a scorer that runs the code's validation. Paid, so slice 7 only.
- Arthur's own unparsed messages can enter the corpus after he rewrites
  them. Nobody else's do.

**agent-eval-harness doesn't fit.** Its targets are MCP servers, and it
grades agents calling tools. A parser with no tools would need the harness
bent around it. It does fit ADR-016.

**Risk:** a corpus Arthur writes matches how Arthur writes. Dogfood misses
are counted and reported beside the corpus numbers.

## ADR-015: Tracing and monitoring

**agent-observability fits.** The OpenTelemetry SDK sends to its Jaeger,
opt-in by endpoint. Spans cover webhook, parse, write and send, following
`docs/conventions.md`: `service.name`, no content, and `error.type`. The
parser path (`rules`, `model`, `failed`) is an attribute. Amounts never are.

**systems-monitoring fits, and needs no new code here:**
- a blackbox probe of `/healthz`;
- container logs through Vector;
- alerts through Alertmanager to Telegram;
- a dead-man alert when no webhook has arrived for a day, since a silent
  Meta failure looks like a quiet day. Arthur's own use makes that safe.

Those changes are made by that repo's session, at deploy time.

**Rejected:** *Grafana for this:* Juku's instance is its own, and a chat
summary needs no dashboard.

## ADR-016: MCP-backbone and the gateway aren't in v1

**Decision:** v1 has no agent tools, so neither is needed. Adding them would
be a dependency with nothing to do.

**Where they fit after v1:** read-only spending questions ("bulan ini
paling boros di mana?").
- **MCP-backbone** runs the validated, audited, read-only SQL.
- Its login sees only a view that row-level security limits to one person.
- **mcp-security-gateway** goes in front if an agent, not just Arthur,
  makes the calls. Juku found it dropping `_meta`, so check that fix first.
- **agent-eval-harness** then measures it, since that's an agent using MCP.
- The risk to settle first: MCP-backbone keys belong to a workspace, not a
  person, so per-person scoping must come from the view. It needs its own
  planning pass.

## ADR-017: Test number first, then a dedicated SIM

**Answers Arthur's question: a personal number won't do, and the test
number covers only part of the project.**
- **Personal number:** Meta's docs say a number already on WhatsApp can't
  be registered unless it's deleted from WhatsApp first. Arthur would lose
  his own account on it.
- **Test number:** free, no payment method needed, created automatically.
  Good for development and Arthur's dogfooding. Its recipient limit isn't
  on the pages read (5 from memory, unconfirmed), so spike 2 reads it in
  the dashboard.
- **Dedicated SIM:** needed before inviting people. It needs a payment
  method on file for templates. A new portfolio can register two numbers
  and has a messaging limit of 250, which is plenty.

**Rejected:** *a WhatsApp Business app number:* Meta's onboarding covers
those, but a SIM used only for this keeps the bot's number separate.

## ADR-018: Hosting and capacity

**Decision:**
- One container: FastAPI, one worker, `pydantic-ai-slim` only when the
  fallback is on.
- **A hard memory cap of 256 MB**, target idle under 150 MB (a guess;
  spike 3 measures it). CPU limit 0.25.
- Its own database and login in the server's existing Postgres, so no new
  database process.
- Caddy gives a subdomain for the webhook.
- Nothing goes on the server until Juku is deployed and the server is
  ready for this data, with encrypted backups verified (PR4).

**Why it fits:** Juku's capacity check left room to spare, and a few
hundred messages a day is almost no load. CPU, not memory, is what to
watch.

**Rejected:**
- *A new server:* money, for no need.
- *Serverless:* a cold start on every message, and it moves financial data
  off Arthur's server.

## Decisions

| # | Decision | Rejected | Status |
|---|---|---|---|
| 001 | Arthur plus a few invited people, up to 5 in all | Arthur only; public | **Settled** |
| 002 | Logging, undo, categories, summaries, budgets, monthly push | Photos | **Settled** |
| 003 | Meta's official Cloud API | Twilio, Baileys, Telegram | **Settled** |
| 004 | pywa, FastAPI, standard stack, Postgres | OpenClaw, Hermes, plain httpx | **Settled** |
| 005 | Own small ledger | Firefly III, Actual Budget | **Accepted** |
| 006 | BIGINT rupiah; void, don't delete | NUMERIC, floats, double-entry | **Accepted** |
| 007 | Rules first; model proposes substrings only; off in v1 | Model first, model-supplied numbers, free-tier APIs, local model | **Accepted** |
| 008 | BSUID as the user key | Phone number | **Accepted** |
| 009 | Scoped code plus row-level security | Code only, one database each | **Accepted** |
| 010 | Consent notice, minimal storage, no photos; local storage if available | Raw message storage | **Accepted** |
| 011 | Warnings in replies; monthly template by cron | Template per warning, APScheduler | **Accepted** |
| 012 | Count replies; Rp25,000 monthly cap; keep logging past it | No cap, stop logging | **Accepted**; the Rp25,000 figure stays open |
| 013 | Commit before replying; no queue | Redis, Celery | **Accepted** |
| 014 | pytest corpus for rules; Inspect AI for the model | agent-eval-harness | **Accepted** |
| 015 | agent-observability and systems-monitoring | A new Grafana | **Accepted** |
| 016 | MCP-backbone and gateway after v1 | Adding them now | **Accepted** |
| 017 | Test number, then a dedicated SIM | Personal number, Business app number | **Accepted** |
| 018 | One capped container, existing Postgres, after Juku's deploy | New server, serverless | **Accepted** |

## The first slice

**Slice 1: the parser and its corpus.** Pure Python: no WhatsApp, no
database, no model, $0. See the [slice plan](slice-plan.md).

## Open questions

**For Arthur:**
1. **Section 4.2:** accept the risk that entries could count as Prohibited
   Information? Slices 1 to 4 are safe to build either way; the answer
   gates real use.
2. **The Meta account:** sign up as an individual and declare the use?
3. **The monthly cap:** is Rp25,000 right?
4. **Name:** what shows in WhatsApp? Meta may review display names.

**For the spikes:** pywa's handler timing and comparison method; the test
number's limit; memory; local storage for Indonesia; template category.

**For later:** Sumopod's data terms, and the fallback's budget.

## Sources (read 2026-10-30)

**Primary:**
[Meta platform terms](https://www.facebook.com/legal/Meta-Terms-for-WhatsApp-Business-Platform) ·
[Business Messaging Policy](https://whatsappbusiness.com/id/policy/) ·
[pricing docs](https://developers.facebook.com/docs/whatsapp/pricing/) ·
[rate calculator](https://whatsappbusiness.com/products/platform-pricing/#rates) ·
[Cloud API data privacy](https://developers.facebook.com/docs/whatsapp/cloud-api/overview/data-privacy-and-security/) ·
[local storage](https://developers.facebook.com/documentation/business-messaging/whatsapp/local-storage.md) ·
[phone numbers](https://developers.facebook.com/docs/whatsapp/cloud-api/phone-numbers) ·
[BSUIDs](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/) ·
[OpenClaw WhatsApp docs](https://docs.openclaw.ai/channels/whatsapp) ·
[OpenClaw advisories](https://github.com/openclaw/openclaw/security/advisories) ·
[Hermes WhatsApp docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/whatsapp) ·
[pywa on PyPI](https://pypi.org/project/pywa/) ·
[pywa server code](https://github.com/david-lev/pywa/blob/master/pywa/server.py) ·
[UU 27/2022](https://jdih.kemenkeu.go.id/download/46c17b53-4a45-411c-9b76-c6799e5769fc/UU_Nomor_27_Tahun_2022%20Perlindungan%20Data%20Pribadi.pdf)

**Secondary, used only where marked:**
[Cyber Security News on the OpenClaw chain](https://cybersecuritynews.com/whatsapp-message-openclaw-remote-access-tool/) ·
[Infobip on local storage](https://www.infobip.com/docs/whatsapp/compliance/data-privacy) ·
[ChatMaxima on Indonesian VAT](https://chatmaxima.com/whatsapp-api-pricing/indonesia/) ·
[Firefly III comparison](https://beancount.io/blog/2026/07/26/firefly-iii-vs-actual-budget-self-hosted-open-source-budgeting-guide)
