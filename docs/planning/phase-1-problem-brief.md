# Phase 1: Problem Brief

**Project:** a personal finance tracker that lives in WhatsApp, for Arthur
and a few invited people in Indonesia. You send "makan 25rb" and it is
logged, in exact rupiah, with an undo button. It is the ninth portfolio
piece.

The working name is the repo's, `whatsapp-finance-tracker`. The name shown
in WhatsApp is still open (see the end).

## Problem

**For the users:**
- **Tracking dies from friction.** Opening an app after every purchase is
  the step people skip. WhatsApp is already open.
- **Indonesian money is written informally.** "25rb", "1,5jt", "goceng"
  and "2jt500" are normal. General apps want a number in a form.
- **A tracker that is sometimes wrong is worthless.** If one amount is off
  by a factor of 1,000, nobody trusts the monthly total again.

*To verify in the dogfood slice, not assumed:* that Arthur keeps logging
after the first two weeks. If he doesn't, the friction story is wrong.

**For the portfolio:**
- Earlier projects let models read data and propose. None lets a model
  near a record that must be exactly right and that a person relies on.
- A reviewer can't yet see how Arthur handles money: exact arithmetic,
  duplicate deliveries, undo, and a model that isn't trusted.
- Everything else in the portfolio is a web app or a backend. None runs on
  a messaging channel, with its terms, prices and delivery failures.

## Users

| Party | Who | Wants |
|---|---|---|
| **Arthur (builder, first user)** | Logs his own money | A tracker he actually keeps using |
| **Invited people (a few)** | Up to 4 adults Arthur invites by number, so 5 people in all (ADR-001) | To log and see their own money, privately |
| **Reviewer / hiring team** | Japan-style employers | Proof of reliability: exact money, tests, measured accuracy, honest limits |

## Positioning: what makes this one different

The idea is common, so the idea isn't what's being shown. What is:

1. **The model never touches the number.** Rules parse common messages at
   no cost. A model is only a fallback. It may point at the words in the
   message that hold the amount, but code turns those words into an
   integer, and a person confirms before anything is written.
2. **Money is exact and the history can't quietly change.** Integer rupiah,
   no floats. Undo marks an entry void instead of erasing it. A duplicate
   delivery from Meta can't log an entry twice.
3. **Accuracy is measured, not claimed.** A labelled corpus of Indonesian
   messages gives the rules' coverage and the count of wrong amounts, with
   a held-out set, in CI, at $0.
4. **The terms were read.** It uses Meta's official API, after checking
   Meta's own terms and prices. Two popular agent frameworks were rejected
   because of what their own docs say about how they reach WhatsApp.

## Goals and how they're measured

| # | Goal | Target | Measured by |
|---|---|---|---|
| G1 | No wrong amount is written | **0** on the held-out corpus | Corpus test in CI |
| G2 | Common messages need no model | Rules parse ≥ 85% of the held-out corpus (first guess, set after slice 1) | Corpus test in CI |
| G3 | Every write is safe to repeat | A replayed webhook never adds a second entry | Test, and a restart test |
| G4 | No one sees anyone else's data | Every cross-user attempt returns nothing, at the database level too | Isolation suite |
| G5 | Summaries are right to the rupiah | Exact match against fixtures | Fixture tests, mutation-checked |
| G6 | It fits the small shared server | App under its memory cap (ADR-018) | Measured, then published |
| G7 | It costs next to nothing | Rp0 from the model in v1; Meta costs within the cap (ADR-012) | Monthly count from delivery records |
| G8 | Arthur keeps using it | Logs on at least 20 of the first 30 days | Rows, Arthur's only |

## Non-goals (v1)

- **Photo receipts.** Each photo would go to Meta and a model provider, and
  receipts can show card details (ADR-010). v2 at the earliest.
- **Bank or e-wallet links, and importing statements.**
- **Moving money, paying bills, or any financial advice.** It records what
  you tell it and adds it up.
- **Multiple currencies.** Rupiah only.
- **WhatsApp groups and shared household budgets.** One person, one ledger.
- **A web dashboard.** The chat is the interface.
- **Public sign-up.**
- **Free-form spending questions in chat.** They come after v1 through
  MCP-backbone (ADR-016).

## Decisions (settled with Arthur, 2026-10-30)

1. **Users:** Arthur plus a few invited people.
2. **v1 scope:** text logging with undo; categories; summaries on request;
   budgets with a monthly push.
3. **Framework:** `pywa` on Meta's official Cloud API, with FastAPI,
   PydanticAI for the fallback, and Postgres with SQLAlchemy 2 and Alembic.

Everything else in Phase 5 is a recommendation until Arthur agrees.

## Open questions

See the end of [Phase 5](phase-5-architecture-decisions.md#open-questions).
The two that could change the design:
- whether financial entries count as "Prohibited Information" under
  section 4.2 of Meta's terms;
- whether Meta accepts an individual, not a company, as the business.

## Cost of doing nothing

Nothing breaks. Arthur keeps not tracking his spending, and the portfolio
keeps lacking a project about exact records and a messaging channel. This
is why the project waits its turn: Juku's deploy and other server work
come first.
