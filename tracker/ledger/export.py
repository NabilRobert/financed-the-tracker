"""A person's records as CSV, needing only Postgres (ADR-022).

The channel can vanish overnight if the number is banned, so this runs with WAHA stopped.
It uses the owner's login, because tracker_app can't work outside a person's scope, and
it filters on person_id itself.
"""

import csv
import io
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

HEADER: Final = ("date", "direction", "category", "amount_rupiah", "description", "undone")
# A spreadsheet runs a cell starting with one of these as a formula, and descriptions are typed by people.
_FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")


def _safe_for_spreadsheets(description: str) -> str:
    return f"'{description}" if description.startswith(_FORMULA_STARTS) else description


async def export_person_csv(owner_engine: AsyncEngine, person_id: int) -> str:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT e.occurred_on, e.direction, c.name, e.amount_rupiah, e.description,
                       e.voided_at IS NOT NULL AS is_undone
                FROM entry e JOIN category c ON c.id = e.category_id
                WHERE e.person_id = :person_id ORDER BY e.occurred_on, e.id
            """),
            {"person_id": person_id},
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADER)
    for row in rows:
        description = _safe_for_spreadsheets(row.description)
        writer.writerow((row.occurred_on, row.direction, row.name, row.amount_rupiah, description, row.is_undone))
    return output.getvalue()
