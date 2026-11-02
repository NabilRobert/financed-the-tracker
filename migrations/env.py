"""Alembic's environment for the tracker's database.

It connects as the owner role, through `config.attributes["database_url"]`
when a test passes one, otherwise $TRACKER_MIGRATOR_DATABASE_URL. Each
migration runs in its own transaction, with short timeouts: a migration that
would wait on a busy table fails fast instead of queueing every query behind it.
"""

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL_ENV = "TRACKER_MIGRATOR_DATABASE_URL"
TIMEOUTS = {"lock_timeout": "5s", "statement_timeout": "60s"}

config = context.config


def database_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not set: add the owner role's database URL to .env")
    return str(url)


def run_with(connection: Connection) -> None:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(database_url(), connect_args={"server_settings": TIMEOUTS})
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_with)
            await connection.commit()
    finally:
        await engine.dispose()


asyncio.run(run_online())
