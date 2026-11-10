"""Offline tests for the AI layer: the real HTTP client is replaced with a stub.

Cover the "AI unavailable" scenarios: timeout, exhausted quota, invalid key,
circuit breaker, fallback provider, daily cap, garbage in the model response.

Run from the project root:  python -m unittest discover -s tests -v
"""
import logging
import unittest

from app import ai


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def ok(text):
    return FakeResp(200, {"choices": [{"message": {"content": text}}]})


class FakeClient:
    script: list = []
    calls: list = []

    def __init__(self, *a, **k):
        self.timeout = k.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.calls.append({"url": url, "timeout": self.timeout, "headers": headers})
        item = FakeClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


DEAL = {"title": "Комбо", "venue_name": "Донерная", "price": 800, "old_price": 1000,
        "discount_pct": 20, "distance_m": 150, "upvotes": 5}


class AITestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)             # expected errors do not clutter test output
        self._client, self._providers = ai.httpx.AsyncClient, ai.get_providers
        ai.httpx.AsyncClient = FakeClient
        FakeClient.script, FakeClient.calls = [], []
        self.primary = ai.OpenAICompat("primary", "http://ai.test/v1", "secret-key", "model-a")
        self.fallback = ai.OpenAICompat("fallback", "http://fb.test/v1", "fb-key", "model-b", vision=False)
        self.chain = [self.primary]
        ai.get_providers = lambda: self.chain

    def tearDown(self):
        logging.disable(logging.NOTSET)
        ai.httpx.AsyncClient, ai.get_providers = self._client, self._providers


