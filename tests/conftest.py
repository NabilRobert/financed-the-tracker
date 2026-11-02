"""Shared test setup: a throwaway migrated database, and the corpus scores after every run.

Database tests use a local postgres:16 (see the README) or $TRACKER_TEST_ADMIN_URL.
With no server they skip with a message. CI sets TRACKER_REQUIRE_DB=1, which turns
the skip into a failure, so a missing database can't pass CI silently.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from corpus_scoring import DEV_CORPUS, HOLDOUT_CORPUS, format_score, load_corpus, score_corpus
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from webhook_fixtures import WEBHOOK_PATH, FakeWaha, app_settings, new_lid, signed_headers, text_event

from tracker.channel.webhook import create_app
from tracker.ledger.people import create_person

ADMIN_URL_ENV = "TRACKER_TEST_ADMIN_URL"
REQUIRE_DB_ENV = "TRACKER_REQUIRE_DB"
DEFAULT_ADMIN_URL = "postgresql://postgres@127.0.0.1:55441/postgres"
CONNECT_TIMEOUT_SECONDS = 3
REPO_ROOT = Path(__file__).parent.parent


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    terminalreporter.section("parser accuracy (see docs/parser-accuracy.md)")
    terminalreporter.line(format_score("dev", score_corpus(load_corpus(DEV_CORPUS))))
    terminalreporter.line(format_score("held-out", score_corpus(load_corpus(HOLDOUT_CORPUS))))


def _admin_url() -> str:
    return os.environ.get(ADMIN_URL_ENV, DEFAULT_ADMIN_URL)


def database_url(role: str, database: str) -> str:
    """The admin URL's host, for another role and database. The test server trusts local logins."""
    host = _admin_url().split("@", 1)[1].rsplit("/", 1)[0]
    return f"postgresql+asyncpg://{role}@{host}/{database}"


async def _run_as_admin(*statements: str) -> None:
    """Runs outside a transaction, because CREATE DATABASE needs that."""
    engine = create_async_engine(
        _admin_url().replace("postgresql://", "postgresql+asyncpg://", 1),
        isolation_level="AUTOCOMMIT",
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


def _skip_or_fail(reason: str) -> None:
    message = f"{reason}. Start the test database (see the README), or set {ADMIN_URL_ENV}."
    if os.environ.get(REQUIRE_DB_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


def _create_database(name: str) -> None:
    roles_sql = (REPO_ROOT / "ops" / "db" / "roles.sql").read_text(encoding="utf-8")
    try:
        asyncio.run(_run_as_admin(roles_sql, f'CREATE DATABASE "{name}" OWNER tracker_owner'))
    except (OSError, TimeoutError, DBAPIError, OperationalError) as error:
        _skip_or_fail(f"no test Postgres at {_admin_url()} ({type(error).__name__})")


def _migrate(url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def database_name() -> Iterator[str]:
    """A migrated database for the whole run. Tests make their own people, so they can't collide."""
    name = f"tracker_test_{uuid.uuid4().hex[:8]}"
    _create_database(name)
    try:
        _migrate(database_url("tracker_owner", name))
        yield name
    finally:
        asyncio.run(_run_as_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
async def app_engine(database_name: str) -> AsyncIterator[AsyncEngine]:
    """The running app's login: bound by row-level security."""
    engine = create_async_engine(database_url("tracker_app", database_name))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def owner_engine(database_name: str) -> AsyncIterator[AsyncEngine]:
    """The owner's login: not bound by row-level security, so tests can see what is really stored."""
    engine = create_async_engine(database_url("tracker_owner", database_name))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def span_exporter() -> InMemorySpanExporter:
    """Every span the app makes, kept in memory. Set once, because OpenTelemetry allows only that."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def fake_waha() -> FakeWaha:
    return FakeWaha()


@pytest.fixture
async def client(database_name: str, fake_waha: FakeWaha) -> AsyncIterator[httpx.AsyncClient]:
    """The whole app, reached over ASGI. WAHA is the stand-in, so nothing leaves the machine."""
    app = create_app(app_settings(database_url("tracker_app", database_name)), fake_waha.transport())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):  # The transport runs no lifespan, and the engine needs closing.
        async with httpx.AsyncClient(transport=transport, base_url="http://tracker.test") as test_client:
            yield test_client


class Bot:
    """Sends signed events to the app, as WAHA would."""

    def __init__(self, client: httpx.AsyncClient, waha: FakeWaha) -> None:
        self.client = client
        self.waha = waha

    async def post(self, event: dict[str, Any]) -> httpx.Response:
        body = json.dumps(event).encode()
        return await self.client.post(WEBHOOK_PATH, content=body, headers=signed_headers(body))

    async def say(self, sender: str, text: str) -> httpx.Response:
        return await self.post(text_event(sender, text))


@pytest.fixture
def bot(client: httpx.AsyncClient, fake_waha: FakeWaha) -> Bot:
    return Bot(client, fake_waha)


@pytest.fixture
async def member(owner_engine: AsyncEngine, bot: Bot) -> str:
    """The id of someone on the allow-list who has agreed to the notice."""
    whatsapp_id = new_lid()
    await create_person(owner_engine, whatsapp_id=whatsapp_id)
    await bot.say(whatsapp_id, "halo")
    await bot.say(whatsapp_id, "setuju")
    bot.waha.texts.clear()
    return whatsapp_id
