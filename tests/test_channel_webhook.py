"""The HTTP surface: nothing unsigned or oversized gets in, and a failure is never a quiet 200."""

import json

import httpx
import pytest
from conftest import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from webhook_fixtures import (
    AGREE,
    APP_SECRET,
    PHONE_NUMBER_ID,
    VERIFY_TOKEN,
    WEBHOOK_PATH,
    batch_update,
    button_update,
    new_bsuid,
    number_change_update,
    signature_for,
    status_update,
    text_update,
)

from tracker.channel import conversation as conversation_module
from tracker.channel.settings import Settings, SettingsError
from tracker.channel.webhook import MAX_BODY_BYTES, split_into_single_messages
from tracker.ledger.people import create_person

SIGNATURE_HEADER = "X-Hub-Signature-256"
GOOD_ENVIRONMENT = {
    "TRACKER_DATABASE_URL": "postgresql+asyncpg://app@localhost/tracker",
    "WHATSAPP_APP_SECRET": APP_SECRET,
    "WHATSAPP_VERIFY_TOKEN": VERIFY_TOKEN,
    "WHATSAPP_ACCESS_TOKEN": "token",
    "WHATSAPP_PHONE_NUMBER_ID": PHONE_NUMBER_ID,
    "BANTUAN_CONTACT": "contact",
}


