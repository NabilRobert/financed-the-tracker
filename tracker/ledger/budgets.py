"""Monthly limits, what's been used of them, and warnings that arrive once (B1 to B3).

What's used is always worked out from the live entries, never stored, so undo
lowers it with nothing to keep in step (B3). A warning that has gone out stays
sent: budget_warning's primary key is what makes it once per month (B2).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy import CursorResult, text

from tracker.ledger.scope import PersonScope
from tracker.ledger.summaries import month_period
from tracker.parsing.message import Direction

WARNING_PERCENTS: Final = (100, 80)  # B2, highest first: one entry can cross both, and one warning is enough.
_BUDGETS_WITH_USE_SQL: Final = text("""
    SELECT b.category_id, c.name, b.limit_rupiah,
           CAST(COALESCE((
               SELECT SUM(e.amount_rupiah) FROM entry e
               WHERE e.person_id = b.person_id AND e.category_id = b.category_id AND e.voided_at IS NULL
                 AND e.direction = 'spending'
                 AND e.occurred_on >= :start AND e.occurred_on < :end_exclusive
           ), 0) AS bigint) AS used_rupiah
    FROM budget b JOIN category c ON c.id = b.category_id
    WHERE b.person_id = :person_id
    ORDER BY c.name
""")


@dataclass(frozen=True)
class BudgetUse:
    category: str
    limit_rupiah: int
    used_rupiah: int


@dataclass(frozen=True)
class BudgetWarning:
    category: str
    threshold_percent: int
    used_rupiah: int
    limit_rupiah: int


async def set_budget(scope: PersonScope, category_name: str, limit_rupiah: int) -> bool:
    """Sets or replaces a limit. False if there's no such spending category."""
    category_id = await scope.connection.scalar(
        text("""
            SELECT id FROM category
            WHERE person_id = :person_id AND lower(name) = lower(:name) AND direction = :direction
        """),
        {"person_id": scope.person_id, "name": category_name, "direction": Direction.SPENDING},
    )
    if category_id is None:
        return False
    await scope.connection.execute(
        text("""
            INSERT INTO budget (person_id, category_id, limit_rupiah) VALUES (:person_id, :category_id, :limit_rupiah)
            ON CONFLICT (person_id, category_id) DO UPDATE SET limit_rupiah = EXCLUDED.limit_rupiah
        """),
        {"person_id": scope.person_id, "category_id": category_id, "limit_rupiah": limit_rupiah},
    )
    return True


async def _budgets_with_use(scope: PersonScope, day: date) -> CursorResult[Any]:
    period = month_period(day.year, day.month)
    parameters = {"person_id": scope.person_id, "start": period.start, "end_exclusive": period.end_exclusive}
    return await scope.connection.execute(_BUDGETS_WITH_USE_SQL, parameters)


async def list_budgets(scope: PersonScope, day: date) -> list[BudgetUse]:
    """Every limit, with what's used in the month that holds `day`."""
    rows = await _budgets_with_use(scope, day)
    return [BudgetUse(row.name, row.limit_rupiah, row.used_rupiah) for row in rows]


async def record_budget_warning(scope: PersonScope, category_id: int, day: date) -> BudgetWarning | None:
    """Records every threshold reached in the month, and returns the highest that's new."""
    rows = await _budgets_with_use(scope, day)
    budget = next((row for row in rows if row.category_id == category_id), None)
    if budget is None:
        return None
    new_warning: BudgetWarning | None = None
    for percent in WARNING_PERCENTS:
        is_reached = budget.used_rupiah * 100 >= budget.limit_rupiah * percent  # Integers: no rounding near the line.
        if is_reached and await _is_first_warning(scope, category_id, day, percent) and new_warning is None:
            new_warning = BudgetWarning(budget.name, percent, budget.used_rupiah, budget.limit_rupiah)
    return new_warning


async def _is_first_warning(scope: PersonScope, category_id: int, day: date, percent: int) -> bool:
    inserted = await scope.connection.scalar(
        text("""
            INSERT INTO budget_warning (person_id, category_id, month, threshold_percent)
            VALUES (:person_id, :category_id, :month, :percent)
            ON CONFLICT DO NOTHING
            RETURNING 1
        """),
        {"person_id": scope.person_id, "category_id": category_id, "month": day.replace(day=1), "percent": percent},
    )
    return inserted is not None
