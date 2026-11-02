"""Entries waiting for a person's answer: nothing here reaches the ledger until they tap Ya.

The update that resolves a pending entry is one statement with its conditions in the
WHERE, so a double tap or two racing deliveries can only ever win once.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from sqlalchemy import text

from tracker.ledger.categories import Category
from tracker.ledger.entries import NewEntry, WrittenEntry, insert_entry
from tracker.ledger.scope import PersonScope
from tracker.parsing.message import Direction

PENDING_EXPIRY: Final = timedelta(hours=24)  # M3.


@dataclass(frozen=True)
class NewPendingEntry:
    amounts_rupiah: tuple[int, ...]  # One or two readings: reply buttons have room for two and Batal.
    direction: Direction
    category: Category
    description: str
    occurred_on: date


@dataclass(frozen=True)
class ConfirmedEntry:
    written: WrittenEntry
    amount_rupiah: int
    direction: Direction


async def create_pending_entry(scope: PersonScope, inbound_message_id: int, pending: NewPendingEntry) -> int:
    first_amount, *other_amounts = pending.amounts_rupiah
    second_amount = other_amounts[0] if other_amounts else None
    result = await scope.connection.execute(
        text("""
            INSERT INTO pending_entry (person_id, inbound_message_id, direction, first_amount_rupiah,
                                       second_amount_rupiah, category_id, description, occurred_on, expires_at)
            VALUES (:person_id, :inbound_message_id, :direction, :first_amount, :second_amount,
                    :category_id, :description, :occurred_on, now() + :expiry)
            RETURNING id
        """),
        {
            "person_id": scope.person_id,
            "inbound_message_id": inbound_message_id,
            "direction": pending.direction,
            "first_amount": first_amount,
            "second_amount": second_amount,
            "category_id": pending.category.id,
            "description": pending.description,
            "occurred_on": pending.occurred_on,
            "expiry": PENDING_EXPIRY,
        },
    )
    pending_id: int = result.scalar_one()
    return pending_id


async def confirm_pending_entry(scope: PersonScope, pending_id: int, choice: int) -> ConfirmedEntry | None:
    """Writes the chosen reading (0 or 1). None if it's gone: expired, answered, or not theirs."""
    row = (
        await scope.connection.execute(
            text("""
                UPDATE pending_entry p SET resolved_at = now()
                FROM category c
                WHERE p.id = :pending_id AND p.person_id = :person_id AND c.id = p.category_id
                  AND p.resolved_at IS NULL AND p.expires_at > now()
                  AND (:choice = 0 OR p.second_amount_rupiah IS NOT NULL)
                RETURNING p.inbound_message_id, p.direction, p.first_amount_rupiah, p.second_amount_rupiah,
                          p.category_id, c.name AS category_name, p.description, p.occurred_on
            """),
            {"pending_id": pending_id, "person_id": scope.person_id, "choice": choice},
        )
    ).one_or_none()
    if row is None:
        return None
    amount_rupiah = row.first_amount_rupiah if choice == 0 else row.second_amount_rupiah
    category = Category(row.category_id, row.category_name)
    direction = Direction(row.direction)
    entry = NewEntry(amount_rupiah, direction, category, row.description, row.occurred_on)
    written = await insert_entry(scope, row.inbound_message_id, entry)
    return ConfirmedEntry(written, amount_rupiah, direction)


async def cancel_pending_entry(scope: PersonScope, pending_id: int) -> bool:
    cancelled_id = await scope.connection.scalar(
        text("""
            UPDATE pending_entry SET resolved_at = now()
            WHERE id = :pending_id AND person_id = :person_id AND resolved_at IS NULL
            RETURNING id
        """),
        {"pending_id": pending_id, "person_id": scope.person_id},
    )
    return cancelled_id is not None
