"""The purge job: nothing is kept longer than PR6 says (T15).

`tracker purge` runs this from cron with the owner's login, because tracker_app can
delete nothing. Entries, budgets and categories stay until the person deletes them, and
message ids with an entry stay with it, so a repeat can never add it back.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

BARE_MESSAGE_ID_RETENTION: Final = timedelta(days=30)  # PR6: long past any retry from WAHA.


@dataclass(frozen=True)
class PurgeResult:
    pending_entries: int
    message_ids: int


async def purge_expired(owner_engine: AsyncEngine) -> PurgeResult:
    async with owner_engine.begin() as connection:
        pending = await connection.execute(text("DELETE FROM pending_entry WHERE expires_at < now()"))
        message_ids = await connection.execute(
            text("""
                DELETE FROM inbound_message i
                WHERE i.received_at < now() - CAST(:retention AS interval)
                  AND NOT EXISTS (SELECT 1 FROM entry e WHERE e.inbound_message_id = i.id)
                  AND NOT EXISTS (SELECT 1 FROM pending_entry p WHERE p.inbound_message_id = i.id)
            """),
            {"retention": BARE_MESSAGE_ID_RETENTION},
        )
    return PurgeResult(pending.rowcount, message_ids.rowcount)
