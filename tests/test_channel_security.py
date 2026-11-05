"""The channel's security done-when, by threat number (docs/planning/threat-model.md)."""

import ast
import asyncio
import csv
import hashlib
import io
import logging
import stat
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from conftest import Bot, database_url
from ledger_helpers import add_entry, new_person, spending
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner
from webhook_fixtures import (
    API_KEY,
    WEBHOOK_KEY,
    WEBHOOK_PATH,
    app_settings,
    image_event,
    new_lid,
    status_event,
    text_event,
)

from tracker.channel import conversation as conversation_module
from tracker.channel.ratelimit import Admission, MessageLimiter
from tracker.channel.replies import CONSENT_DECLINED, CONSENT_NOTICE, CONSENT_NOTICE_VERSION
from tracker.channel.webhook import WAHA_TIMEOUT_SECONDS, create_waha_client
from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.engine import create_engine
from tracker.ledger.export import export_person_csv
from tracker.ledger.people import create_person, record_consent, set_suspended
from tracker.ledger.scope import person_transaction
from tracker.serve import BIND_PORT, server_config

REPO_ROOT = Path(__file__).parent.parent
SENTINEL = "zzsentinelzz"
SENTINEL_PHONE = "628120000098"
SENTINEL_AMOUNT = "98.765"
# Change this only together with CONSENT_NOTICE_VERSION: people agreed to the old text.
PINNED_NOTICE_SHA256 = "cb2ca1e016532b2f0f3cddd7f8e020b5547cf0ea19c8f8ef7916567704734621"


async def row_counts(owner_engine: AsyncEngine, person_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with owner_engine.connect() as connection:
        for table in ("inbound_message", "entry", "pending_entry"):
            count = await connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE person_id = :id"), {"id": person_id}
            )
            counts[table] = int(count or 0)
    return counts


NO_ROWS = {"inbound_message": 0, "entry": 0, "pending_entry": 0}


async def unknown_sender_total(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as connection:
        total = await connection.scalar(text("SELECT coalesce(sum(senders), 0) FROM unknown_sender_count"))
    return int(total or 0)


async def entry_count_of(owner_engine: AsyncEngine, whatsapp_id: str) -> int:
    async with owner_engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT count(*) FROM entry e JOIN person p ON p.id = e.person_id WHERE p.whatsapp_id = :i"),
            {"i": whatsapp_id},
        )
    return int(count or 0)


async def person_id_of(owner_engine: AsyncEngine, whatsapp_id: str) -> int:
    async with owner_engine.connect() as connection:
        person_id = await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :i"), {"i": whatsapp_id})
    assert person_id is not None
    return int(person_id)


# T4: strangers.
async def test_a_stranger_gets_no_reply_and_leaves_only_a_count(owner_engine: AsyncEngine, bot: Bot) -> None:
    before = await unknown_sender_total(owner_engine)
    stranger = new_lid()
    bot.waha.pair(stranger, "628120000099")
    assert (await bot.say(stranger, "makan 25rb")).status_code == 200
    assert bot.waha.texts == []
    assert await unknown_sender_total(owner_engine) == before + 1
    async with owner_engine.connect() as connection:
        matches = await connection.scalar(
            text("SELECT count(*) FROM person WHERE whatsapp_id = :i OR phone = '628120000099'"), {"i": stranger}
        )
    assert matches == 0


