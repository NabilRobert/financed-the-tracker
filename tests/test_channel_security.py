"""The channel's security done-when, by threat number (docs/planning/threat-model.md)."""

import asyncio
import hashlib
import logging
from datetime import UTC, date, datetime

import httpx
import pytest
import uvicorn
from conftest import Bot, database_url
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner
from webhook_fixtures import (
    AGREE,
    VERIFY_TOKEN,
    WEBHOOK_PATH,
    FakeMeta,
    app_settings,
    button_update,
    image_update,
    new_bsuid,
    number_change_update,
    reaction_update,
    text_update,
)

from tracker.channel import bot as bot_module
from tracker.channel import conversation as conversation_module
from tracker.channel.buttons import undo_payload
from tracker.channel.failures import begin_request, contain_library_logs, has_failed
from tracker.channel.ratelimit import Admission, MessageLimiter
from tracker.channel.replies import CONSENT_NOTICE, CONSENT_NOTICE_VERSION, ReplyButton, consent_request
from tracker.channel.webhook import GRAPH_TIMEOUT_SECONDS, create_app, create_graph_client
from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.engine import create_engine
from tracker.ledger.people import create_person, set_suspended
from tracker.serve import BIND_PORT, server_config

SENTINEL = "zzsentinelzz"
SENTINEL_PHONE = "628120000098"
SENTINEL_AMOUNT = "98.765"
# Change this only together with CONSENT_NOTICE_VERSION: people agreed to the old text.
PINNED_NOTICE_SHA256 = "b3a3a02f7aacf2d56e921179bf81c1bb6b6083fb4f19b293c17af4aedefca56b"


async def row_counts(owner_engine: AsyncEngine, person_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with owner_engine.connect() as connection:
        for table in ("inbound_message", "entry", "pending_entry"):
            count = await connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE person_id = :id"), {"id": person_id}
            )
            counts[table] = int(count or 0)
    return counts


async def unknown_sender_total(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as connection:
        total = await connection.scalar(text("SELECT coalesce(sum(senders), 0) FROM unknown_sender_count"))
    return int(total or 0)


async def entry_ids_of(owner_engine: AsyncEngine, bsuid: str) -> list[int]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT e.id FROM entry e JOIN person p ON p.id = e.person_id WHERE p.bsuid = :bsuid"),
            {"bsuid": bsuid},
        )
    return [row.id for row in rows]


# T4: strangers.
async def test_a_stranger_gets_no_reply_and_leaves_only_a_count(owner_engine: AsyncEngine, bot: Bot) -> None:
    before = await unknown_sender_total(owner_engine)
    stranger = new_bsuid()
    response = await bot.post(text_update(stranger, "makan 25rb", phone="628120000099"))
    assert response.status_code == 200
    assert bot.meta.sent == []
    assert await unknown_sender_total(owner_engine) == before + 1
    async with owner_engine.connect() as connection:
        matches = await connection.scalar(
            text("SELECT count(*) FROM person WHERE bsuid = :bsuid OR phone = '628120000099'"), {"bsuid": stranger}
        )
    assert matches == 0


async def test_a_strangers_button_press_does_nothing(bot: Bot) -> None:
    await bot.post(button_update(new_bsuid(), AGREE))
    assert bot.meta.sent == []


async def test_the_app_login_cannot_read_the_stranger_count(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(Exception, match="permission denied"):
            await connection.execute(text("SELECT * FROM unknown_sender_count"))


# T10: buttons.
async def test_a_tap_cannot_undo_another_persons_entry(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    entry_id = (await entry_ids_of(owner_engine, member))[0]
    other = new_bsuid()
    await create_person(owner_engine, bsuid=other)
    await bot.post(button_update(other, AGREE))
    await bot.post(button_update(other, undo_payload(entry_id)))
    assert bot.meta.texts()[-1] == "Catatan itu tidak ditemukan."
    async with owner_engine.connect() as connection:
        voided = await connection.scalar(text("SELECT voided_at FROM entry WHERE id = :id"), {"id": entry_id})
    assert voided is None


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "undo",
        "undo:",
        "undo:0",
        "undo:-1",
        "undo:abc",
        "undo:1:2",
        "ya:1",
        "ya:1:2",
        "batal",
        "setuju",
        "tolak:1",
        "hack:1",
        "undo:9223372036854775808",
        "undo:99999999999999999999",
        "undo:1; DROP TABLE entry",
        "UNDO:1",
        "undo:١",
    ],
)
async def test_a_forged_payload_does_nothing_and_breaks_nothing(bot: Bot, member: str, payload: str) -> None:
    response = await bot.post(button_update(member, payload))
    assert response.status_code == 200
    assert bot.meta.sent == []


