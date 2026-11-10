"""Tests for the circuit breaker and daily limit (standard library only).

Run from the project root:  python -m unittest discover -s tests -v
"""
import unittest

from app.guard import Breaker, DailyQuota


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class BreakerTests(unittest.TestCase):
    def test_closed_by_default(self):
        b = Breaker(FakeClock())
        self.assertFalse(b.is_open)
        self.assertEqual(b.remaining, 0)

    def test_trip_and_recover(self):
        clock = FakeClock()
        b = Breaker(clock)
        b.trip(120, "таймаут")
        self.assertTrue(b.is_open)
        self.assertEqual(b.remaining, 120)
        clock.t += 119.2
        self.assertTrue(b.is_open)
        clock.t += 1
        self.assertFalse(b.is_open)

    def test_shorter_trip_does_not_shorten(self):
        clock = FakeClock()
        b = Breaker(clock)
        b.trip(900, "ключ")
        b.trip(60, "5xx")
        self.assertEqual(b.remaining, 900)
        self.assertEqual(b.reason, "ключ")

    def test_reset(self):
        b = Breaker(FakeClock())
        b.trip(60, "x")
        b.reset()
        self.assertFalse(b.is_open)


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_in_memory(self):
        q = DailyQuota("t", 3)
        self.assertEqual([await q.take() for _ in range(5)], [True, True, True, False, False])
        self.assertEqual(await q.used(), 5)

    async def test_zero_means_unlimited(self):
        q = DailyQuota("t", 0)
        self.assertTrue(all([await q.take() for _ in range(50)]))

    async def test_new_day_resets_memory_counter(self):
        day = {"d": "2026-11-07"}
        q = DailyQuota("t", 1, today=lambda: day["d"])
        self.assertTrue(await q.take())
        self.assertFalse(await q.take())
        day["d"] = "2026-11-08"
        self.assertTrue(await q.take())

    async def test_uses_redis_counter_when_available(self):
        store = {}

        async def incr(key, ttl):
            store[key] = store.get(key, 0) + 1
            return store[key]

        async def get(key):
            return store.get(key, 0)

        q = DailyQuota("t", 2, incr=incr, get=get)
        self.assertEqual([await q.take() for _ in range(3)], [True, True, False])
        self.assertEqual(await q.used(), 3)
        self.assertTrue(next(iter(store)).startswith("ai:quota:t:"))

    async def test_falls_back_to_memory_when_redis_returns_none(self):
        async def incr(key, ttl):
            return None

        q = DailyQuota("t", 1, incr=incr)
        self.assertTrue(await q.take())
        self.assertFalse(await q.take())

    async def test_falls_back_to_memory_when_redis_raises(self):
        async def incr(key, ttl):
            raise ConnectionError("down")

        q = DailyQuota("t", 1, incr=incr)
        self.assertTrue(await q.take())
        self.assertFalse(await q.take())


if __name__ == "__main__":
    unittest.main()
