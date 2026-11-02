"""One person, one transaction: the only way the app reaches the ledger.

Every ledger function takes a PersonScope, so it can't run without a person. The
person is set for the transaction only, so a pooled connection carries nothing
to the next request. That gives two layers (ADR-009): the code filters on
scope.person_id, and row-level security filters on the setting.

Row-level security here catches a forgotten WHERE. It isn't a wall against SQL
injection, because tracker_app may set the value itself. The queries are all
parameterised for that.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@dataclass(frozen=True)
class PersonScope:
    connection: AsyncConnection
    person_id: int


@asynccontextmanager
async def person_transaction(engine: AsyncEngine, person_id: int) -> AsyncGenerator[PersonScope]:
    """Opens a transaction limited to one person. It commits on exit and rolls back on an error."""
    async with engine.begin() as connection:
        # set_config, not SET LOCAL, because SET can't take a bound parameter.
        await connection.execute(
            text("SELECT set_config('tracker.person_id', :person_id, true)"), {"person_id": str(person_id)}
        )
        yield PersonScope(connection, person_id)
