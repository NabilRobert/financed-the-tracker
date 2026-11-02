"""Totals for a period, computed in SQL from the live entries (S1).

Months and weeks cut at midnight in Asia/Jakarta. An entry's occurred_on is
already a Jakarta date, so comparing dates is enough, and no timestamp is
converted here.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import text

from tracker.ledger.scope import PersonScope
from tracker.parsing.message import Direction

JAKARTA: Final = ZoneInfo("Asia/Jakarta")
DAYS_IN_A_WEEK: Final = 7
DECEMBER: Final = 12


@dataclass(frozen=True)
class Period:
    start: date
    end_exclusive: date


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    direction: Direction
    total_rupiah: int


@dataclass(frozen=True)
class Summary:
    period: Period
    spending_rupiah: int
    income_rupiah: int
    categories: list[CategoryTotal]  # Spending first, largest first within each direction.


def today_in_jakarta(now: datetime | None = None) -> date:
    """Today's date in Jakarta. `now` must be timezone-aware; tests pass one."""
    return (now or datetime.now(UTC)).astimezone(JAKARTA).date()


def month_period(year: int, month: int) -> Period:
    next_month = date(year + 1, 1, 1) if month == DECEMBER else date(year, month + 1, 1)
    return Period(date(year, month, 1), next_month)


def week_period(day: date) -> Period:
    """The Monday-to-Sunday week that holds `day`."""
    monday = day - timedelta(days=day.weekday())
    return Period(monday, monday + timedelta(days=DAYS_IN_A_WEEK))


async def summarise(scope: PersonScope, period: Period) -> Summary:
    rows = await scope.connection.execute(
        text("""
            SELECT c.name, e.direction, CAST(SUM(e.amount_rupiah) AS bigint) AS total_rupiah
            FROM entry e JOIN category c ON c.id = e.category_id
            WHERE e.person_id = :person_id AND e.voided_at IS NULL
              AND e.occurred_on >= :start AND e.occurred_on < :end_exclusive
            GROUP BY c.name, e.direction
            ORDER BY e.direction DESC, total_rupiah DESC, c.name
        """),
        {"person_id": scope.person_id, "start": period.start, "end_exclusive": period.end_exclusive},
    )
    categories = [CategoryTotal(row.name, Direction(row.direction), row.total_rupiah) for row in rows]
    return Summary(
        period,
        spending_rupiah=_total(categories, Direction.SPENDING),
        income_rupiah=_total(categories, Direction.INCOME),
        categories=categories,
    )


def _total(categories: list[CategoryTotal], direction: Direction) -> int:
    return sum(category.total_rupiah for category in categories if category.direction is direction)
