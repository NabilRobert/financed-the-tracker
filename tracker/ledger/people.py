"""People: the allow-list, finding the sender of a webhook, and consent.

create_person and set_suspended need the owner's login, because tracker_app can't
see other people. Arthur's CLI calls them. Everything else runs as tracker_app.
"""

from dataclasses import dataclass
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


async def create_person(owner_engine: AsyncEngine, *, whatsapp_id: str | None = None, phone: str | None = None) -> int:
    """Adds a person to the allow-list, with their starter categories."""
    async with owner_engine.begin() as connection:
        result = await connection.execute(
            text("INSERT INTO person (whatsapp_id, phone) VALUES (:whatsapp_id, :phone) RETURNING id"),
            {"whatsapp_id": whatsapp_id, "phone": phone},
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


async def resolve_sender(engine: AsyncEngine, whatsapp_id: str, phone: str | None) -> int | None:
    """The person a signed webhook names, or None for a stranger or a suspended person.

    A first message from an allow-listed number links its id and drops the number.
    """
    async with engine.begin() as connection:
        return await connection.scalar(
            text("SELECT tracker_resolve_sender(:whatsapp_id, :phone)"), {"whatsapp_id": whatsapp_id, "phone": phone}
        )


async def count_unknown_sender(engine: AsyncEngine) -> None:
    """I3: a stranger leaves a daily count, and nothing that could identify them."""
    async with engine.begin() as connection:
        await connection.execute(text("SELECT tracker_count_unknown_sender()"))


async def has_consented(scope: PersonScope) -> bool:
    consented_at = await scope.connection.scalar(
        text("SELECT consented_at FROM person WHERE id = :person_id"), {"person_id": scope.person_id}
    )
    return consented_at is not None


async def notice_sent_version(scope: PersonScope) -> str | None:
    return await scope.connection.scalar(
        text("SELECT notice_sent_version FROM person WHERE id = :person_id"), {"person_id": scope.person_id}
    )


async def record_notice_sent(scope: PersonScope, notice_version: str) -> None:
    await scope.connection.execute(
        text("UPDATE person SET notice_sent_version = :notice_version WHERE id = :person_id"),
        {"person_id": scope.person_id, "notice_version": notice_version},
    )


async def record_consent(scope: PersonScope, notice_version: str) -> None:
    """I4, I7: the time, and which notice was agreed to."""
    await scope.connection.execute(
        text("""
            UPDATE person SET consented_at = now(), consent_notice_version = :notice_version
            WHERE id = :person_id AND consented_at IS NULL
        """),
        {"person_id": scope.person_id, "notice_version": notice_version},
    )


@dataclass(frozen=True)
class NewestEntry:
    entry_id: int
    category: str
    amount_rupiah: int


async def newest_entry(scope: PersonScope) -> NewestEntry | None:
    """The person's newest entry, voided or not: what `batal` refers to."""
    row = (
        await scope.connection.execute(
            text("""
                SELECT e.id, c.name, e.amount_rupiah FROM entry e JOIN category c ON c.id = e.category_id
                WHERE e.person_id = :person_id ORDER BY e.id DESC LIMIT 1
            """),
            {"person_id": scope.person_id},
        )
    ).one_or_none()
    return None if row is None else NewestEntry(row.id, row.name, row.amount_rupiah)
