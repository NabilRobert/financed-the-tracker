"""Small builders for the ledger tests."""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.entries import WrittenEntry, write_entry
from tracker.ledger.people import create_person
from tracker.ledger.scope import person_transaction
from tracker.parsing.message import Direction, ParsedEntry

A_DAY = date(2026, 11, 2)


async def new_person(owner_engine: AsyncEngine) -> int:
    return await create_person(owner_engine, bsuid=f"ID.{uuid.uuid4().hex}")


def spending(amount_rupiah: int, category: str = "Makan", description: str = "makan") -> ParsedEntry:
    return ParsedEntry(amount_rupiah, Direction.SPENDING, category, description)


def income(amount_rupiah: int, category: str = "Gaji", description: str = "gaji") -> ParsedEntry:
    return ParsedEntry(amount_rupiah, Direction.INCOME, category, description)


async def add_entry(
    app_engine: AsyncEngine, person_id: int, entry: ParsedEntry, occurred_on: date = A_DAY
) -> WrittenEntry:
    async with person_transaction(app_engine, person_id) as scope:
        written = await write_entry(scope, f"wamid.{uuid.uuid4().hex}", entry, occurred_on)
    assert written is not None
    return written
