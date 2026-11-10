"""Protection against external AI failures. Standard library only, so it is easy to test.

Breaker    - circuit breaker: after a failure we stop calling the AI for a while and return the fallback
             right away (no timeout wait on every request).
DailyQuota - daily call cap, so we do not exhaust the free quota or waste tokens.
"""
import math
import time
from datetime import datetime, timezone


class Breaker:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._until = 0.0
        self.reason = ""

    def trip(self, seconds: float, reason: str = "") -> None:
        """Disable for `seconds`. A shorter disable does not shorten one already in effect."""
        until = self._clock() + seconds
        if until > self._until:
            self._until, self.reason = until, reason

    @property
    def remaining(self) -> int:
        """How many seconds remain disabled (0 = working)."""
        return max(0, math.ceil(self._until - self._clock()))

    @property
    def is_open(self) -> bool:
        return self.remaining > 0

    def reset(self) -> None:
        self._until, self.reason = 0.0, ""


class DailyQuota:
    """Per-day call counter (UTC). Stored in Redis if available, otherwise in process memory.

    incr(key, ttl) -> int | None   increment the counter; None if Redis is unavailable
    get(key)       -> int | None   read the counter; None if Redis is unavailable
    """

    def __init__(self, name: str, limit: int, incr=None, get=None, today=None):
        self.name, self.limit = name, limit
        self._incr, self._get = incr, get
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._mem_day, self._mem_n = "", 0

    def _key(self) -> str:
        return f"ai:quota:{self.name}:{self._today()}"

    async def take(self) -> bool:
        """Claim one call. False if the daily limit is already exhausted. limit<=0 means no limit."""
        if self.limit <= 0:
            return True
        n = None
        if self._incr:
            try:
                n = await self._incr(self._key(), 90000)
            except Exception:
                n = None
        if n is None:                                   # Redis unavailable -> count in memory
            day = self._today()
            if day != self._mem_day:
                self._mem_day, self._mem_n = day, 0
            self._mem_n += 1
            n = self._mem_n
        return n <= self.limit

    async def used(self) -> int:
        n = None
        if self._get:
            try:
                n = await self._get(self._key())
            except Exception:
                n = None
        if n is None:
            return self._mem_n if self._mem_day == self._today() else 0
        return n
