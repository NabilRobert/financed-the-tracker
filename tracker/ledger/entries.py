"""Writing and undoing entries.

An entry and its message id commit in one transaction, before any reply
(ADR-013). A repeat delivery from Meta finds the id taken and does nothing, even
after a restart, because the check is the database's unique key (L5), not pywa's
in-memory one. Undo sets voided_at and nothing else (L3).
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import text

from tracker.ledger.budgets import BudgetWarning, record_budget_warning
from tracker.ledger.categories import Category, resolve_category
from tracker.ledger.scope import PersonScope
from tracker.parsing.message import Direction, ParsedEntry


class EntrySource(StrEnum):
    RULES = "rules"
    MODEL = "model"


class MessageOutcome(StrEnum):
    """What became of a message. No text, so misses can be counted safely (ADR-015)."""

    ENTRY = "entry"
    QUESTION = "question"
    ANSWER = "answer"
    COMMAND = "command"
    UNPARSED = "unparsed"
    REFUSED = "refused"
    NOT_TEXT = "not_text"


class VoidOutcome(StrEnum):
    VOIDED = "voided"
    ALREADY_VOIDED = "already_voided"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class WrittenEntry:
    entry_id: int
    category: str
    budget_warning: BudgetWarning | None


@dataclass(frozen=True)
class NewEntry:
    """An entry whose amount is settled, ready to write."""

    amount_rupiah: int
    direction: Direction
    category: Category
    description: str
    occurred_on: date
    source: EntrySource = EntrySource.RULES


async def claim_message(scope: PersonScope, meta_message_id: str) -> int | None:
    """Records a message id, or returns None if this message was already handled."""
    return await scope.connection.scalar(
        text("""
            INSERT INTO inbound_message (person_id, meta_message_id) VALUES (:person_id, :meta_message_id)
            ON CONFLICT (meta_message_id) DO NOTHING
            RETURNING id
        """),
        {"person_id": scope.person_id, "meta_message_id": meta_message_id},
    )


async def record_outcome(scope: PersonScope, inbound_message_id: int, outcome: MessageOutcome) -> None:
    await scope.connection.execute(
        text("UPDATE inbound_message SET outcome = :outcome WHERE id = :id AND person_id = :person_id"),
        {"outcome": outcome, "id": inbound_message_id, "person_id": scope.person_id},
    )


async def insert_entry(scope: PersonScope, inbound_message_id: int, entry: NewEntry) -> WrittenEntry:
    """Writes an entry for a claimed message, and any budget warning it sets off."""
    result = await scope.connection.execute(
        text("""
            INSERT INTO entry (person_id, inbound_message_id, direction, amount_rupiah, category_id,
                               description, occurred_on, source)
            VALUES (:person_id, :inbound_message_id, :direction, :amount_rupiah, :category_id,
                    :description, :occurred_on, :source)
            RETURNING id
        """),
        {
            "person_id": scope.person_id,
            "inbound_message_id": inbound_message_id,
            "direction": entry.direction,
            "amount_rupiah": entry.amount_rupiah,
            "category_id": entry.category.id,
            "description": entry.description,
            "occurred_on": entry.occurred_on,
            "source": entry.source,
        },
    )
    entry_id: int = result.scalar_one()
    budget_warning = None
    if entry.direction is Direction.SPENDING:
        budget_warning = await record_budget_warning(scope, entry.category.id, entry.occurred_on)
    return WrittenEntry(entry_id, entry.category.name, budget_warning)


async def write_entry(
    scope: PersonScope,
    meta_message_id: str,
    entry: ParsedEntry,
    occurred_on: date,
    source: EntrySource = EntrySource.RULES,
) -> WrittenEntry | None:
    """Writes an entry, or returns None if this message was already handled."""
    inbound_message_id = await claim_message(scope, meta_message_id)
    if inbound_message_id is None:
        return None
    category = await resolve_category(scope, entry.description, entry.direction, entry.category)
    new_entry = NewEntry(entry.amount_rupiah, entry.direction, category, entry.description, occurred_on, source)
    return await insert_entry(scope, inbound_message_id, new_entry)


async def void_entry(scope: PersonScope, entry_id: int) -> VoidOutcome:
    """Undoes an entry. Safe to repeat, and it keeps the first time of voiding (L4)."""
    voided_id = await scope.connection.scalar(
        text("""
            UPDATE entry SET voided_at = now()
            WHERE id = :entry_id AND person_id = :person_id AND voided_at IS NULL
            RETURNING id
        """),
        {"entry_id": entry_id, "person_id": scope.person_id},
    )
    if voided_id is not None:
        return VoidOutcome.VOIDED
    known_id = await scope.connection.scalar(
        text("SELECT id FROM entry WHERE id = :entry_id AND person_id = :person_id"),
        {"entry_id": entry_id, "person_id": scope.person_id},
    )
    return VoidOutcome.NOT_FOUND if known_id is None else VoidOutcome.ALREADY_VOIDED
