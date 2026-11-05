"""L6, R6, R7, T5: one person at their own limit can't use up everyone's replies.

Replies are limited across everyone, because a burst of sends is what gets a number
banned. Each person's daily message limit is sized so that every invited person at
their own limit still fits inside that shared reply limit.
"""

from typing import Final

from tracker.channel.ratelimit import MAX_MESSAGES_PER_DAY, MAX_REPLIES_PER_DAY, Admission, MessageLimiter

INVITED_PEOPLE: Final = 5  # The brief: Arthur and up to four others.


def test_every_invited_person_at_their_limit_fits_the_shared_reply_limit() -> None:
    assert MAX_MESSAGES_PER_DAY * INVITED_PEOPLE <= MAX_REPLIES_PER_DAY


async def test_two_people_at_their_limit_leave_replies_for_everyone_else() -> None:
    """The review's case: two heavy users used to take all 500 replies (2 x 300). The
    per-minute limits are lifted, since this is about a whole day's messages."""
    limiter = MessageLimiter(per_minute=MAX_REPLIES_PER_DAY, replies_per_minute=MAX_REPLIES_PER_DAY)
    for heavy_user in (1, 2):
        while await limiter.admit(heavy_user) is Admission.ALLOWED:
            assert await limiter.may_reply()
    for other_person in (3, 4, 5):
        assert await limiter.admit(other_person) is Admission.ALLOWED
        assert await limiter.may_reply()
