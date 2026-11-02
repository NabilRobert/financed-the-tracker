"""Budgets: limits, what's used, and warnings that arrive once (B1 to B3, ADR-011)."""

from datetime import date

import pytest
from ledger_helpers import A_DAY, add_entry, income, new_person, spending
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.budgets import BudgetUse, BudgetWarning, list_budgets, set_budget
from tracker.ledger.entries import void_entry
from tracker.ledger.scope import person_transaction

LIMIT = 100_000
NEXT_MONTH = date(2026, 12, 2)


@pytest.fixture
async def person(owner_engine: AsyncEngine, app_engine: AsyncEngine) -> int:
    person_id = await new_person(owner_engine)
    async with person_transaction(app_engine, person_id) as scope:
        assert await set_budget(scope, "Makan", LIMIT)
    return person_id


async def test_setting_a_budget_again_replaces_it(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        assert await set_budget(scope, "makan", 200_000)
        assert await list_budgets(scope, A_DAY) == [BudgetUse("Makan", 200_000, 0)]


@pytest.mark.parametrize("category", ["Tidak Ada", "Gaji"])
async def test_a_budget_needs_a_spending_category(app_engine: AsyncEngine, person: int, category: str) -> None:
    async with person_transaction(app_engine, person) as scope:
        assert not await set_budget(scope, category, LIMIT)


async def test_used_counts_this_month_only_and_only_live_spending(app_engine: AsyncEngine, person: int) -> None:
    await add_entry(app_engine, person, spending(10_000))
    await add_entry(app_engine, person, spending(5_000), date(2026, 11, 30))
    await add_entry(app_engine, person, spending(70_000), date(2026, 10, 31))
    await add_entry(app_engine, person, spending(70_000), NEXT_MONTH)
    await add_entry(app_engine, person, spending(70_000, "Hiburan", "nonton"))
    voided = await add_entry(app_engine, person, spending(7_000))
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, voided.entry_id)
        assert await list_budgets(scope, A_DAY) == [BudgetUse("Makan", LIMIT, 15_000)]


async def test_a_budget_with_nothing_spent_is_listed_at_zero(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        assert await list_budgets(scope, A_DAY) == [BudgetUse("Makan", LIMIT, 0)]


async def test_no_warning_below_eighty_percent(app_engine: AsyncEngine, person: int) -> None:
    assert (await add_entry(app_engine, person, spending(79_999))).budget_warning is None


async def test_a_warning_comes_at_exactly_eighty_percent(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(80_000))
    assert written.budget_warning == BudgetWarning("Makan", 80, 80_000, LIMIT)


async def test_a_warning_comes_once_per_threshold(app_engine: AsyncEngine, person: int) -> None:
    await add_entry(app_engine, person, spending(80_000))
    assert (await add_entry(app_engine, person, spending(1_000))).budget_warning is None


async def test_the_hundred_percent_warning_follows_the_eighty(app_engine: AsyncEngine, person: int) -> None:
    await add_entry(app_engine, person, spending(80_000))
    written = await add_entry(app_engine, person, spending(20_000))
    assert written.budget_warning == BudgetWarning("Makan", 100, 100_000, LIMIT)


async def test_jumping_past_both_thresholds_warns_once_at_the_higher(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(150_000))
    assert written.budget_warning == BudgetWarning("Makan", 100, 150_000, LIMIT)
    assert (await add_entry(app_engine, person, spending(1_000))).budget_warning is None


async def test_a_new_month_warns_again(app_engine: AsyncEngine, person: int) -> None:
    await add_entry(app_engine, person, spending(90_000))
    assert (await add_entry(app_engine, person, spending(90_000), NEXT_MONTH)).budget_warning is not None


async def test_undo_lowers_what_is_used(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(90_000))
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, written.entry_id)
        assert await list_budgets(scope, A_DAY) == [BudgetUse("Makan", LIMIT, 0)]


async def test_a_warning_already_sent_stays_sent_after_undo(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(90_000))
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, written.entry_id)
    assert (await add_entry(app_engine, person, spending(90_000))).budget_warning is None


async def test_a_category_with_no_budget_never_warns(app_engine: AsyncEngine, person: int) -> None:
    assert (await add_entry(app_engine, person, spending(900_000, "Hiburan"))).budget_warning is None


async def test_income_in_a_spending_category_does_not_count_as_used(app_engine: AsyncEngine, person: int) -> None:
    await add_entry(app_engine, person, income(50_000, "Makan", "patungan"))
    async with person_transaction(app_engine, person) as scope:
        assert await list_budgets(scope, A_DAY) == [BudgetUse("Makan", LIMIT, 0)]


async def test_income_never_warns(app_engine: AsyncEngine, person: int) -> None:
    assert (await add_entry(app_engine, person, income(900_000))).budget_warning is None
