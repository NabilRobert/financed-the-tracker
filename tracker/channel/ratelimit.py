"""Message and reply limits (R6, R7, T5), on the `limits` library as in mcp-security-gateway.

Counts live in memory, so a restart resets them. That's fine for limits this far above
real use: they're here to stop a runaway client, not to be exact.

The reply limit replaces Meta's cap. WhatsApp bills nothing here, so what it guards is
the number: a burst of sends is what gets an account banned. Past it, entries are still
logged and only the echo stops, because losing a record is worse than losing a reply.
"""

from enum import StrEnum
from typing import Final

from limits import RateLimitItemPerDay, RateLimitItemPerHour, RateLimitItemPerMinute
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter

MAX_MESSAGES_PER_MINUTE: Final = 20
# Five invited people at 100 each fit inside the 500 shared replies, so one heavy user can't
# silence the rest (L6). Real use is a few dozen a day.
MAX_MESSAGES_PER_DAY: Final = 100
MAX_LIMIT_NOTICES_PER_HOUR: Final = 1  # A refusal can't turn into a reply flood of its own.
MAX_REPLIES_PER_MINUTE: Final = 30  # All people together. Five people typing at once stays well under this.
MAX_REPLIES_PER_DAY: Final = 500


class Admission(StrEnum):
    ALLOWED = "allowed"
    REFUSED = "refused"
    REFUSED_WITH_NOTICE = "refused_with_notice"


class MessageLimiter:
    def __init__(
        self,
        per_minute: int = MAX_MESSAGES_PER_MINUTE,
        per_day: int = MAX_MESSAGES_PER_DAY,
        replies_per_minute: int = MAX_REPLIES_PER_MINUTE,
        replies_per_day: int = MAX_REPLIES_PER_DAY,
    ) -> None:
        self._limits = (RateLimitItemPerMinute(per_minute), RateLimitItemPerDay(per_day))
        self._notice_limit = RateLimitItemPerHour(MAX_LIMIT_NOTICES_PER_HOUR)
        self._reply_limits = (RateLimitItemPerMinute(replies_per_minute), RateLimitItemPerDay(replies_per_day))
        self._limiter = FixedWindowRateLimiter(MemoryStorage())

    async def admit(self, person_id: int) -> Admission:
        key = str(person_id)
        for limit in self._limits:
            if not await self._limiter.hit(limit, key):
                return await self._refuse(key)
        return Admission.ALLOWED

    async def may_reply(self) -> bool:
        """Counts one reply across everyone, False past the limit."""
        for limit in self._reply_limits:
            if not await self._limiter.hit(limit, "all-replies"):
                return False
        return True

    async def _refuse(self, key: str) -> Admission:
        may_send_notice = await self._limiter.hit(self._notice_limit, key, "notice")
        return Admission.REFUSED_WITH_NOTICE if may_send_notice else Admission.REFUSED
