"""Per-person message limits (R6, T5), on the `limits` library as in mcp-security-gateway.

Counts live in memory, so a restart resets them. That's fine for a limit this far above
real use: it's here to stop a runaway client costing money, not to be exact.
"""

from enum import StrEnum
from typing import Final

from limits import RateLimitItemPerDay, RateLimitItemPerHour, RateLimitItemPerMinute
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter

MAX_MESSAGES_PER_MINUTE: Final = 20
MAX_MESSAGES_PER_DAY: Final = 300
MAX_LIMIT_NOTICES_PER_HOUR: Final = 1  # A refusal can't turn into a reply flood of its own.


class Admission(StrEnum):
    ALLOWED = "allowed"
    REFUSED = "refused"
    REFUSED_WITH_NOTICE = "refused_with_notice"


class MessageLimiter:
    def __init__(self, per_minute: int = MAX_MESSAGES_PER_MINUTE, per_day: int = MAX_MESSAGES_PER_DAY) -> None:
        self._limits = (RateLimitItemPerMinute(per_minute), RateLimitItemPerDay(per_day))
        self._notice_limit = RateLimitItemPerHour(MAX_LIMIT_NOTICES_PER_HOUR)
        self._limiter = FixedWindowRateLimiter(MemoryStorage())

    async def admit(self, person_id: int) -> Admission:
        key = str(person_id)
        for limit in self._limits:
            if not await self._limiter.hit(limit, key):
                return await self._refuse(key)
        return Admission.ALLOWED

    async def _refuse(self, key: str) -> Admission:
        may_send_notice = await self._limiter.hit(self._notice_limit, key, "notice")
        return Admission.REFUSED_WITH_NOTICE if may_send_notice else Admission.REFUSED
