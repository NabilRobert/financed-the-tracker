"""Summaries match a hand-worked fixture to the rupiah (G5, S1)."""

from datetime import UTC, date, datetime

import pytest
from ledger_helpers import add_entry, income, new_person, spending
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.entries import void_entry
from tracker.ledger.scope import person_transaction
from tracker.ledger.summaries import CategoryTotal, Period, month_period, summarise, today_in_jakarta, week_period
from tracker.parsing.message import Direction

NOVEMBER = month_period(2026, 11)


@pytest.fixture
async def person(owner_engine: AsyncEngine, app_engine: AsyncEngine) -> int:
    """November by hand: Makan 25,000 + 12,500 = 37,500. Transport reaches 3 billion, past an int32."""
    person_id = await new_person(owner_engine)
    fixture = [
        (spending(25_000), date(2026, 11, 1)),
        (spending(12_500), date(2026, 11, 30)),
        (spending(1_000_000_000, "Transport", "mobil"), date(2026, 11, 2)),
        (spending(1_000_000_000, "Transport", "mobil"), date(2026, 11, 3)),
        (spending(1_000_000_000, "Transport", "mobil"), date(2026, 11, 4)),
        (spending(3, "Hiburan", "x"), date(2026, 11, 3)),
        (income(8_000_000), date(2026, 11, 25)),
        (income(250_000, "Pemasukan Lain", "dapat"), date(2026, 11, 26)),
        # Outside the month, on both sides.
        (spending(999), date(2026, 10, 31)),
        (spending(999), date(2026, 12, 1)),
    ]
    for entry, occurred_on in fixture:
        await add_entry(app_engine, person_id, entry, occurred_on)
    voided = await add_entry(app_engine, person_id, spending(777_777), date(2026, 11, 15))
    async with person_transaction(app_engine, person_id) as scope:
        await void_entry(scope, voided.entry_id)
    return person_id


async def test_a_month_matches_the_hand_worked_fixture(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        summary = await summarise(scope, NOVEMBER)
    assert summary.spending_rupiah == 3_000_037_503
    assert summary.income_rupiah == 8_250_000
    assert summary.categories == [
        CategoryTotal("Transport", Direction.SPENDING, 3_000_000_000),
        CategoryTotal("Makan", Direction.SPENDING, 37_500),
        CategoryTotal("Hiburan", Direction.SPENDING, 3),
        CategoryTotal("Gaji", Direction.INCOME, 8_000_000),
        CategoryTotal("Pemasukan Lain", Direction.INCOME, 250_000),
    ]


async def test_totals_come_back_as_int_not_decimal(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        summary = await summarise(scope, NOVEMBER)
    assert type(summary.spending_rupiah) is int
    assert all(type(category.total_rupiah) is int for category in summary.categories)


async def test_a_week_runs_monday_to_sunday(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        summary = await summarise(scope, week_period(date(2026, 11, 4)))
    assert summary.spending_rupiah == 3_000_000_003
    assert summary.income_rupiah == 0


async def test_an_empty_period_is_zero(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        summary = await summarise(scope, month_period(2020, 1))
    assert (summary.spending_rupiah, summary.income_rupiah, summary.categories) == (0, 0, [])


def test_december_ends_at_the_start_of_january() -> None:
    assert month_period(2026, 12) == Period(date(2026, 12, 1), date(2027, 1, 1))


def test_a_week_period_starts_on_monday_from_any_day() -> None:
    assert week_period(date(2026, 11, 2)) == Period(date(2026, 11, 2), date(2026, 11, 9))
    assert week_period(date(2026, 11, 8)) == Period(date(2026, 11, 2), date(2026, 11, 9))


def test_the_day_changes_at_midnight_in_jakarta_not_utc() -> None:
    assert today_in_jakarta(datetime(2026, 11, 30, 16, 59, tzinfo=UTC)) == date(2026, 11, 30)
    assert today_in_jakarta(datetime(2026, 11, 30, 17, 0, tzinfo=UTC)) == date(2026, 12, 1)