async def entry_count(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as connection:
        return int(await connection.scalar(text("SELECT count(*) FROM entry")) or 0)


async def entries_of(owner_engine: AsyncEngine, bsuid: str) -> list[int]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT e.amount_rupiah FROM entry e JOIN person p ON p.id = e.person_id
                WHERE p.bsuid = :bsuid ORDER BY e.id
            """),
            {"bsuid": bsuid},
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


@pytest.mark.parametrize("name", ["WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"])
@pytest.mark.parametrize("value", ["change-me", "change-me-please-a-long-enough-one", "short"])
def test_a_placeholder_or_short_secret_stops_startup(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_environment({**GOOD_ENVIRONMENT, name: value})


def test_a_secret_of_exactly_the_minimum_length_is_allowed() -> None:
    Settings.from_environment({**GOOD_ENVIRONMENT, "WHATSAPP_APP_SECRET": "x" * 16})


def test_printing_the_settings_shows_no_secret() -> None:
    printed = repr(Settings.from_environment(GOOD_ENVIRONMENT))
    assert APP_SECRET not in printed
    assert VERIFY_TOKEN not in printed


async def test_a_request_with_no_signature_is_refused_and_writes_nothing(
    owner_engine: AsyncEngine, client: httpx.AsyncClient, member: str
) -> None:
    before = await entry_count(owner_engine)
    body = json.dumps(text_update(member, "makan 25rb")).encode()
    assert (await client.post(WEBHOOK_PATH, content=body)).status_code == 401
    assert await entry_count(owner_engine) == before


@pytest.mark.parametrize(
    "signature",
    [
        "sha256=" + "0" * 64,
        "sha256=",
        "sha1=abc",
        "garbage",
        "sha256=caf\xe9",
    ],
)
async def test_a_bad_signature_is_refused_and_writes_nothing(
    owner_engine: AsyncEngine, client: httpx.AsyncClient, member: str, signature: str
) -> None:
    before = await entry_count(owner_engine)
    body = json.dumps(text_update(member, "makan 25rb")).encode()
    headers = [(SIGNATURE_HEADER.encode(), signature.encode("latin-1"))]
    response = await client.post(WEBHOOK_PATH, content=body, headers=headers)
    assert response.status_code == 403
    assert await entry_count(owner_engine) == before


async def test_a_signature_without_its_prefix_is_refused(client: httpx.AsyncClient, member: str) -> None:
    body = json.dumps(text_update(member, "makan 25rb")).encode()
    bare_signature = signature_for(body).removeprefix("sha256=")
    response = await client.post(WEBHOOK_PATH, content=body, headers={SIGNATURE_HEADER: bare_signature})
    assert response.status_code == 403


async def test_a_signature_from_another_secret_is_refused(client: httpx.AsyncClient, member: str) -> None:
    body = json.dumps(text_update(member, "makan 25rb")).encode()
    headers = {SIGNATURE_HEADER: signature_for(body, "another-secret")}
    assert (await client.post(WEBHOOK_PATH, content=body, headers=headers)).status_code == 403


async def test_a_signature_for_another_body_is_refused(client: httpx.AsyncClient, member: str) -> None:
    signed_body = json.dumps(text_update(member, "makan 25rb")).encode()
    swapped_body = json.dumps(text_update(member, "makan 99rb")).encode()
    headers = {SIGNATURE_HEADER: signature_for(signed_body)}
    assert (await client.post(WEBHOOK_PATH, content=swapped_body, headers=headers)).status_code == 403


async def test_an_oversized_body_is_refused_before_the_signature_is_read(client: httpx.AsyncClient) -> None:
    response = await client.post(WEBHOOK_PATH, content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413


async def test_a_body_exactly_at_the_limit_reaches_the_signature_check(client: httpx.AsyncClient) -> None:
    response = await client.post(WEBHOOK_PATH, content=b"x" * MAX_BODY_BYTES)
    assert response.status_code == 401


async def test_the_verification_request_echoes_the_challenge_for_the_right_token(client: httpx.AsyncClient) -> None:
    parameters = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"}
    response = await client.get(WEBHOOK_PATH, params=parameters)
    assert (response.status_code, response.text) == (200, "12345")
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("token", ["wrong", "", "tökén"])
async def test_the_verification_request_is_refused_for_a_wrong_token(client: httpx.AsyncClient, token: str) -> None:
    parameters = {"hub.verify_token": token, "hub.challenge": "12345"}
    response = await client.get(WEBHOOK_PATH, params=parameters)
    assert response.status_code == 403
    assert "12345" not in response.text


async def test_the_verification_request_without_a_token_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get(WEBHOOK_PATH)).status_code == 403


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/"])
async def test_nothing_else_is_served(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 404


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"entry": [{}]}', b'{"entry": 5}'])
async def test_signed_nonsense_gets_a_400_and_no_crash(client: httpx.AsyncClient, body: bytes) -> None:
    response = await client.post(WEBHOOK_PATH, content=body, headers={SIGNATURE_HEADER: signature_for(body)})
    assert response.status_code == 400


async def test_a_failed_handler_answers_500_so_meta_retries_and_the_retry_works(
    owner_engine: AsyncEngine, bot: Bot, member: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = text_update(member, "makan 25rb")

    def broken_parser(_: str) -> None:
        raise RuntimeError("the parser broke")

    with monkeypatch.context() as patch:
        patch.setattr(conversation_module, "parse_message", broken_parser)
        assert (await bot.post(update)).status_code == 500
    assert await entries_of(owner_engine, member) == []
    assert (await bot.post(update)).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000]


async def test_a_message_pywa_cannot_read_answers_500(bot: Bot, member: str) -> None:
    update = text_update(member, "makan 25rb")
    del update["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"]
    assert (await bot.post(update)).status_code == 500


async def test_a_failed_reply_keeps_the_entry_and_does_not_ask_meta_to_retry(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    bot.meta.is_down = True
    assert (await bot.post(text_update(member, "makan 25rb"))).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000]


async def test_every_message_in_a_batch_reaches_its_own_sender(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    other_member = new_bsuid()
    await create_person(owner_engine, bsuid=other_member)
    await bot.post(text_update(other_member, "halo"))
    await bot.post(button_update(other_member, AGREE))
    update = batch_update((member, "makan 25rb"), (other_member, "kopi 18rb"), (member, "parkir 5rb"))
    assert (await bot.post(update)).status_code == 200
    assert await entries_of(owner_engine, member) == [25_000, 5_000]
    assert await entries_of(owner_engine, other_member) == [18_000]


async def test_a_batch_message_with_no_matching_sender_fails_rather_than_guessing(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    update = batch_update((member, "makan 25rb"), (new_bsuid(), "kopi 18rb"))
    value = update["entry"][0]["changes"][0]["value"]
    value["messages"][1]["from_user_id"] = "ID.nobody"
    assert (await bot.post(update)).status_code == 500
    assert await entries_of(owner_engine, member) == [25_000]


async def test_two_people_with_no_phone_numbers_are_not_the_same_person(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    update = batch_update((new_bsuid(), "kopi 18rb"), (member, "makan 25rb"))
    del update["entry"][0]["changes"][0]["value"]["contacts"][0]["user_id"]
    assert (await bot.post(update)).status_code == 500
    assert await entries_of(owner_engine, member) == [25_000]


async def test_a_message_and_a_contact_that_both_lack_a_bsuid_do_not_match(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    update = batch_update((new_bsuid(), "kopi 18rb"), (member, "makan 25rb"))
    value = update["entry"][0]["changes"][0]["value"]
    del value["contacts"][0]["user_id"]
    del value["messages"][0]["from_user_id"]
    assert (await bot.post(update)).status_code == 500
    assert await entries_of(owner_engine, member) == [25_000]


def test_a_batch_can_match_senders_by_phone_number() -> None:
    update = batch_update(("ID.one", "a"), ("ID.two", "b"))
    value = update["entry"][0]["changes"][0]["value"]
    for position, phone in enumerate(("628120000011", "628120000012")):
        del value["messages"][position]["from_user_id"]
        value["messages"][position]["from"] = phone
        value["contacts"][position]["wa_id"] = phone
    bodies, unmatched = split_into_single_messages(update)
    senders = [json.loads(body)["entry"][0]["changes"][0]["value"]["contacts"][0]["user_id"] for body in bodies]
    assert (senders, unmatched) == (["ID.one", "ID.two"], 0)


def test_the_split_counts_every_message_it_cannot_match() -> None:
    update = batch_update(("ID.one", "a"), ("ID.two", "b"))
    for message in update["entry"][0]["changes"][0]["value"]["messages"]:
        message["from_user_id"] = "ID.nobody"
    assert split_into_single_messages(update) == ([], 2)


def test_the_split_never_matches_two_missing_bsuids() -> None:
    update = batch_update(("ID.one", "a"), ("ID.two", "b"))
    value = update["entry"][0]["changes"][0]["value"]
    del value["contacts"][0]["user_id"]
    del value["messages"][0]["from_user_id"]
    assert split_into_single_messages(update)[1] == 1


def test_the_split_counts_a_message_it_cannot_match_and_leaves_it_out() -> None:
    bodies, unmatched = split_into_single_messages(batch_update(("ID.one", "a"), ("ID.two", "b")))
    assert (len(bodies), unmatched) == (2, 0)
    update = batch_update(("ID.one", "a"), ("ID.two", "b"))
    update["entry"][0]["changes"][0]["value"]["messages"][1]["from_user_id"] = "ID.nobody"
    bodies, unmatched = split_into_single_messages(update)
    assert (len(bodies), unmatched) == (1, 1)


async def test_a_message_with_no_contact_fails_rather_than_guessing(bot: Bot, member: str) -> None:
    update = text_update(member, "makan 25rb")
    del update["entry"][0]["changes"][0]["value"]["contacts"]
    assert (await bot.post(update)).status_code == 500


async def test_a_status_update_is_accepted_and_ignored(bot: Bot) -> None:
    assert (await bot.post(status_update())).status_code == 200
    assert bot.meta.sent == []


async def test_a_number_change_moves_the_person_to_the_new_id(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    new_id = new_bsuid()
    assert (await bot.post(number_change_update(member, new_id))).status_code == 200
    await bot.post(text_update(new_id, "makan 25rb"))
    assert await entries_of(owner_engine, new_id) == [25_000]


async def test_a_number_change_cannot_take_over_another_persons_id(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    taken_id = new_bsuid()
    await create_person(owner_engine, bsuid=taken_id)
    assert (await bot.post(number_change_update(member, taken_id))).status_code == 200
    await bot.post(text_update(member, "makan 25rb"))
    assert await entries_of(owner_engine, member) == [25_000]