async def test_the_largest_id_a_payload_can_carry_is_handled(bot: Bot, member: str) -> None:
    assert (await bot.post(button_update(member, "undo:9223372036854775807"))).status_code == 200
    assert bot.meta.texts() == ["Catatan itu tidak ditemukan."]


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


async def test_a_flood_stops_at_the_limit_and_costs_one_notice(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    for _ in range(25):
        await bot.post(text_update(member, "makan 1rb"))
    # Agreeing to the notice used one of the minute's 20 messages.
    assert len(await entry_ids_of(owner_engine, member)) == 19
    assert bot.meta.texts().count("Pesanmu terlalu banyak. Coba lagi nanti ya.") == 1
    assert len(bot.meta.sent) == 20


async def test_one_persons_flood_does_not_silence_another(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    for _ in range(21):
        await bot.post(text_update(member, "makan 1rb"))
    other = new_bsuid()
    await create_person(owner_engine, bsuid=other)
    await bot.post(button_update(other, AGREE))
    await bot.post(text_update(other, "makan 5rb"))
    assert bot.meta.texts()[-1] == "Tercatat pengeluaran: Makan Rp5.000"


async def test_each_person_gets_their_own_limit_notice() -> None:
    limiter = MessageLimiter(per_minute=1, per_day=100)
    await limiter.admit(1)
    await limiter.admit(2)
    assert await limiter.admit(1) is Admission.REFUSED_WITH_NOTICE
    assert await limiter.admit(2) is Admission.REFUSED_WITH_NOTICE


# T11: suspension.
async def test_a_suspended_person_can_neither_read_nor_write(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(text_update(member, "makan 25rb"))
    entries_before = await entry_ids_of(owner_engine, member)
    bot.meta.sent.clear()
    async with owner_engine.connect() as connection:
        person_id = await connection.scalar(text("SELECT id FROM person WHERE bsuid = :b"), {"b": member})
    assert await set_suspended(owner_engine, int(person_id or 0), is_suspended=True)
    for update in (
        text_update(member, "rekap"),
        text_update(member, "makan 25rb"),
        button_update(member, undo_payload(entries_before[0])),
        image_update(member),
    ):
        assert (await bot.post(update)).status_code == 200
    assert bot.meta.sent == []
    assert await entry_ids_of(owner_engine, member) == entries_before
    assert await set_suspended(owner_engine, int(person_id or 0), is_suspended=False)
    await bot.post(text_update(member, "makan 5rb"))
    assert len(bot.meta.sent) == 1


async def test_a_suspended_number_cannot_link_a_new_id(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id = await create_person(owner_engine, phone="628120000007")
    await set_suspended(owner_engine, person_id, is_suspended=True)
    await bot.post(text_update(new_bsuid(), "halo", phone="628120000007"))
    assert bot.meta.sent == []


# T12 and T21: joining by number, and dropping it.
async def test_the_first_message_links_the_bsuid_and_drops_the_number(owner_engine: AsyncEngine, bot: Bot) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, phone="628120000001")
    await bot.post(text_update(bsuid, "halo", phone="628120000001"))
    async with owner_engine.connect() as connection:
        row = (await connection.execute(text("SELECT bsuid, phone FROM person WHERE id = :i"), {"i": person_id})).one()
    assert (row.bsuid, row.phone) == (bsuid, None)


async def test_a_number_cannot_replace_the_bsuid_of_a_linked_person(owner_engine: AsyncEngine, bot: Bot) -> None:
    linked_bsuid = new_bsuid()
    person_id = await create_person(owner_engine, bsuid=linked_bsuid, phone="628120000003")
    await bot.post(text_update(new_bsuid(), "halo", phone="628120000003"))
    assert bot.meta.sent == []
    async with owner_engine.connect() as connection:
        bsuid = await connection.scalar(text("SELECT bsuid FROM person WHERE id = :i"), {"i": person_id})
    assert bsuid == linked_bsuid


async def test_a_recycled_number_with_a_new_id_reaches_nothing(owner_engine: AsyncEngine, bot: Bot) -> None:
    await create_person(owner_engine, phone="628120000002")
    await bot.post(text_update(new_bsuid(), "halo", phone="628120000002"))
    bot.meta.sent.clear()
    await bot.post(text_update(new_bsuid(), "rekap", phone="628120000002"))
    assert bot.meta.sent == []


async def test_a_number_change_for_an_unknown_id_links_nothing(bot: Bot) -> None:
    new_id = new_bsuid()
    await bot.post(number_change_update("ID.nobody", new_id))
    await bot.post(text_update(new_id, "rekap"))
    assert bot.meta.sent == []


# T14: no content in logs.
async def _run_every_path(bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch) -> None:
    await bot.post(text_update(member, f"{SENTINEL} {SENTINEL_AMOUNT}", phone=SENTINEL_PHONE))
    await bot.post(text_update(member, f"{SENTINEL} 25", phone=SENTINEL_PHONE))
    await bot.post(text_update(member, f"{SENTINEL} tanpa jumlah", phone=SENTINEL_PHONE))
    await bot.post(button_update(member, f"undo:{SENTINEL}"))
    await bot.post(text_update(new_bsuid(), f"{SENTINEL} {SENTINEL_AMOUNT}", phone=SENTINEL_PHONE))

    def broken_parser(message_text: str) -> None:
        raise RuntimeError(f"failed on {message_text}")

    with monkeypatch.context() as patch:
        patch.setattr(conversation_module, "parse_message", broken_parser)
        assert (await bot.post(text_update(member, f"{SENTINEL} {SENTINEL_AMOUNT}"))).status_code == 500

    bot.meta.is_down = True
    await bot.post(text_update(member, f"{SENTINEL} 7rb", phone=SENTINEL_PHONE))
    bot.meta.is_down = False

    broken_update = text_update(member, f"{SENTINEL} 7rb")
    del broken_update["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"]
    await bot.post(broken_update)


async def test_no_path_logs_message_text_numbers_or_ids(
    bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await _run_every_path(bot, member, monkeypatch)
    assert caplog.records, "the capture saw nothing, so this test would prove nothing"
    logged = caplog.text
    for secret in (SENTINEL, SENTINEL_PHONE, SENTINEL_AMOUNT, "98765", member):
        assert secret not in logged


def test_a_pywa_error_stays_out_of_the_logs_whatever_it_says(caplog: pytest.LogCaptureFixture) -> None:
    contain_library_logs()
    pywa_logger = logging.getLogger("pywa.server")
    with caplog.at_level(logging.DEBUG):
        begin_request()
        try:
            raise ValueError(SENTINEL)
        except ValueError:
            pywa_logger.exception("Failed on %s", SENTINEL)
        pywa_logger.debug("Received raw update: %s", SENTINEL)
    assert SENTINEL not in caplog.text
    assert has_failed()


def test_a_pywa_error_without_an_exception_does_not_fail_the_request() -> None:
    """An unknown webhook field is logged as an error by pywa, and isn't a lost message."""
    contain_library_logs()
    begin_request()
    logging.getLogger("pywa.server").error("Received unexpected update")
    assert not has_failed()


async def test_a_failure_is_still_visible_by_its_type(
    bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await _run_every_path(bot, member, monkeypatch)
    assert "webhook_failed stage=handler error_type=RuntimeError" in caplog.text
    assert "reply_failed error_type=" in caplog.text
    assert "webhook_failed stage=pywa error_type=KeyError" in caplog.text


async def test_a_database_error_does_not_print_what_was_being_stored(database_name: str) -> None:
    engine = create_engine(database_url("tracker_owner", database_name))
    try:
        async with engine.begin() as connection:
            with pytest.raises(IntegrityError) as caught:
                await connection.execute(
                    text("INSERT INTO person (bsuid, phone) VALUES (:bsuid, :phone)"),
                    {"bsuid": None, "phone": None},
                )
                await connection.execute(text("SELECT 1"), {"x": SENTINEL})
    finally:
        await engine.dispose()
    assert "parameters hidden" in str(caught.value).lower()


# T6: the verify token.
def test_the_server_keeps_no_access_log() -> None:
    """Uvicorn decides per connection, from whether this logger has anywhere to write."""
    server_config(app_settings("postgresql+asyncpg://app@localhost/x"))
    assert not logging.getLogger("uvicorn.access").hasHandlers()


async def test_the_entry_date_is_the_day_in_jakarta_when_the_message_was_sent(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    """17:00 UTC on 30 November is already 1 December in Jakarta, whatever day it is now."""
    sent_at = datetime(2026, 11, 30, 17, 0, tzinfo=UTC)
    update = text_update(member, "makan 25rb")
    update["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = str(int(sent_at.timestamp()))
    await bot.post(update)
    async with owner_engine.connect() as connection:
        occurred_on = await connection.scalar(
            text("""
                SELECT e.occurred_on FROM entry e JOIN person p ON p.id = e.person_id WHERE p.bsuid = :bsuid
            """),
            {"bsuid": member},
        )
    assert occurred_on == date(2026, 12, 1)


async def test_the_real_server_writes_no_token_to_its_logs(
    database_name: str, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = server_config(app_settings(database_url("tracker_app", database_name)))
    config.port = 0
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        with caplog.at_level(logging.DEBUG):
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://127.0.0.1:{port}{WEBHOOK_PATH}",
                    params={"hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
                )
    finally:
        server.should_exit = True
        await task
    assert response.status_code == 200
    assert "server" not in response.headers
    written = capsys.readouterr()
    assert VERIFY_TOKEN not in caplog.text + written.out + written.err


def test_the_server_binds_to_this_machine_only_and_says_little_about_itself() -> None:
    config = server_config(app_settings("postgresql+asyncpg://app@localhost/x"))
    assert (config.host, config.port) == ("127.0.0.1", BIND_PORT)
    assert (config.server_header, config.date_header) == (False, False)


def test_replies_to_meta_cannot_hang_forever() -> None:
    assert create_graph_client(None).timeout.read == GRAPH_TIMEOUT_SECONDS


async def test_a_failed_number_change_answers_500_so_meta_retries(
    bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_change(*_: object) -> bool:
        raise RuntimeError("the database broke")

    monkeypatch.setattr(bot_module, "change_bsuid", broken_change)
    assert (await bot.post(number_change_update(member, new_bsuid()))).status_code == 500


async def test_a_number_change_without_the_old_id_changes_nothing(bot: Bot, member: str) -> None:
    update = number_change_update(member, new_bsuid())
    del update["entry"][0]["changes"][0]["value"]["messages"][0]["system"]["previous_user_id"]
    assert (await bot.post(update)).status_code == 200
    await bot.post(text_update(member, "bantuan"))
    assert len(bot.meta.sent) == 1


# T17: consent.
async def test_a_person_who_has_not_agreed_gets_the_notice_and_nothing_is_stored(
    owner_engine: AsyncEngine, bot: Bot
) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, bsuid=bsuid)
    for update in (
        text_update(bsuid, "makan 25rb"),
        text_update(bsuid, "kopi 25"),
        text_update(bsuid, "rekap"),
        text_update(bsuid, "budget makan 2jt"),
        image_update(bsuid),
        button_update(bsuid, "undo:1"),
        button_update(bsuid, "ya:1:0"),
    ):
        assert (await bot.post(update)).status_code == 200
    assert await row_counts(owner_engine, person_id) == {"inbound_message": 0, "entry": 0, "pending_entry": 0}
    assert all(text_sent == CONSENT_NOTICE for text_sent in bot.meta.texts())


async def test_agreeing_records_the_time_and_the_notice_version(owner_engine: AsyncEngine, bot: Bot) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, bsuid=bsuid)
    await bot.post(button_update(bsuid, AGREE))
    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT consented_at, consent_notice_version FROM person WHERE id = :i"), {"i": person_id}
            )
        ).one()
    assert row.consented_at is not None
    assert row.consent_notice_version == str(CONSENT_NOTICE_VERSION)


async def test_a_button_from_an_older_notice_records_nothing_and_shows_this_one(
    owner_engine: AsyncEngine, bot: Bot
) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, bsuid=bsuid)
    await bot.post(button_update(bsuid, "setuju:20200101"))
    assert bot.meta.texts() == [CONSENT_NOTICE]
    async with owner_engine.connect() as connection:
        consented_at = await connection.scalar(text("SELECT consented_at FROM person WHERE id = :i"), {"i": person_id})
    assert consented_at is None


async def test_declining_after_agreeing_does_not_claim_nothing_is_stored(bot: Bot, member: str) -> None:
    await bot.post(button_update(member, "tolak"))
    assert bot.meta.texts()[0].startswith("Kamu sudah setuju")


async def test_agreeing_as_the_very_first_webhook_links_the_number(owner_engine: AsyncEngine, bot: Bot) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, phone="628120000004")
    await bot.post(button_update(bsuid, AGREE, phone="628120000004"))
    assert bot.meta.texts()[0].startswith("Terima kasih!")
    async with owner_engine.connect() as connection:
        linked_bsuid = await connection.scalar(text("SELECT bsuid FROM person WHERE id = :i"), {"i": person_id})
    assert linked_bsuid == bsuid


async def test_agreeing_twice_keeps_the_first_time(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    async def consent_time() -> object:
        async with owner_engine.connect() as connection:
            return await connection.scalar(text("SELECT consented_at FROM person WHERE bsuid = :b"), {"b": member})

    first = await consent_time()
    await bot.post(button_update(member, AGREE))
    assert await consent_time() == first


async def test_declining_stores_nothing(owner_engine: AsyncEngine, bot: Bot) -> None:
    bsuid = new_bsuid()
    person_id = await create_person(owner_engine, bsuid=bsuid)
    await bot.post(button_update(bsuid, "tolak"))
    assert bot.meta.texts() == ["Oke. Tidak ada pesanmu yang disimpan."]
    await bot.post(text_update(bsuid, "makan 25rb"))
    assert bot.meta.texts()[-1] == CONSENT_NOTICE
    assert await row_counts(owner_engine, person_id) == {"inbound_message": 0, "entry": 0, "pending_entry": 0}


async def test_the_database_refuses_consent_without_a_notice_version(owner_engine: AsyncEngine, member: str) -> None:
    with pytest.raises(IntegrityError, match="consent_names_its_notice"):
        async with owner_engine.begin() as connection:
            await connection.execute(
                text("UPDATE person SET consent_notice_version = NULL WHERE bsuid = :b"), {"b": member}
            )


# T18: the notice.
def test_the_notice_cannot_change_without_a_new_version() -> None:
    digest = hashlib.sha256(f"{CONSENT_NOTICE_VERSION}\n{CONSENT_NOTICE}".encode()).hexdigest()
    assert digest == PINNED_NOTICE_SHA256


@pytest.mark.parametrize(
    "fact", ["Meta", "Arthur", "30 hari", "tidak memakai model AI", "nomor kartu", "bukan layanan keuangan"]
)
def test_the_notice_states_what_a_person_must_know(fact: str) -> None:
    assert fact in CONSENT_NOTICE


def test_the_notice_and_its_buttons_fit_metas_limits() -> None:
    reply = consent_request()
    assert len(reply.text) <= 1024
    assert all(_fits_a_button(button) for button in reply.buttons)


def _fits_a_button(button: ReplyButton) -> bool:
    return len(button.title) <= 20 and len(button.payload) <= 256


# Things the bot stays silent on.
async def test_a_reaction_gets_no_reply(bot: Bot, member: str) -> None:
    assert (await bot.post(reaction_update(member))).status_code == 200
    assert bot.meta.sent == []


async def test_a_group_message_gets_no_reply_and_stores_nothing(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    update = text_update(member, "makan 25rb")
    update["entry"][0]["changes"][0]["value"]["messages"][0]["group_id"] = "group-1"
    assert (await bot.post(update)).status_code == 200
    assert bot.meta.sent == []
    assert await entry_ids_of(owner_engine, member) == []


async def test_a_message_for_another_business_number_is_ignored(bot: Bot, member: str) -> None:
    update = text_update(member, "makan 25rb")
    update["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "999"
    assert (await bot.post(update)).status_code == 200
    assert bot.meta.sent == []


# The CLI.
runner = CliRunner()


async def test_the_app_starts_with_pywas_duplicate_cache_off(database_name: str) -> None:
    create_app(app_settings(database_url("tracker_app", database_name)), FakeMeta().transport())


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
        return await create_person(engine, bsuid=new_bsuid())
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


def test_the_cli_suspends_and_says_when_there_is_nobody_to_suspend(
    database_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    assert runner.invoke(cli_app, ["suspend", "999999999"]).exit_code == 1


def test_the_cli_needs_the_owner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_URL_ENV, raising=False)
    result = runner.invoke(cli_app, ["add", "628120000051"])
    assert result.exit_code != 0
