"""People: the allow-list, finding the sender of a webhook, and consent.

create_person and set_suspended need the owner's login, because tracker_app can't
see other people. Arthur's CLI calls them. Everything else runs as tracker_app.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.scope import PersonScope
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


async def create_person(owner_engine: AsyncEngine, *, bsuid: str | None = None, phone: str | None = None) -> int:
    """Adds a person to the allow-list, with their starter categories."""
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


async def set_suspended(owner_engine: AsyncEngine, person_id: int, *, is_suspended: bool) -> bool:
    """Stops or restores a person (I8). False if there's no such person."""
    async with owner_engine.begin() as connection:
        changed_id = await connection.scalar(
            text("""
                UPDATE person SET suspended_at = CASE WHEN :is_suspended THEN now() END
                WHERE id = :person_id RETURNING id
            """),
            {"person_id": person_id, "is_suspended": is_suspended},
        )
    return changed_id is not None


async def resolve_sender(engine: AsyncEngine, bsuid: str, phone: str | None) -> int | None:
    """The person a signed webhook names, or None for a stranger or a suspended person.

    A first message from an allow-listed number links its BSUID and drops the number.
    """
    async with engine.begin() as connection:
        return await connection.scalar(
            text("SELECT tracker_resolve_sender(:bsuid, :phone)"), {"bsuid": bsuid, "phone": phone}
        )


async def count_unknown_sender(engine: AsyncEngine) -> None:
    """I3: a stranger leaves a daily count, and nothing that could identify them."""
    async with engine.begin() as connection:
        await connection.execute(text("SELECT tracker_count_unknown_sender()"))


async def change_bsuid(engine: AsyncEngine, old_bsuid: str, new_bsuid: str) -> bool:
    """Follows a number change from Meta's signed system message (ADR-008)."""
    async with engine.begin() as connection:
        changed = await connection.scalar(
            text("SELECT tracker_change_bsuid(:old_bsuid, :new_bsuid)"),
            {"old_bsuid": old_bsuid, "new_bsuid": new_bsuid},
        )
    return bool(changed)


async def has_consented(scope: PersonScope) -> bool:
    consented_at = await scope.connection.scalar(
        text("SELECT consented_at FROM person WHERE id = :person_id"), {"person_id": scope.person_id}
    )
    return consented_at is not None


async def record_consent(scope: PersonScope, notice_version: str) -> None:
    """I4, I7: the time, and which notice was agreed to."""
    await scope.connection.execute(
        text("""
            UPDATE person SET consented_at = now(), consent_notice_version = :notice_version
            WHERE id = :person_id AND consented_at IS NULL
        """),
        {"person_id": scope.person_id, "notice_version": notice_version},
    )
