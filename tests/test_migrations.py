"""The migrations themselves: one line of history, and a head that matches the running database."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REPO_ROOT = Path(__file__).parent.parent


async def test_the_database_is_at_the_single_head(owner_engine: AsyncEngine) -> None:
    head = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini"))).get_current_head()
    async with owner_engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head
