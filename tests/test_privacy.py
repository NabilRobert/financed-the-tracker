"""Slice 4's security done-when, by threat number (docs/planning/threat-model.md)."""

import base64
import csv
import io
import json
from datetime import timedelta

import httpx
import pytest
from conftest import Bot, database_url
from ledger_helpers import run_the_deletion_job
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner
from webhook_fixtures import API_KEY, SESSION, WAHA_URL, WEBHOOK_KEY, FakeWaha, app_settings, new_lid, text_event

from tracker.channel import conversation as conversation_module
from tracker.channel import webhook as webhook_module
from tracker.channel.gateway import Attachment
from tracker.channel.ratelimit import MAX_REPLIES_PER_DAY, MAX_REPLIES_PER_MINUTE, MessageLimiter
from tracker.channel.replies import DELETE_CONFIRM, DELETE_HOW, DELETED, SENSITIVE_NUMBER
from tracker.channel.settings import Settings
from tracker.channel.tracing import SERVICE_NAME, configure_tracing, create_tracer_provider
from tracker.channel.waha import SEND_ATTEMPTS, WahaGateway
from tracker.channel.webhook import create_app
from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.engine import create_engine
from tracker.ledger.entries import MessageOutcome, record_outcome
from tracker.ledger.people import create_person, delete_person_in_scope
from tracker.ledger.retention import BARE_MESSAGE_ID_RETENTION, purge_expired
from tracker.ledger.scope import PersonScope, person_transaction
from tracker.parsing.message import ParsedEntry, parse_message
from tracker.parsing.sensitive import looks_sensitive

LID = "12345@lid"
SENTINEL = "zzsentinelzz"
SENTINEL_AMOUNT = "98.765"
runner = CliRunner()


async def person_id_of(owner_engine: AsyncEngine, whatsapp_id: str) -> int | None:
    async with owner_engine.connect() as connection:
        return await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :i"), {"i": whatsapp_id})