async def test_the_app_login_cannot_read_the_stranger_count(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(Exception, match="permission denied"):
            await connection.execute(text("SELECT * FROM unknown_sender_count"))


# T5: rate limits.
async def test_past_the_minute_limit_messages_are_refused_with_one_notice() -> None:
    limiter = MessageLimiter(per_minute=2, per_day=100)
    results = [await limiter.admit(1) for _ in range(5)]
    assert results == [
        Admission.ALLOWED,
        Admission.ALLOWED,
        Admission.REFUSED_WITH_NOTICE,
        Admission.REFUSED,
        Admission.REFUSED,
    ]


async def test_past_the_day_limit_messages_are_refused() -> None:
    limiter = MessageLimiter(per_minute=100, per_day=1)
    assert [await limiter.admit(1), await limiter.admit(1)] == [Admission.ALLOWED, Admission.REFUSED_WITH_NOTICE]


async def test_one_persons_limit_does_not_touch_another() -> None:
    limiter = MessageLimiter(per_minute=1, per_day=100)
    await limiter.admit(1)
    assert await limiter.admit(2) is Admission.ALLOWED


async def test_each_person_gets_their_own_limit_notice() -> None:
    limiter = MessageLimiter(per_minute=1, per_day=100)
    await limiter.admit(1)
    await limiter.admit(2)
    assert await limiter.admit(1) is Admission.REFUSED_WITH_NOTICE
    assert await limiter.admit(2) is Admission.REFUSED_WITH_NOTICE


async def test_a_flood_stops_at_the_limit_and_costs_one_notice(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    for _ in range(25):
        await bot.say(member, "makan 1rb")
    # Joining used two of the minute's 20 messages.
    assert await entry_count_of(owner_engine, member) == 18
    assert bot.waha.texts.count("Pesanmu terlalu banyak. Coba lagi nanti ya.") == 1
    assert len(bot.waha.texts) == 19


async def test_one_persons_flood_does_not_silence_another(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    for _ in range(21):
        await bot.say(member, "makan 1rb")
    other = new_lid()
    await create_person(owner_engine, whatsapp_id=other)
    await bot.say(other, "halo")
    await bot.say(other, "setuju")
    await bot.say(other, "makan 5rb")
    assert bot.waha.texts[-1].startswith("Tercatat pengeluaran: Makan Rp5.000")


# T11: suspension.
async def test_a_suspended_person_can_neither_read_nor_write(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "makan 25rb")
    bot.waha.texts.clear()
    person_id = await person_id_of(owner_engine, member)
    assert await set_suspended(owner_engine, person_id, is_suspended=True)
    for event in (
        text_event(member, "rekap"),
        text_event(member, "makan 25rb"),
        text_event(member, "batal"),
        image_event(member),
    ):
        assert (await bot.post(event)).status_code == 200
    assert bot.waha.texts == []
    assert await entry_count_of(owner_engine, member) == 1
    assert await set_suspended(owner_engine, person_id, is_suspended=False)
    await bot.say(member, "makan 5rb")
    assert len(bot.waha.texts) == 1


async def test_a_suspended_number_cannot_link_a_new_id(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id = await create_person(owner_engine, phone="628120000007")
    await set_suspended(owner_engine, person_id, is_suspended=True)
    lid = new_lid()
    bot.waha.pair(lid, "628120000007")
    await bot.say(lid, "halo")
    assert bot.waha.texts == []


# T12 and T21: joining by number, and dropping it.
async def test_the_first_message_links_the_lid_and_drops_the_number(owner_engine: AsyncEngine, bot: Bot) -> None:
    lid = new_lid()
    bot.waha.pair(lid, "628120000001")
    person_id = await create_person(owner_engine, phone="628120000001")
    await bot.say("628120000001@c.us", "halo")
    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT whatsapp_id, phone FROM person WHERE id = :i"), {"i": person_id})
        ).one()
    assert (row.whatsapp_id, row.phone) == (lid, None)
    assert bot.waha.sent_to == ["628120000001@c.us"]


async def test_a_first_message_from_a_lid_links_through_its_phone(owner_engine: AsyncEngine, bot: Bot) -> None:
    lid = new_lid()
    bot.waha.pair(lid, "628120000005")
    await create_person(owner_engine, phone="628120000005")
    await bot.say(lid, "halo")
    assert bot.waha.texts == [CONSENT_NOTICE]


async def test_a_number_with_no_lid_links_by_its_chat_id(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id = await create_person(owner_engine, phone="628120000006")
    await bot.say("628120000006@c.us", "halo")
    async with owner_engine.connect() as connection:
        whatsapp_id = await connection.scalar(text("SELECT whatsapp_id FROM person WHERE id = :i"), {"i": person_id})
    assert whatsapp_id == "628120000006@c.us"


async def test_a_number_cannot_replace_the_id_of_a_linked_person(owner_engine: AsyncEngine, bot: Bot) -> None:
    linked_id = new_lid()
    person_id = await create_person(owner_engine, whatsapp_id=linked_id, phone="628120000003")
    new_owner = new_lid()
    bot.waha.pair(new_owner, "628120000003")
    await bot.say(new_owner, "halo")
    assert bot.waha.texts == []
    async with owner_engine.connect() as connection:
        whatsapp_id = await connection.scalar(text("SELECT whatsapp_id FROM person WHERE id = :i"), {"i": person_id})
    assert whatsapp_id == linked_id


async def test_a_recycled_number_with_a_new_id_reaches_nothing(owner_engine: AsyncEngine, bot: Bot) -> None:
    first_owner, new_owner = new_lid(), new_lid()
    bot.waha.pair(first_owner, "628120000002")
    await create_person(owner_engine, phone="628120000002")
    await bot.say(first_owner, "halo")
    bot.waha.texts.clear()
    bot.waha.pairs.clear()
    bot.waha.pair(new_owner, "628120000002")
    await bot.say(new_owner, "rekap")
    assert bot.waha.texts == []


# T14: no content in logs.
async def _run_every_path(bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch) -> None:
    bot.waha.pair(member, SENTINEL_PHONE)
    await bot.say(member, f"{SENTINEL} {SENTINEL_AMOUNT}")
    await bot.say(member, f"{SENTINEL} 25")
    await bot.say(member, f"{SENTINEL} tanpa jumlah")
    await bot.say(new_lid(), f"{SENTINEL} {SENTINEL_AMOUNT}")

    def broken_parser(message_text: str) -> None:
        raise RuntimeError(f"failed on {message_text}")

    with monkeypatch.context() as patch:
        patch.setattr(conversation_module, "parse_message", broken_parser)
        assert (await bot.say(member, f"{SENTINEL} {SENTINEL_AMOUNT}")).status_code == 500

    bot.waha.is_down = True
    await bot.say(member, f"{SENTINEL} 7rb")
    bot.waha.is_down = False

    bot.waha.is_lookup_down = True
    await bot.say(member, f"{SENTINEL} 8rb")
    bot.waha.is_lookup_down = False

    broken_event = text_event(member, f"{SENTINEL} 7rb")
    del broken_event["payload"]["timestamp"]
    await bot.post(broken_event)
    await bot.post(status_event("STOPPED"))


async def test_no_path_logs_message_text_numbers_or_ids(
    bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await _run_every_path(bot, member, monkeypatch)
    assert caplog.records, "the capture saw nothing, so this test would prove nothing"
    logged = caplog.text
    for secret in (SENTINEL, SENTINEL_PHONE, SENTINEL_AMOUNT, "98765", member, API_KEY, WEBHOOK_KEY):
        assert secret not in logged


async def test_a_failure_is_still_visible_by_its_type(
    bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await _run_every_path(bot, member, monkeypatch)
    assert "webhook_failed error_type=RuntimeError" in caplog.text
    assert "webhook_failed error_type=HTTPStatusError" in caplog.text
    assert "reply_failed error_type=HTTPStatusError" in caplog.text
    assert "webhook_malformed error_type=MalformedEventError" in caplog.text


async def test_a_stopped_session_is_logged(bot: Bot, caplog: pytest.LogCaptureFixture) -> None:
    await bot.post(status_event("FAILED"))
    assert "channel_stopped status=FAILED" in caplog.text


async def test_a_database_error_does_not_print_what_was_being_stored(database_name: str) -> None:
    engine = create_engine(database_url("tracker_owner", database_name))
    try:
        async with engine.begin() as connection:
            with pytest.raises(IntegrityError) as caught:
                await connection.execute(
                    text("INSERT INTO person (whatsapp_id, phone) VALUES (:whatsapp_id, :phone)"),
                    {"whatsapp_id": None, "phone": None},
                )
    finally:
        await engine.dispose()
    assert "parameters hidden" in str(caught.value).lower()


# T6: secrets and URLs.
def test_the_server_keeps_no_access_log() -> None:
    """Uvicorn decides per connection, from whether this logger has anywhere to write."""
    server_config(app_settings("postgresql+asyncpg://app@localhost/x"))
    assert not logging.getLogger("uvicorn.access").hasHandlers()


async def test_the_real_server_answers_and_says_little_about_itself(
    database_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    config = server_config(app_settings(database_url("tracker_app", database_name)))
    config.port = 0
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            response = await client.post(f"http://127.0.0.1:{port}{WEBHOOK_PATH}?secret={SENTINEL}", content=b"{}")
    finally:
        server.should_exit = True
        await task
    assert response.status_code == 401
    assert "server" not in response.headers
    written = capsys.readouterr()
    assert SENTINEL not in written.out + written.err


def test_the_server_binds_to_this_machine_only_and_says_little_about_itself() -> None:
    config = server_config(app_settings("postgresql+asyncpg://app@localhost/x"))
    assert (config.host, config.port) == ("127.0.0.1", BIND_PORT)
    assert (config.server_header, config.date_header) == (False, False)


def test_calls_to_waha_cannot_hang_forever() -> None:
    assert create_waha_client(None).timeout.read == WAHA_TIMEOUT_SECONDS


# T17: consent.
async def test_a_person_who_has_not_agreed_gets_the_notice_and_nothing_is_stored(
    owner_engine: AsyncEngine, bot: Bot
) -> None:
    whatsapp_id = new_lid()
    person_id = await create_person(owner_engine, whatsapp_id=whatsapp_id)
    for event in (
        text_event(whatsapp_id, "makan 25rb"),
        text_event(whatsapp_id, "kopi 25"),
        text_event(whatsapp_id, "rekap"),
        text_event(whatsapp_id, "budget makan 2jt"),
        text_event(whatsapp_id, "batal"),
        text_event(whatsapp_id, "ya"),
        image_event(whatsapp_id),
    ):
        assert (await bot.post(event)).status_code == 200
    assert await row_counts(owner_engine, person_id) == NO_ROWS
    assert all(sent == CONSENT_NOTICE for sent in bot.waha.texts)


async def test_agreeing_records_the_time_and_the_notice_version(owner_engine: AsyncEngine, bot: Bot) -> None:
    whatsapp_id = new_lid()
    person_id = await create_person(owner_engine, whatsapp_id=whatsapp_id)
    await bot.say(whatsapp_id, "halo")
    await bot.say(whatsapp_id, "  SETUJU ")
    assert bot.waha.texts[-1].startswith("Terima kasih!")
    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT consented_at, consent_notice_version FROM person WHERE id = :i"), {"i": person_id}
            )
        ).one()
    assert row.consented_at is not None
    assert row.consent_notice_version == CONSENT_NOTICE_VERSION


async def test_setuju_agrees_only_to_a_notice_the_person_was_sent(owner_engine: AsyncEngine, bot: Bot) -> None:
    whatsapp_id = new_lid()
    person_id = await create_person(owner_engine, whatsapp_id=whatsapp_id)
    await bot.say(whatsapp_id, "setuju")
    assert bot.waha.texts == [CONSENT_NOTICE]
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE person SET notice_sent_version = 'an-older-notice' WHERE id = :i"), {"i": person_id}
        )
    await bot.say(whatsapp_id, "setuju")
    assert bot.waha.texts == [CONSENT_NOTICE, CONSENT_NOTICE]
    async with owner_engine.connect() as connection:
        consented_at = await connection.scalar(text("SELECT consented_at FROM person WHERE id = :i"), {"i": person_id})
    assert consented_at is None


async def test_agreeing_twice_keeps_the_first_time(owner_engine: AsyncEngine, member: str) -> None:
    async def consent_time() -> object:
        async with owner_engine.connect() as connection:
            return await connection.scalar(
                text("SELECT consented_at FROM person WHERE whatsapp_id = :i"), {"i": member}
            )

    first = await consent_time()
    person_id = await person_id_of(owner_engine, member)
    async with person_transaction(owner_engine, person_id) as scope:
        await record_consent(scope, "a-later-notice")
    assert await consent_time() == first


async def test_declining_keeps_only_the_invitation_and_says_so(owner_engine: AsyncEngine, bot: Bot) -> None:
    whatsapp_id = new_lid()
    person_id = await create_person(owner_engine, whatsapp_id=whatsapp_id)
    await bot.say(whatsapp_id, "halo")
    await bot.say(whatsapp_id, "tidak")
    assert bot.waha.texts[-1] == CONSENT_DECLINED
    assert await person_id_of(owner_engine, whatsapp_id) == person_id  # The invitation the reply names.
    await bot.say(whatsapp_id, "makan 25rb")
    assert bot.waha.texts[-1] == CONSENT_NOTICE
    assert await row_counts(owner_engine, person_id) == NO_ROWS


async def test_tidak_before_any_notice_gets_the_notice(bot: Bot, owner_engine: AsyncEngine) -> None:
    whatsapp_id = new_lid()
    await create_person(owner_engine, whatsapp_id=whatsapp_id)
    await bot.say(whatsapp_id, "tidak")
    assert bot.waha.texts == [CONSENT_NOTICE]


async def test_tidak_after_agreeing_is_not_a_refusal(bot: Bot, member: str) -> None:
    await bot.say(member, "tidak")
    assert bot.waha.texts[0].startswith("Belum paham.")


async def test_the_database_refuses_consent_without_a_notice_version(owner_engine: AsyncEngine, member: str) -> None:
    with pytest.raises(IntegrityError, match="consent_names_its_notice"):
        async with owner_engine.begin() as connection:
            await connection.execute(
                text("UPDATE person SET consent_notice_version = NULL WHERE whatsapp_id = :i"), {"i": member}
            )


# T18: the notice.
def test_the_notice_cannot_change_without_a_new_version() -> None:
    digest = hashlib.sha256(f"{CONSENT_NOTICE_VERSION}\n{CONSENT_NOTICE}".encode()).hexdigest()
    assert digest == PINNED_NOTICE_SHA256


@pytest.mark.parametrize(
    "fact",
    [
        "jalur tidak resmi",
        "bisa diblokir",
        "tetap aman di server",
        "Arthur",
        "WhatsApp (Meta)",
        "tidak memakai model AI",
        "nomor kartu",
        "bukan layanan keuangan",
        "ekspor",
        "hapus data saya",
        "14 hari",
    ],
)
def test_the_notice_states_what_a_person_must_know(fact: str) -> None:
    assert fact in CONSENT_NOTICE


# T35 to T38: WAHA itself.
def test_waha_runs_on_a_pinned_image_with_nothing_public() -> None:
    compose = (REPO_ROOT / "ops" / "compose.yaml").read_text(encoding="utf-8")
    assert "image: devlikeapro/waha:gows-2026.10.1@sha256:" in compose
    assert '- "127.0.0.1:3000:3000"' in compose
    assert 'WAHA_DASHBOARD_ENABLED: "False"' in compose
    assert 'WHATSAPP_SWAGGER_ENABLED: "False"' in compose
    assert "WAHA_API_KEY: ${WAHA_API_KEY_SHA512:?" in compose
    assert "WAHA_NO_API_KEY" not in compose
    assert "mem_limit: 512m" in compose


def test_session_files_can_never_be_committed() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "ops/waha/sessions/" in gitignore
    assert ".sessions/" in gitignore


# T40: the ledger outlives the channel.
async def test_the_export_needs_no_channel(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "kopi 18rb")
    await bot.say(member, "makan 25rb")
    await bot.say(member, "batal")
    bot.waha.is_down = True
    bot.waha.is_lookup_down = True
    person_id = await person_id_of(owner_engine, member)
    lines = (await export_person_csv(owner_engine, person_id)).splitlines()
    assert lines[0] == "date,direction,category,amount_rupiah,description,undone"
    assert lines[1].endswith(",spending,Makan,18000,kopi,False")
    assert lines[2].endswith(",spending,Makan,25000,makan,True")


async def test_the_export_holds_one_persons_records(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "kopi 18rb")
    other = await create_person(owner_engine, whatsapp_id=new_lid())
    assert len((await export_person_csv(owner_engine, other)).splitlines()) == 1


@pytest.mark.parametrize("description", ["=1+1", "@cmd", "+1", "-1", "\tx"])
async def test_a_description_cannot_become_a_spreadsheet_formula(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, description: str
) -> None:
    person_id = await new_person(owner_engine)
    await add_entry(app_engine, person_id, spending(1_000, "Makan", description))
    row = next(csv.DictReader(io.StringIO(await export_person_csv(owner_engine, person_id))))
    assert row["description"] == f"'{description}"


async def test_an_ordinary_description_is_exported_unchanged(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    person_id = await new_person(owner_engine)
    await add_entry(app_engine, person_id, spending(1_000, "Makan", "makan, siang"))
    row = next(csv.DictReader(io.StringIO(await export_person_csv(owner_engine, person_id))))
    assert row["description"] == "makan, siang"


# T41: reply only.
def test_nothing_sends_except_a_reply_to_an_inbound_message() -> None:
    callers: list[str] = []
    for source in sorted((REPO_ROOT / "tracker").rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            is_send = (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send_text"
            )
            if is_send:
                callers.append(source.name)
    assert callers == ["webhook.py"]


async def test_a_message_from_the_bot_itself_gets_no_reply(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_event(member, "makan 25rb", from_me=True))
    assert bot.waha.texts == []
    assert await entry_count_of(owner_engine, member) == 0


async def test_the_entry_date_is_the_day_in_jakarta_when_the_message_was_sent(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    """17:00 UTC on 30 November is already 1 December in Jakarta, whatever day it is now."""
    sent_at = int(datetime(2026, 11, 30, 17, 0, tzinfo=UTC).timestamp())
    await bot.post(text_event(member, "makan 25rb", sent_at=sent_at))
    async with owner_engine.connect() as connection:
        occurred_on = await connection.scalar(
            text("SELECT e.occurred_on FROM entry e JOIN person p ON p.id = e.person_id WHERE p.whatsapp_id = :i"),
            {"i": member},
        )
    assert occurred_on == date(2026, 12, 1)


# The CLI.
runner = CliRunner()


def test_the_cli_adds_a_number_and_refuses_anything_else(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    assert runner.invoke(cli_app, ["add", "628120000050"]).exit_code == 0
    assert runner.invoke(cli_app, ["add", "+62 812"]).exit_code != 0


def test_the_cli_adds_a_number_once(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    assert runner.invoke(cli_app, ["add", "628120000052"]).exit_code == 0
    repeated = runner.invoke(cli_app, ["add", "628120000052"])
    assert repeated.exit_code == 1
    assert "already on the list" in repeated.stderr


async def _new_person_id(database_name: str) -> int:
    engine = create_engine(database_url("tracker_owner", database_name))
    try:
        return await create_person(engine, whatsapp_id=new_lid())
    finally:
        await engine.dispose()


async def _suspended_at(database_name: str, person_id: int) -> object:
    engine = create_engine(database_url("tracker_owner", database_name))
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text("SELECT suspended_at FROM person WHERE id = :i"), {"i": person_id})
    finally:
        await engine.dispose()


def test_the_cli_suspends_and_resumes_a_person(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    person_id = asyncio.run(_new_person_id(database_name))
    assert runner.invoke(cli_app, ["suspend", str(person_id)]).exit_code == 0
    assert asyncio.run(_suspended_at(database_name, person_id)) is not None
    assert runner.invoke(cli_app, ["resume", str(person_id)]).exit_code == 0
    assert asyncio.run(_suspended_at(database_name, person_id)) is None


def test_the_cli_says_when_there_is_nobody_to_suspend(database_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    assert runner.invoke(cli_app, ["suspend", "999999999"]).exit_code == 1


def test_the_cli_exports_to_a_new_owner_only_file(
    database_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    person_id = asyncio.run(_new_person_id(database_name))
    path = tmp_path / "export.csv"
    assert runner.invoke(cli_app, ["export", str(person_id), str(path)]).exit_code == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8").startswith("date,direction")


def test_the_cli_will_not_overwrite_an_export(
    database_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    person_id = asyncio.run(_new_person_id(database_name))
    path = tmp_path / "export.csv"
    path.write_text("keep me", encoding="utf-8")
    assert runner.invoke(cli_app, ["export", str(person_id), str(path)]).exit_code == 1
    assert path.read_text(encoding="utf-8") == "keep me"


def test_the_cli_never_prints_local_variables_in_a_crash() -> None:
    """They'd include the database URL."""
    assert cli_app.pretty_exceptions_show_locals is False


def test_the_cli_needs_the_owner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_URL_ENV, raising=False)
    assert runner.invoke(cli_app, ["add", "628120000051"]).exit_code != 0