class ProviderTests(AITestCase):
    async def test_success_and_auth_header(self):
        FakeClient.script = [ok("привет")]
        self.assertEqual(await self.primary.chat("s", "u"), "привет")
        self.assertEqual(FakeClient.calls[0]["url"], "http://ai.test/v1/chat/completions")
        self.assertEqual(FakeClient.calls[0]["headers"], {"Authorization": "Bearer secret-key"})

    async def test_text_and_vision_use_different_timeouts(self):
        FakeClient.script = [ok("a"), ok("b")]
        await self.primary.chat("s", "u")
        await self.primary.chat("s", "u", image=("image/png", b"x"))
        self.assertEqual(FakeClient.calls[0]["timeout"], ai.AI_TEXT_TIMEOUT)
        self.assertEqual(FakeClient.calls[1]["timeout"], ai.AI_TIMEOUT)
        self.assertLess(ai.AI_TEXT_TIMEOUT, ai.AI_TIMEOUT)

    async def test_rate_limit_trips_breaker_and_next_call_skips_network(self):
        FakeClient.script = [FakeResp(429, {}, {"retry-after": "120"})]
        with self.assertRaises(ai.AIError) as cm:
            await self.primary.chat("s", "u")
        self.assertEqual(cm.exception.status, 429)
        self.assertTrue(self.primary.breaker.is_open)
        self.assertEqual(self.primary.breaker.remaining, 120)
        with self.assertRaises(ai.AIError) as cm2:
            await self.primary.chat("s", "u")
        self.assertEqual(cm2.exception.status, 503)
        self.assertEqual(len(FakeClient.calls), 1)          # the second call never reached the network

    async def test_retry_after_is_clamped(self):
        FakeClient.script = [FakeResp(429, {}, {"retry-after": "999999"})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")
        self.assertEqual(self.primary.breaker.remaining, 3600)

    async def test_429_without_header_uses_default(self):
        FakeClient.script = [FakeResp(429, {}, {})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")
        self.assertEqual(self.primary.breaker.remaining, 300)

    async def test_timeout(self):
        FakeClient.script = [ai.httpx.TimeoutException("slow")]
        with self.assertRaises(ai.AIError) as cm:
            await self.primary.chat("s", "u")
        self.assertEqual(cm.exception.status, 504)
        self.assertTrue(self.primary.breaker.is_open)

    async def test_network_error(self):
        FakeClient.script = [ai.httpx.ConnectError("down")]
        with self.assertRaises(ai.AIError) as cm:
            await self.primary.chat("s", "u")
        self.assertEqual(cm.exception.status, 502)
        self.assertNotIn("secret-key", str(cm.exception))

    async def test_bad_key(self):
        FakeClient.script = [FakeResp(401, {})]
        with self.assertRaises(ai.AIError) as cm:
            await self.primary.chat("s", "u")
        self.assertIn("AI_API_KEY", str(cm.exception))
        self.assertEqual(self.primary.breaker.remaining, 900)

    async def test_server_error_trips_but_client_error_does_not(self):
        FakeClient.script = [FakeResp(400, {})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")
        self.assertFalse(self.primary.breaker.is_open)
        FakeClient.script = [FakeResp(503, {})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")
        self.assertTrue(self.primary.breaker.is_open)

    async def test_null_content_and_list_content(self):
        FakeClient.script = [ok(None), ok([{"type": "text", "text": "аб"}, {"type": "text", "text": "вг"}])]
        self.assertEqual(await self.primary.chat("s", "u"), "")
        self.assertEqual(await self.primary.chat("s", "u"), "абвг")

    async def test_garbage_response(self):
        FakeClient.script = [FakeResp(200, {"oops": 1})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")

    async def test_not_configured(self):
        p = ai.OpenAICompat("primary", "", "", "")
        with self.assertRaises(ai.AIError) as cm:
            await p.chat("s", "u")
        self.assertEqual(cm.exception.status, 503)

    async def test_daily_limit_blocks_without_network(self):
        self.primary.quota.limit = 2
        FakeClient.script = [ok("1"), ok("2"), ok("3")]
        await self.primary.chat("s", "u")
        await self.primary.chat("s", "u")
        with self.assertRaises(ai.AIError) as cm:
            await self.primary.chat("s", "u")
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(len(FakeClient.calls), 2)


class ChainTests(AITestCase):
    async def test_fallback_answers_when_primary_down(self):
        self.chain = [self.primary, self.fallback]
        FakeClient.script = [FakeResp(500, {}), ok("совет от запасного")]
        text, p = await ai._chat("s", "u")
        self.assertEqual((text, p.role), ("совет от запасного", "fallback"))

    async def test_fallback_skipped_for_photos_if_no_vision(self):
        self.chain = [self.primary, self.fallback]
        FakeClient.script = [FakeResp(500, {})]
        with self.assertRaises(ai.AIError):
            await ai._chat("s", "u", image=("image/png", b"x"))
        self.assertEqual(len(FakeClient.calls), 1)

    async def test_all_down_raises_aierror(self):
        self.chain = [self.primary, self.fallback]
        FakeClient.script = [FakeResp(500, {}), FakeResp(500, {})]
        with self.assertRaises(ai.AIError):
            await ai._chat("s", "u")

    async def test_unexpected_exception_becomes_aierror(self):
        FakeClient.script = [RuntimeError("bug")]
        with self.assertRaises(ai.AIError):
            await ai._chat("s", "u")


class FeatureTests(AITestCase):
    async def test_explain_returns_text_and_limits_length(self):
        FakeClient.script = [ok("  Берите комбо. " + "я" * 600)]
        text = await ai.explain([DEAL], 1200)
        self.assertTrue(text.startswith("Берите комбо."))
        self.assertLessEqual(len(text), 500)

    async def test_explain_empty_answer_is_error(self):
        FakeClient.script = [ok("   ")]
        with self.assertRaises(ai.AIError):
            await ai.explain([DEAL], 1200)

    async def test_extract_menu_validates_and_drops_garbage(self):
        FakeClient.script = [ok('```json\n{"items":['
                                '{"name":"Борщ","price":"1 200 ₸","category":"soup"},'
                                '{"name":"Борщ","price":1200,"category":"soup"},'
                                '{"name":"Чай","price":250,"category":"???"},'
                                '{"name":"Без цены","price":"нет"}]}\n```')]
        res = await ai.extract_menu(b"\xff\xd8\xff", "image/jpeg")
        self.assertEqual([(i["name"], i["price"], i["category"]) for i in res["items"]],
                         [("Борщ", 1200, "soup"), ("Чай", 250, "other")])
        self.assertEqual(res["dropped"], 1)
        self.assertEqual(res["provider"], "openai_compat")

    async def test_extract_menu_nothing_found(self):
        FakeClient.script = [ok('{"items": []}')]
        with self.assertRaises(ai.AIError) as cm:
            await ai.extract_menu(b"\xff\xd8\xff", "image/jpeg")
        self.assertEqual(cm.exception.status, 422)

    async def test_non_json_answer(self):
        FakeClient.script = [ok("Извините, не могу")]
        with self.assertRaises(ai.AIError):
            await ai.extract_menu(b"\xff\xd8\xff", "image/jpeg")

    async def test_status_hides_secrets(self):
        self.chain = [self.primary, self.fallback]
        st = await ai.status()
        self.assertEqual(st["provider"], "openai_compat")
        self.assertTrue(st["available"])
        self.assertEqual([p["role"] for p in st["providers"]], ["primary", "fallback"])
        dump = str(st)
        self.assertNotIn("secret-key", dump)
        self.assertNotIn("ai.test", dump)

    async def test_status_shows_outage(self):
        FakeClient.script = [FakeResp(500, {})]
        with self.assertRaises(ai.AIError):
            await self.primary.chat("s", "u")
        st = await ai.status()
        self.assertFalse(st["available"])
        self.assertGreater(st["providers"][0]["down_for_s"], 0)
        self.assertEqual(st["providers"][0]["used_today"], 1)

    async def test_mock_provider_menu_works_without_network(self):
        self.chain = [ai.MockProvider()]
        res = await ai.extract_menu(b"\xff\xd8\xff", "image/jpeg")
        self.assertEqual(len(res["items"]), 5)
        self.assertEqual(res["items"][1]["price"], 1100)       # "1 100 ₸" -> 1100
        self.assertEqual(res["provider"], "mock")


if __name__ == "__main__":
    unittest.main()
