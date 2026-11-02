"""The database engine, set up so an error can't print what was being stored."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    """hide_parameters keeps amounts and descriptions out of exception text (T14)."""
    return create_async_engine(database_url, hide_parameters=True)
