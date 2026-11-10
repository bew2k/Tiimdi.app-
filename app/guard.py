"""Защита от сбоев внешнего ИИ. Только стандартная библиотека, поэтому легко тестируется.

Breaker    - «выключатель»: после сбоя на время перестаём ходить к ИИ и сразу отдаём запасной вариант
             (не ждём таймаут на каждом запросе).
DailyQuota - дневной потолок вызовов, чтобы не выбить бесплатный лимит и не потратить токены.
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
        """Отключить на `seconds`. Более короткое отключение не сокращает уже действующее."""
        until = self._clock() + seconds
        if until > self._until:
            self._until, self.reason = until, reason

    @property
    def remaining(self) -> int:
        """Сколько секунд ещё отключено (0 = работает)."""
        return max(0, math.ceil(self._until - self._clock()))

    @property
    def is_open(self) -> bool:
        return self.remaining > 0

    def reset(self) -> None:
        self._until, self.reason = 0.0, ""


class DailyQuota:
    """Счётчик вызовов за сутки (UTC). Хранится в Redis (если он есть), иначе в памяти процесса.

    incr(key, ttl) -> int | None   увеличить счётчик; None, если Redis недоступен
    get(key)       -> int | None   прочитать счётчик; None, если Redis недоступен
    """

    def __init__(self, name: str, limit: int, incr=None, get=None, today=None):
        self.name, self.limit = name, limit
        self._incr, self._get = incr, get
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._mem_day, self._mem_n = "", 0

    def _key(self) -> str:
        return f"ai:quota:{self.name}:{self._today()}"

    async def take(self) -> bool:
        """Занять один вызов. False, если дневной лимит уже исчерпан. limit<=0 - без лимита."""
        if self.limit <= 0:
            return True
        n = None
        if self._incr:
            try:
                n = await self._incr(self._key(), 90000)
            except Exception:
                n = None
        if n is None:                                   # Redis недоступен -> считаем в памяти
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
