"""Making a deletion survive a restore (PR7, T27).

`deleted_person` lives in the database, so restoring an older backup would bring back both
the person and a list that never heard of their deletion. So the ids are also copied, by
cron, to a file on the server that backups don't touch, and re-applied after any restore.
The file holds ids and nothing else, so it says nothing about anyone.

A deletion made after the last copy and before a restore is the one gap: copy often.
"""

import os
from pathlib import Path
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.people import delete_person

OWNER_ONLY_FILE_MODE: Final = 0o600


class DeletionListError(Exception):
    """The list file has a line that isn't a person id."""


def read_deleted_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    ids: set[int] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = line.strip()
        if not entry:
            continue
        # ASCII only: isdecimal() accepts full-width digits, which int() would read as a real id.
        if not (entry.isascii() and entry.isdecimal()):
            raise DeletionListError(f"{path} line {line_number} isn't a person id: fix or remove it")
        ids.add(int(entry))
    return ids


def _write_ids(path: Path, ids: set[int]) -> None:
    """Writes atomically, so a crash can't leave half a list."""
    partial = path.with_name(f"{path.name}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_ONLY_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.writelines(f"{person_id}\n" for person_id in sorted(ids))
    partial.replace(path)


async def export_deleted_ids(owner_engine: AsyncEngine, path: Path) -> int:
    """Adds the database's deleted ids to the file, keeping any already there. Returns the total."""
    async with owner_engine.connect() as connection:
        rows = await connection.execute(text("SELECT person_id FROM deleted_person"))
        in_database = {int(row.person_id) for row in rows}
    ids = read_deleted_ids(path) | in_database
    _write_ids(path, ids)
    return len(ids)


async def reapply_deletions(owner_engine: AsyncEngine, path: Path) -> int:
    """Deletes every listed person a restore brought back. Returns how many were deleted."""
    reapplied = 0
    for person_id in sorted(read_deleted_ids(path)):
        if await delete_person(owner_engine, person_id):
            reapplied += 1
    return reapplied
