"""T3: a signed event more than a day old is a replay, not a delivery, and is dropped.

The message id stops a repeat, but a bare id is purged after 30 days, so a captured webhook
replayed after that would count twice. The payload's `timestamp` is inside the signed body,
so its age can be trusted, and WAHA's own retries finish within minutes.
"""

import time
from datetime import UTC, datetime, timedelta

from conftest import Bot
from webhook_fixtures import text_event

from tracker.channel.webhook import MAX_EVENT_AGE, is_stale
from tracker.ledger.retention import BARE_MESSAGE_ID_RETENTION

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SECONDS_PER_DAY = 24 * 60 * 60


def test_an_event_older_than_the_limit_is_stale() -> None:
    assert is_stale(NOW - MAX_EVENT_AGE - timedelta(seconds=1), NOW)


def test_an_event_exactly_at_the_limit_is_still_delivered() -> None:
    assert not is_stale(NOW - MAX_EVENT_AGE, NOW)


def test_an_event_from_a_minute_ago_is_delivered() -> None:
    assert not is_stale(NOW - timedelta(minutes=1), NOW)


def test_the_limit_is_far_inside_how_long_a_message_id_is_kept() -> None:
    assert MAX_EVENT_AGE * 7 <= BARE_MESSAGE_ID_RETENTION


async def test_a_replayed_message_two_days_old_gets_no_reply(bot: Bot, member: str) -> None:
    two_days_ago = int(time.time()) - 2 * SECONDS_PER_DAY
    response = await bot.post(text_event(member, "makan 25rb", sent_at=two_days_ago))
    assert response.status_code == 200  # So WAHA doesn't retry what is dropped on purpose.
    assert bot.waha.texts == []
