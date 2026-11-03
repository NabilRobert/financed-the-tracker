"""The HTTP surface: nothing unsigned or oversized gets in, and a failure is never a quiet 200."""

import json

import httpx
import pytest
from conftest import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from webhook_fixtures import (
    API_KEY,
    WEBHOOK_KEY,
    WEBHOOK_PATH,
    app_environment,
    image_event,
    new_lid,
    signature_for,
    signed_headers,
    status_event,
    text_event,
)

from tracker.channel import conversation as conversation_module
from tracker.channel.settings import Settings, SettingsError
from tracker.channel.webhook import MAX_BODY_BYTES

SIGNATURE_HEADER = "X-Webhook-Hmac"
ALGORITHM_HEADER = "X-Webhook-Hmac-Algorithm"
GOOD_ENVIRONMENT = app_environment("postgresql+asyncpg://app@localhost/tracker")
SECRET_NAMES = ["WAHA_API_KEY", "WAHA_WEBHOOK_HMAC_KEY"]


async def entry_count(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as connection:
        return int(await connection.scalar(text("SELECT count(*) FROM entry")) or 0)


async def entries_of(owner_engine: AsyncEngine, whatsapp_id: str) -> list[int]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT e.amount_rupiah FROM entry e JOIN person p ON p.id = e.person_id
                WHERE p.whatsapp_id = :whatsapp_id ORDER BY e.id
            """),
            {"whatsapp_id": whatsapp_id},
        )
    return [row.amount_rupiah for row in rows]


@pytest.mark.parametrize("name", list(GOOD_ENVIRONMENT))
def test_the_app_refuses_to_start_without_each_setting(name: str) -> None:
    environment = {key: value for key, value in GOOD_ENVIRONMENT.items() if key != name}
    with pytest.raises(SettingsError, match=name):
        Settings.from_environment(environment)


@pytest.mark.parametrize("name", list(GOOD_ENVIRONMENT))
def test_a_blank_setting_counts_as_missing(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_environment({**GOOD_ENVIRONMENT, name: "   "})


@pytest.mark.parametrize("name", SECRET_NAMES)
@pytest.mark.parametrize("value", ["change-me", "change-me-please-a-long-enough-one-indeed", "short"])
def test_a_placeholder_or_short_secret_stops_startup(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_environment({**GOOD_ENVIRONMENT, name: value})


def test_a_secret_of_exactly_the_minimum_length_is_allowed() -> None:
    Settings.from_environment({**GOOD_ENVIRONMENT, "WAHA_API_KEY": "x" * 32})


def test_a_secret_one_short_of_the_minimum_stops_startup() -> None:
    with pytest.raises(SettingsError, match="WAHA_API_KEY"):
        Settings.from_environment({**GOOD_ENVIRONMENT, "WAHA_API_KEY": "x" * 31})


@pytest.mark.parametrize(
    "url",
    ["https://waha.example.com", "http://1.1.1.1:3000", "http://8.8.8.8", "not a url", "http://"],
)
def test_a_public_waha_url_stops_startup(url: str) -> None:
    with pytest.raises(SettingsError, match="WAHA_URL"):
        Settings.from_environment({**GOOD_ENVIRONMENT, "WAHA_URL": url})


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:3000", "http://localhost:3000", "http://waha:3000", "http://10.0.0.5:3000"]
)
def test_a_private_waha_url_is_allowed(url: str) -> None:
    assert Settings.from_environment({**GOOD_ENVIRONMENT, "WAHA_URL": url}).waha_url == url


def test_printing_the_settings_shows_no_secret() -> None:
    printed = repr(Settings.from_environment(GOOD_ENVIRONMENT))
    assert WEBHOOK_KEY not in printed
    assert API_KEY not in printed


async def test_a_request_with_no_signature_is_refused_and_writes_nothing(
    owner_engine: AsyncEngine, client: httpx.AsyncClient, member: str
) -> None:
    before = await entry_count(owner_engine)
    body = json.dumps(text_event(member, "makan 25rb")).encode()
    assert (await client.post(WEBHOOK_PATH, content=body)).status_code == 401
    assert await entry_count(owner_engine) == before


@pytest.mark.parametrize("signature", ["0" * 128, "garbage", "caf\xe9"])
async def test_a_bad_signature_is_refused_and_writes_nothing(
    owner_engine: AsyncEngine, client: httpx.AsyncClient, member: str, signature: str
) -> None:
    before = await entry_count(owner_engine)
    body = json.dumps(text_event(member, "makan 25rb")).encode()
    headers = [
        (SIGNATURE_HEADER.encode(), signature.encode("latin-1")),
        (ALGORITHM_HEADER.encode(), b"sha512"),
    ]
    assert (await client.post(WEBHOOK_PATH, content=body, headers=headers)).status_code == 403
    assert await entry_count(owner_engine) == before


@pytest.mark.parametrize("algorithm", ["sha256", "md5", "SHA512", ""])
async def test_any_other_signature_algorithm_is_refused(client: httpx.AsyncClient, member: str, algorithm: str) -> None:
    body = json.dumps(text_event(member, "makan 25rb")).encode()
    headers = {SIGNATURE_HEADER: signature_for(body), ALGORITHM_HEADER: algorithm}
    assert (await client.post(WEBHOOK_PATH, content=body, headers=headers)).status_code == 403


async def test_a_signature_from_another_secret_is_refused(client: httpx.AsyncClient, member: str) -> None:
    body = json.dumps(text_event(member, "makan 25rb")).encode()
    headers = {SIGNATURE_HEADER: signature_for(body, "another-secret"), ALGORITHM_HEADER: "sha512"}
    assert (await client.post(WEBHOOK_PATH, content=body, headers=headers)).status_code == 403


async def test_a_signature_for_another_body_is_refused(client: httpx.AsyncClient, member: str) -> None:
    signed_body = json.dumps(text_event(member, "makan 25rb")).encode()
    swapped_body = json.dumps(text_event(member, "makan 99rb")).encode()
    response = await client.post(WEBHOOK_PATH, content=swapped_body, headers=signed_headers(signed_body))
    assert response.status_code == 403


async def test_an_oversized_body_is_refused_before_the_signature_is_read(client: httpx.AsyncClient) -> None:
    response = await client.post(WEBHOOK_PATH, content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413


async def test_a_body_exactly_at_the_limit_reaches_the_signature_check(client: httpx.AsyncClient) -> None:
    response = await client.post(WEBHOOK_PATH, content=b"x" * MAX_BODY_BYTES)
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/", WEBHOOK_PATH])
async def test_nothing_else_is_served(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code in (404, 405)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"session": "default"}',
        b'{"session": "default", "event": "message"}',
        b'{"session": "default", "event": "message", "payload": {}}',
        b'{"session": "default", "event": "message", "payload": 5}',
        b'{"session": "default", "event": "session.status", "payload": {}}',
    ],
)
async def test_signed_nonsense_gets_a_400_and_no_crash(client: httpx.AsyncClient, body: bytes) -> None:
    assert (await client.post(WEBHOOK_PATH, content=body, headers=signed_headers(body))).status_code == 400


@pytest.mark.parametrize(
    ("field", "value"), [("id", ""), ("id", 5), ("from", 5), ("timestamp", "soon"), ("timestamp", 10**30)]
)
async def test_a_message_with_an_impossible_field_gets_a_400(
    owner_engine: AsyncEngine, bot: Bot, member: str, field: str, value: object
) -> None:
    event = text_event(member, "makan 25rb")
    event["payload"][field] = value
    assert (await bot.post(event)).status_code == 400
    assert await entries_of(owner_engine, member) == []


async def test_an_event_for_another_session_is_ignored(bot: Bot, member: str) -> None:
    event = text_event(member, "makan 25rb")
    event["session"] = "someone-else"
    assert (await bot.post(event)).status_code == 200
    assert bot.waha.texts == []


async def test_an_event_type_the_bot_does_not_use_is_ignored(bot: Bot, member: str) -> None:
    event = text_event(member, "makan 25rb")
    event["event"] = "message.any"
    assert (await bot.post(event)).status_code == 200
    assert bot.waha.texts == []


async def test_a_failed_handler_answers_500_so_the_retry_works(
    owner_engine: AsyncEngine, bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = text_event(member, "makan 25rb")

    def broken_parser(_: str) -> None:
        raise RuntimeError("the parser broke")

    with monkeypatch.context() as patch:
        patch.setattr(conversation_module, "parse_message", broken_parser)
        assert (await bot.post(event)).status_code == 500
    assert await entries_of(owner_engine, member) == []
    assert (await bot.post(event)).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000]


async def test_a_waha_outage_during_lookup_answers_500_so_nothing_is_lost(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    bot.waha.is_lookup_down = True
    event = text_event(member, "makan 25rb")
    assert (await bot.post(event)).status_code == 500
    bot.waha.is_lookup_down = False
    assert (await bot.post(event)).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000]


async def test_a_failed_reply_keeps_the_entry_and_does_not_ask_for_a_retry(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    bot.waha.is_down = True
    assert (await bot.say(member, "makan 25rb")).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000]


async def test_the_reply_goes_back_to_the_address_it_came_from(bot: Bot, member: str) -> None:
    await bot.say(member, "makan 25rb")
    assert bot.waha.sent_to[-1] == member


async def test_a_photo_with_a_caption_is_not_read_as_an_entry(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.post(image_event(member, "makan 25rb"))
    assert await entries_of(owner_engine, member) == []


@pytest.mark.parametrize("status", ["STOPPED", "FAILED", "WORKING", "STARTING"])
async def test_a_status_event_is_accepted_and_gets_no_reply(bot: Bot, status: str) -> None:
    assert (await bot.post(status_event(status))).status_code == 200
    assert bot.waha.texts == []


async def test_a_sender_with_no_lookup_result_is_a_stranger(bot: Bot) -> None:
    assert (await bot.say(new_lid(), "makan 25rb")).status_code == 200
    assert bot.waha.texts == []
