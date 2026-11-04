"""Small builders for the ledger tests."""

import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.deletions import DeletionRun, run_confirmed_deletions
from tracker.ledger.entries import WrittenEntry, write_entry
from tracker.ledger.people import create_person, record_consent
from tracker.ledger.scope import person_transaction
from tracker.parsing.message import Direction, ParsedEntry

A_DAY = date(2026, 11, 2)


async def new_person(owner_engine: AsyncEngine) -> int:
    """A person who has consented, as every ledger test assumes."""
    person_id = await create_person(owner_engine, whatsapp_id=f"{uuid.uuid4().hex}@lid")
    async with person_transaction(owner_engine, person_id) as scope:
        await record_consent(scope, "test-notice")
    return person_id


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


async def _age_earlier_deletions(owner_engine: AsyncEngine) -> None:
    """Moves earlier tests' deletions out of the owner job's 24-hour window."""
    async with owner_engine.begin() as connection:
        await connection.execute(text("UPDATE deleted_person SET deleted_at = deleted_at - interval '2 days'"))


async def quiet_the_deletion_window(owner_engine: AsyncEngine) -> None:
    """A quiet day for the owner job, before a test confirms anyone: earlier deletions leave
    the 24-hour window, and any confirmation another test or probe left waiting is
    cancelled, since the job counts everyone in the shared test database."""
    await _age_earlier_deletions(owner_engine)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE person SET deletion_confirmed_at = NULL WHERE deletion_confirmed_at IS NOT NULL")
        )


async def run_the_deletion_job(owner_engine: AsyncEngine) -> DeletionRun:
    """The owner job, with earlier tests' deletions out of its window so they can't cause a hold."""
    await _age_earlier_deletions(owner_engine)
    return await run_confirmed_deletions(owner_engine)
