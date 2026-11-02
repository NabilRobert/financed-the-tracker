"""Adding a person and finding one by the id in a webhook.

create_person needs the owner's login, because tracker_app can't see other
people to add one. From slice 3, Arthur's CLI command calls it.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.parsing.keywords import (
    DEFAULT_SPENDING_CATEGORY,
    OTHER_INCOME_CATEGORY,
    SALARY_CATEGORY,
    SPENDING_KEYWORDS,
)
from tracker.parsing.message import Direction

# C1: the fixed starter list. Each person gets their own copy, so remembered
# words and budgets never touch anyone else's.
STARTER_CATEGORIES: Final[tuple[tuple[str, Direction], ...]] = (
    *((name, Direction.SPENDING) for name in SPENDING_KEYWORDS),
    (DEFAULT_SPENDING_CATEGORY, Direction.SPENDING),
    (SALARY_CATEGORY, Direction.INCOME),
    (OTHER_INCOME_CATEGORY, Direction.INCOME),
)


async def create_person(owner_engine: AsyncEngine, bsuid: str, phone: str | None = None) -> int:
    async with owner_engine.begin() as connection:
        result = await connection.execute(
            text("INSERT INTO person (bsuid, phone) VALUES (:bsuid, :phone) RETURNING id"),
            {"bsuid": bsuid, "phone": phone},
        )
        person_id: int = result.scalar_one()
        for name, direction in STARTER_CATEGORIES:
            await connection.execute(
                text("INSERT INTO category (person_id, name, direction) VALUES (:person_id, :name, :direction)"),
                {"person_id": person_id, "name": name, "direction": direction},
            )
    return person_id


async def find_person_id(engine: AsyncEngine, bsuid: str) -> int | None:
    """The person a webhook names, or None for a stranger (I3). Needs no scope: see migration 0001."""
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT tracker_person_id_for_bsuid(:bsuid)"), {"bsuid": bsuid})
