"""The WAHA adapter on its own: what it trusts, and exactly what it sends."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from webhook_fixtures import API_KEY, SESSION, WAHA_URL, WEBHOOK_KEY, FakeWaha, status_event, text_event

from tracker.channel.gateway import ChannelStopped, IncomingMessage, MalformedEventError, Sender
from tracker.channel.waha import WahaGateway

LID = "12345@lid"
PHONE = "628120000021"
PHONE_CHAT = f"{PHONE}@c.us"


def gateway_for(waha: FakeWaha) -> WahaGateway:
    client = httpx.AsyncClient(transport=waha.transport())
    return WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY)


def read(event: dict[str, Any]) -> IncomingMessage | ChannelStopped | None:
    return gateway_for(FakeWaha()).read_event(event)


def test_a_text_message_is_read_exactly() -> None:
    event = text_event(LID, "makan 25rb", message_id="m1", sent_at=1_800_000_000)
    assert read(event) == IncomingMessage("m1", LID, "makan 25rb", datetime.fromtimestamp(1_800_000_000, tz=UTC))


@pytest.mark.parametrize("chat_id", [LID, PHONE_CHAT])
def test_direct_chats_are_read(chat_id: str) -> None:
    assert read(text_event(chat_id, "halo")) is not None


@pytest.mark.parametrize(
    "chat_id",
    ["120363000000000000@g.us", "status@broadcast", "status@c.us", "120363000000000000@newsletter", "x", ""],
)
def test_only_direct_chats_reach_the_bot(chat_id: str) -> None:
    assert read(text_event(chat_id, "makan 25rb")) is None


def test_the_bots_own_messages_are_ignored() -> None:
    assert read(text_event(LID, "makan 25rb", from_me=True)) is None


def test_an_empty_body_counts_as_not_text() -> None:
    message = read(text_event(LID, ""))
    assert isinstance(message, IncomingMessage)
    assert message.text is None


def test_a_missing_from_me_is_malformed_rather_than_assumed() -> None:
    event = text_event(LID, "halo")
    del event["payload"]["fromMe"]
    with pytest.raises(MalformedEventError):
        read(event)


@pytest.mark.parametrize("status", ["STOPPED", "FAILED"])
def test_a_stopped_session_is_reported(status: str) -> None:
    assert read(status_event(status)) == ChannelStopped(status)


@pytest.mark.parametrize("status", ["WORKING", "STARTING", "SCAN_QR_CODE"])
def test_a_healthy_status_is_not_reported(status: str) -> None:
    assert read(status_event(status)) is None


async def test_a_lid_sender_keeps_the_lid_and_learns_the_phone() -> None:
    waha = FakeWaha()
    waha.pair(LID, PHONE)
    assert await gateway_for(waha).identify(LID) == Sender(LID, PHONE, LID)


async def test_a_lid_with_no_known_phone_has_none() -> None:
    assert await gateway_for(FakeWaha()).identify(LID) == Sender(LID, None, LID)


async def test_a_phone_sender_becomes_its_lid_and_replies_to_where_it_came_from() -> None:
    waha = FakeWaha()
    waha.pair(LID, PHONE)
    assert await gateway_for(waha).identify(PHONE_CHAT) == Sender(LID, PHONE, PHONE_CHAT)


async def test_a_phone_sender_with_no_lid_stays_on_its_chat_id() -> None:
    assert await gateway_for(FakeWaha()).identify(PHONE_CHAT) == Sender(PHONE_CHAT, PHONE, PHONE_CHAT)


@pytest.mark.parametrize("pairing", [{"lid": LID, "pn": None}, {"lid": LID}, {"lid": LID, "pn": "garbage"}])
async def test_a_lookup_with_no_usable_phone_gives_none(pairing: dict[str, Any]) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=pairing))
    client = httpx.AsyncClient(transport=transport)
    gateway = WahaGateway(client, WAHA_URL, API_KEY, SESSION, WEBHOOK_KEY)
    assert (await gateway.identify(LID)).phone is None


async def test_a_lookup_outage_raises_rather_than_treating_the_sender_as_a_stranger() -> None:
    waha = FakeWaha(is_lookup_down=True)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway_for(waha).identify(LID)


async def test_a_lookup_escapes_the_id_so_it_cannot_change_the_path() -> None:
    waha = FakeWaha()
    await gateway_for(waha).identify("../sendText@lid")
    assert b"/lids/..%2FsendText%40lid" in waha.requests[0].url.raw_path


async def test_sending_marks_the_chat_seen_then_replies_with_previews_off() -> None:
    waha = FakeWaha()
    await gateway_for(waha).send_text(LID, "halo")
    paths = [request.url.path for request in waha.requests]
    assert paths == ["/api/sendSeen", "/api/sendText"]
    assert json.loads(waha.requests[1].content) == {
        "session": SESSION,
        "chatId": LID,
        "text": "halo",
        "linkPreview": False,
    }


async def test_a_failed_send_raises() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await gateway_for(FakeWaha(is_down=True)).send_text(LID, "halo")


async def test_the_api_key_travels_in_a_header_only() -> None:
    waha = FakeWaha()
    gateway = gateway_for(waha)
    await gateway.identify(LID)
    await gateway.send_text(LID, "halo")
    for request in waha.requests:
        assert request.headers["X-Api-Key"] == API_KEY
        assert API_KEY not in str(request.url)