async def rows_left_for(owner_engine: AsyncEngine, person_id: int) -> dict[str, int]:
    """Every table with a person_id, found from the catalog, so a new table can't be missed."""
    counts: dict[str, int] = {}
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'person_id' AND table_name <> 'deleted_person'
            """)
        )
        for row in rows.all():
            count = await connection.scalar(
                text(f"SELECT count(*) FROM {row.table_name} WHERE person_id = :id"), {"id": person_id}
            )
            counts[row.table_name] = int(count or 0)
        person_rows = await connection.scalar(text("SELECT count(*) FROM person WHERE id = :id"), {"id": person_id})
        counts["person"] = int(person_rows or 0)
    return counts


async def fill_every_table(bot: Bot, whatsapp_id: str) -> None:
    await bot.say(whatsapp_id, "budget makan 10rb")
    await bot.say(whatsapp_id, "makan 9rb")
    await bot.say(whatsapp_id, "martabak 1rb")
    await bot.say(whatsapp_id, "kategori makan")
    await bot.say(whatsapp_id, "kopi 25")


# T16, T11: leaving.
async def test_hapus_data_saya_leaves_zero_rows_for_that_person_alone(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    other = new_lid()
    await create_person(owner_engine, whatsapp_id=other)
    await bot.say(other, "halo")
    await bot.say(other, "setuju")
    await bot.say(other, "makan 25rb")
    await fill_every_table(bot, member)
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    other_id = await person_id_of(owner_engine, other)
    assert other_id is not None
    before = await rows_left_for(owner_engine, person_id)
    assert all(before.values()), f"this test must start with a row in every table: {before}"
    other_before = await rows_left_for(owner_engine, other_id)

    await bot.say(member, "hapus data saya")
    assert bot.waha.texts[-1] == DELETE_CONFIRM
    assert await rows_left_for(owner_engine, person_id) == before | {"inbound_message": before["inbound_message"] + 1}
    await bot.say(member, "hapus semua")
    assert bot.waha.texts[-1] == DELETED
    await run_the_deletion_job(owner_engine)

    assert set((await rows_left_for(owner_engine, person_id)).values()) == {0}
    assert await rows_left_for(owner_engine, other_id) == other_before


async def test_a_deleted_person_is_a_stranger_afterwards(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "hapus data saya")
    await bot.say(member, "hapus semua")
    await run_the_deletion_job(owner_engine)
    bot.waha.texts.clear()
    await bot.say(member, "rekap")
    assert bot.waha.texts == []


async def test_a_deletion_leaves_only_the_id_behind(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    person_id = await person_id_of(owner_engine, member)
    await bot.say(member, "hapus data saya")
    await bot.say(member, "hapus semua")
    await run_the_deletion_job(owner_engine)
    async with owner_engine.connect() as connection:
        result = await connection.execute(text("SELECT * FROM deleted_person WHERE person_id = :id"), {"id": person_id})
        rows = result.all()
    assert len(rows) == 1
    assert set(result.keys()) == {"person_id", "deleted_at"}


async def test_hapus_semua_without_a_request_deletes_nothing(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "hapus semua")
    assert bot.waha.texts == [DELETE_HOW]
    assert await person_id_of(owner_engine, member) is not None


async def test_a_stale_deletion_request_cannot_be_confirmed(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "hapus data saya")
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE person SET deletion_requested_at = now() - interval '11 minutes' WHERE whatsapp_id = :i"),
            {"i": member},
        )
    await bot.say(member, "hapus semua")
    assert bot.waha.texts[-1] == DELETE_HOW
    assert await person_id_of(owner_engine, member) is not None


async def test_a_deletion_request_inside_the_window_still_counts(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.say(member, "hapus data saya")
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE person SET deletion_requested_at = now() - interval '9 minutes' WHERE whatsapp_id = :i"),
            {"i": member},
        )
    await bot.say(member, "hapus semua")
    assert bot.waha.texts[-1] == DELETED
    await run_the_deletion_job(owner_engine)


@pytest.mark.parametrize("message", ["hapus", "hapus data", "hapus semuanya dong"])
async def test_a_half_finished_hapus_says_how(owner_engine: AsyncEngine, bot: Bot, member: str, message: str) -> None:
    await bot.say(member, message)
    assert bot.waha.texts == [DELETE_HOW]
    assert await person_id_of(owner_engine, member) is not None


async def test_the_delete_function_needs_a_person_in_scope(owner_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="no person in scope"):
        async with owner_engine.begin() as connection:
            await connection.execute(text("SELECT tracker_delete_person_in_scope()"))


async def test_the_delete_function_reaches_only_the_person_in_scope(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    other = await create_person(owner_engine, whatsapp_id=new_lid())
    async with person_transaction(owner_engine, other) as scope:
        await delete_person_in_scope(scope)
    assert await person_id_of(owner_engine, member) == person_id
    assert bot.waha.texts == []


async def test_the_app_cannot_read_the_deleted_list(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.connect() as connection:
            await connection.execute(text("SELECT * FROM deleted_person"))


def test_the_cli_deletes_only_with_yes(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    assert runner.invoke(cli_app, ["add", "628120000060"]).exit_code == 0
    person_id = runner.invoke(cli_app, ["add", "628120000061"]).stdout.split()[2].rstrip(".")
    assert runner.invoke(cli_app, ["delete", person_id]).exit_code == 1
    assert runner.invoke(cli_app, ["delete", person_id, "--yes"]).exit_code == 0
    assert runner.invoke(cli_app, ["delete", person_id, "--yes"]).exit_code == 1


# T19: ekspor.
async def test_ekspor_sends_this_persons_records_and_no_one_elses(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    other = new_lid()
    await create_person(owner_engine, whatsapp_id=other)
    await bot.say(other, "halo")
    await bot.say(other, "setuju")
    await bot.say(other, "rahasia orang lain 99rb")
    await bot.say(member, "makan 25rb")
    await bot.say(member, "ekspor")
    sent = bot.waha.files[-1]
    assert (sent["chatId"], sent["session"]) == (member, SESSION)
    assert sent["file"]["mimetype"] == "text/csv"
    assert sent["file"]["filename"] == "catatan-keuangan.csv"
    assert sent["caption"] == "Ini semua catatanmu."
    rows = list(csv.DictReader(io.StringIO(base64.b64decode(sent["file"]["data"]).decode())))
    assert [(row["description"], row["amount_rupiah"]) for row in rows] == [("makan", "25000")]


# T13: card and account numbers.
@pytest.mark.parametrize(
    "message",
    [
        "4111111111111111",
        "bayar 4111 1111 1111 1111",
        "kartu 4111-1111-1111-1111 25rb",
        "KTP saya 3171000000000001",
        "nomor 1234567890123",
        "1234567890123456789",
        "transfer ke rekening 12345678 25rb",
        "rek 0123456789",
        "norek: 123456789 50rb",
        "cvv 12345678",
    ],
)
def test_what_looks_like_a_card_or_account_number_is_caught(message: str) -> None:
    assert looks_sensitive(message)


@pytest.mark.parametrize(
    "message",
    [
        "makan 25rb",
        "kos 1.200.000",
        "bensin 100000",
        "pulsa 08120000000 50rb",
        "gaji 10500000",
        "1000000000",
        "12345678901234567890",
    ],
)
def test_ordinary_money_is_not_mistaken_for_a_card(message: str) -> None:
    assert not looks_sensitive(message)


def test_digits_in_small_groups_are_counted_without_their_gaps() -> None:
    assert looks_sensitive("1234 5678 9012 3456 789")
    assert looks_sensitive("kartu 123 456 789 012 345")


def test_two_groups_of_digits_are_two_numbers_not_a_card() -> None:
    assert not looks_sensitive("nomor 123456 789015")


def test_a_twelve_digit_number_counts_only_if_it_passes_luhn() -> None:
    assert looks_sensitive("nomor 123456789015") is True
    assert looks_sensitive("nomor 123456789016") is False


async def test_a_card_number_is_refused_before_anything_stores_it(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.say(member, "beli kopi 4111 1111 1111 1111 25rb")
    assert bot.waha.texts == [SENSITIVE_NUMBER]
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    left = await rows_left_for(owner_engine, person_id)
    assert (left["entry"], left["pending_entry"]) == (0, 0)


async def test_a_card_number_cannot_reach_a_question_either(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "kopi 25 4111111111111111")
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    assert (await rows_left_for(owner_engine, person_id))["pending_entry"] == 0


def test_a_long_number_is_kept_out_of_the_description() -> None:
    parsed = parse_message("pulsa 08120000000 50rb")
    assert isinstance(parsed, ParsedEntry)
    assert parsed.description == "pulsa #"


# T15: retention.
async def test_the_purge_removes_expired_questions_and_old_bare_message_ids(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.say(member, "halo")
    await bot.say(member, "makan 25rb")
    await bot.say(member, "kopi 25")
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    age = BARE_MESSAGE_ID_RETENTION + timedelta(days=1)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE inbound_message SET received_at = now() - CAST(:age AS interval) WHERE person_id = :id"),
            {"age": age, "id": person_id},
        )
        await connection.execute(
            text("UPDATE pending_entry SET expires_at = now() - interval '1 second' WHERE person_id = :id"),
            {"id": person_id},
        )
    result = await purge_expired(owner_engine)
    assert result.pending_entries >= 1
    left = await rows_left_for(owner_engine, person_id)
    assert (left["pending_entry"], left["inbound_message"], left["entry"]) == (0, 1, 1)


async def test_the_purge_keeps_what_is_still_in_date(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "halo")
    await bot.say(member, "kopi 25")
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    await purge_expired(owner_engine)
    left = await rows_left_for(owner_engine, person_id)
    assert (left["pending_entry"], left["inbound_message"]) == (1, 2)


async def test_a_purged_entrys_message_id_is_never_removed(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    event = text_event(member, "makan 25rb")
    await bot.post(event)
    async with owner_engine.begin() as connection:
        await connection.execute(text("UPDATE inbound_message SET received_at = now() - interval '400 days'"))
    await purge_expired(owner_engine)
    await bot.post(event)
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    assert (await rows_left_for(owner_engine, person_id))["entry"] == 1


def test_the_cli_runs_the_purge(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    result = runner.invoke(cli_app, ["purge"])
    assert result.exit_code == 0
    assert "Removed" in result.stdout


# Outcomes: counts for finding misses, with no text.
@pytest.mark.parametrize(
    ("message", "outcome"),
    [
        ("makan 25rb", "entry"),
        ("kopi 25", "question"),
        ("rekap", "command"),
        ("halo", "unparsed"),
        ("100 usd", "refused"),
        ("4111111111111111", "refused"),
        ("batal", "answer"),
        ("ekspor", "command"),
    ],
)
async def test_each_message_records_its_outcome(
    owner_engine: AsyncEngine, bot: Bot, member: str, message: str, outcome: str
) -> None:
    event = text_event(member, message)
    await bot.post(event)
    async with owner_engine.connect() as connection:
        stored = await connection.scalar(
            text("SELECT outcome FROM inbound_message WHERE meta_message_id = :id"),
            {"id": event["payload"]["id"]},
        )
    assert stored == outcome


async def test_the_code_records_an_outcome_only_on_your_own_message(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    event = text_event(member, "makan 25rb")
    await bot.post(event)
    other = await create_person(owner_engine, whatsapp_id=new_lid())
    async with owner_engine.begin() as connection:
        inbound_id = await connection.scalar(
            text("SELECT id FROM inbound_message WHERE meta_message_id = :id"), {"id": event["payload"]["id"]}
        )
        await record_outcome(PersonScope(connection, other), int(inbound_id or 0), MessageOutcome.REFUSED)
        outcome = await connection.scalar(
            text("SELECT outcome FROM inbound_message WHERE id = :id"), {"id": inbound_id}
        )
    assert outcome == "entry"


# R7: the reply limit that replaces Meta's cap.
def test_the_reply_limits_are_the_ones_the_plan_states() -> None:
    assert (MAX_REPLIES_PER_MINUTE, MAX_REPLIES_PER_DAY) == (30, 500)


async def test_replies_stop_at_the_limit_across_everyone() -> None:
    limiter = MessageLimiter(replies_per_minute=2, replies_per_day=100)
    assert [await limiter.may_reply() for _ in range(3)] == [True, True, False]


async def test_replies_stop_at_the_daily_limit() -> None:
    limiter = MessageLimiter(replies_per_minute=100, replies_per_day=1)
    assert [await limiter.may_reply(), await limiter.may_reply()] == [True, False]


async def test_past_the_reply_limit_entries_are_still_logged(
    owner_engine: AsyncEngine, bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def refuse(_: object) -> bool:
        return False

    monkeypatch.setattr(MessageLimiter, "may_reply", refuse)
    assert (await bot.say(member, "makan 25rb")).status_code == 200
    assert bot.waha.texts == []
    person_id = await person_id_of(owner_engine, member)
    assert person_id is not None
    assert (await rows_left_for(owner_engine, person_id))["entry"] == 1
    assert "reply_limited" in caplog.text


# R3: send retries.
def test_a_send_is_tried_three_times() -> None:
    assert SEND_ATTEMPTS == 3


def gateway_for(waha: FakeWaha) -> WahaGateway:
    client = httpx.AsyncClient(transport=waha.transport())
    return WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY, retry_wait_ms=0)


async def test_a_send_retries_when_waha_fails_for_a_moment() -> None:
    waha = FakeWaha(failures_left=SEND_ATTEMPTS - 1)
    await gateway_for(waha).send_text(LID, "halo")
    assert waha.texts == ["halo"]


async def test_a_send_gives_up_after_its_attempts() -> None:
    waha = FakeWaha(failures_left=SEND_ATTEMPTS)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway_for(waha).send_text(LID, "halo")


async def test_a_refused_send_is_not_retried() -> None:
    calls: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    gateway = WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY, retry_wait_ms=0)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.send_file(LID, Attachment("a.csv", b"x", "caption"))
    assert len(calls) == 1


async def test_a_send_that_may_have_arrived_is_not_retried() -> None:
    calls: list[httpx.Request] = []

    def time_out(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("no answer", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(time_out))
    gateway = WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY, retry_wait_ms=0)
    with pytest.raises(httpx.ReadTimeout):
        await gateway.send_file(LID, Attachment("a.csv", b"x", "caption"))
    assert len(calls) == 1


async def test_a_lost_connection_is_retried() -> None:
    calls: list[httpx.Request] = []

    def refuse_connection(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse_connection))
    gateway = WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY, retry_wait_ms=0)
    with pytest.raises(httpx.ConnectError):
        await gateway.send_file(LID, Attachment("a.csv", b"x", "caption"))
    assert len(calls) == SEND_ATTEMPTS


# R5: health.
async def test_healthz_reports_ok_and_the_version(database_name: str, fake_waha: FakeWaha) -> None:
    environment = {
        "TRACKER_DATABASE_URL": database_url("tracker_app", database_name),
        "WAHA_URL": WAHA_URL,
        "WAHA_SESSION": SESSION,
        "WAHA_API_KEY": API_KEY,
        "WAHA_WEBHOOK_HMAC_KEY": WEBHOOK_KEY,
        "BANTUAN_CONTACT": "contact",
        "TRACKER_VERSION": "abc1234",
    }
    app = create_app(Settings.from_environment(environment), fake_waha.transport())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://tracker.test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "abc1234"}


async def test_healthz_says_so_when_the_database_is_down() -> None:
    settings = app_settings("postgresql+asyncpg://nobody@127.0.0.1:1/none")
    app = create_app(settings, FakeWaha().transport())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tracker.test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "version": "unknown"}


async def test_healthz_gives_nothing_away(client: httpx.AsyncClient) -> None:
    assert set((await client.get("/healthz")).json()) == {"status", "version"}


# T14: spans.
def _all_span_text(exporter: InMemorySpanExporter) -> str:
    return json.dumps([json.loads(span.to_json()) for span in exporter.get_finished_spans()])


async def test_spans_cover_the_path_and_hold_no_content(
    bot: Bot, member: str, span_exporter: InMemorySpanExporter
) -> None:
    span_exporter.clear()
    await bot.say(member, f"{SENTINEL} {SENTINEL_AMOUNT}")
    await bot.say(member, f"{SENTINEL} tanpa jumlah")
    await bot.say(member, f"{SENTINEL} 25")
    names = {span.name for span in span_exporter.get_finished_spans()}
    assert names == {"whatsapp.webhook", "tracker.parse", "tracker.write", "whatsapp.send"}
    exported = _all_span_text(span_exporter)
    for secret in (SENTINEL, SENTINEL_AMOUNT, "98765", member, "25000"):
        assert secret not in exported


async def test_no_library_adds_spans_of_its_own(client: httpx.AsyncClient, span_exporter: InMemorySpanExporter) -> None:
    """FastAPI traces itself if allowed, including URL query strings."""
    span_exporter.clear()
    await client.post(f"/webhook?secret={SENTINEL}", content=b"{}")
    await client.get(f"/healthz?secret={SENTINEL}")
    assert {span.name for span in span_exporter.get_finished_spans()} == {"whatsapp.webhook"}
    assert SENTINEL not in _all_span_text(span_exporter)


async def test_a_failed_span_names_the_error_type_and_not_its_message(
    bot: Bot, member: str, span_exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_parser(message_text: str) -> None:
        raise RuntimeError(f"failed on {message_text}")

    span_exporter.clear()
    monkeypatch.setattr(conversation_module, "parse_message", broken_parser)
    await bot.say(member, f"{SENTINEL} 7rb")
    spans = [span for span in span_exporter.get_finished_spans() if span.name == "tracker.parse"]
    attributes = spans[0].attributes or {}
    assert attributes["error.type"] == "RuntimeError"
    assert SENTINEL not in _all_span_text(span_exporter)


async def test_the_parser_path_is_recorded(bot: Bot, member: str, span_exporter: InMemorySpanExporter) -> None:
    span_exporter.clear()
    await bot.say(member, "makan 25rb")
    await bot.say(member, "halo")
    paths = [
        (span.attributes or {}).get("tracker.parser_path")
        for span in span_exporter.get_finished_spans()
        if span.name == "tracker.parse"
    ]
    assert paths == ["rules", "failed"]


def test_tracing_is_off_without_an_endpoint() -> None:
    assert app_settings("postgresql+asyncpg://app@localhost/x").otlp_endpoint is None
    assert configure_tracing(None, "abc1234") is None


def test_a_tracer_names_the_service_and_its_version() -> None:
    provider = create_tracer_provider("http://127.0.0.1:4318/v1/traces", "abc1234")
    attributes = provider.resource.attributes
    assert (attributes["service.name"], attributes["service.version"]) == (SERVICE_NAME, "abc1234")
    provider.shutdown()


def test_an_endpoint_turns_tracing_on_with_this_version(span_exporter: InMemorySpanExporter) -> None:
    assert span_exporter is not None
    provider = configure_tracing("http://127.0.0.1:4318/v1/traces", "abc1234")
    assert provider is not None
    assert provider.resource.attributes["service.version"] == "abc1234"
    provider.shutdown()


async def test_spans_are_flushed_when_the_app_stops(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch still in memory at SIGTERM is lost, which is what Project 6 found in the gateway."""
    calls: list[str] = []

    class FakeProvider:
        def shutdown(self) -> None:
            calls.append("shutdown")

    def fake_configure(*_: object) -> FakeProvider:
        return FakeProvider()

    monkeypatch.setattr(webhook_module, "configure_tracing", fake_configure)
    app = create_app(app_settings(database_url("tracker_app", database_name)), FakeWaha().transport())
    async with app.router.lifespan_context(app):
        assert calls == []
    assert calls == ["shutdown"]


async def test_the_engine_factory_is_what_the_app_uses(database_name: str) -> None:
    engine = create_engine(database_url("tracker_app", database_name))
    try:
        assert engine.sync_engine.hide_parameters
    finally:
        await engine.dispose()
