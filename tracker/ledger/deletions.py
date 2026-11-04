"""Making a deletion survive a restore (PR7, T27).

`deleted_person` lives in the database, so restoring an older backup would bring back both
the person and a list that never heard of their deletion. So the ids are also copied, by
cron, to a file on the server that backups don't touch, and from there to the backup
bucket, since losing the server is the likeliest reason to restore
(ops/backup/export_deletions.sh). The list is re-applied after any restore. It holds ids
and nothing else, so it says nothing about anyone.

A deletion made after the last copy and before a restore is the one gap: copy often.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.people import delete_person

# Group-readable: the owner account reads it through the folder's group to copy it off the
# server (export_deletions.sh), while the container writes it as user 10001. Ids only.
LIST_FILE_MODE: Final = 0o640


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
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, LIST_FILE_MODE)
    os.fchmod(descriptor, LIST_FILE_MODE)  # Whatever the umask, or a leftover partial's mode.
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


async def _keep_ids_retired(owner_engine: AsyncEngine, ids: set[int]) -> None:
    """Records the ids as deleted again, and moves the id counter past every one of them.

    pg_restore sets the counter back to where the backup was. Without this, the next person
    added could get a deleted person's id, and the next reapply would delete them.
    """
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO deleted_person (person_id) SELECT unnest(CAST(:ids AS bigint[]))
                ON CONFLICT (person_id) DO NOTHING
            """),
            {"ids": sorted(ids)},
        )
        await connection.execute(
            text("""
                SELECT setval(pg_get_serial_sequence('person', 'id'),
                              GREATEST(:highest_id, (SELECT coalesce(max(id), 0) FROM person)))
            """),
            {"highest_id": max(ids)},
        )


async def reapply_deletions(owner_engine: AsyncEngine, path: Path, *, is_empty_ok: bool = False) -> int:
    """Deletes every listed person a restore brought back. Returns how many were deleted.

    A missing or empty list is an error, because reading it as "nobody" would quietly bring
    back everyone who left. `is_empty_ok` is for a database nobody has ever left.
    """
    if not path.exists():
        raise DeletionListError(f"{path} doesn't exist: copy the list from the server or the backup bucket first")
    ids = read_deleted_ids(path)
    if not ids:
        if is_empty_ok:
            return 0
        raise DeletionListError(f"{path} is empty: if nobody has ever been deleted, pass --empty-ok")
    reapplied = 0
    for person_id in sorted(ids):
        if await delete_person(owner_engine, person_id):
            reapplied += 1
    await _keep_ids_retired(owner_engine, ids)
    return reapplied


# Real people rarely leave together. More confirmed at once looks like a taken-over app
# confirming for others, so the job deletes none of them and Arthur checks (M1, ADR-023).
MAX_DELETIONS_PER_RUN: Final = 2


@dataclass(frozen=True)
class DeletionRun:
    deleted: list[int]
    held: list[int]


async def run_confirmed_deletions(owner_engine: AsyncEngine) -> DeletionRun:
    """Deletes everyone who confirmed `hapus semua`, unless more are waiting than the cap."""
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT id FROM person WHERE deletion_confirmed_at IS NOT NULL ORDER BY id")
        )
        waiting = [int(row.id) for row in rows]
    if len(waiting) > MAX_DELETIONS_PER_RUN:
        return DeletionRun(deleted=[], held=waiting)
    for person_id in waiting:
        await delete_person(owner_engine, person_id)
    return DeletionRun(deleted=waiting, held=[])


async def cancel_confirmed_deletion(owner_engine: AsyncEngine, person_id: int) -> bool:
    """Lets a held person back in, for a confirmation they didn't send. False if there was none."""
    async with owner_engine.begin() as connection:
        cancelled = await connection.scalar(
            text("""
                UPDATE person SET deletion_confirmed_at = NULL, deletion_requested_at = NULL
                WHERE id = :id AND deletion_confirmed_at IS NOT NULL RETURNING id
            """),
            {"id": person_id},
        )
    return cancelled is not None
