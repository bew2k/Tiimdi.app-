"""Redis helpers. If Redis is unavailable, the API keeps working without a cache.

If Redis does not respond, we do not try it again for 10 seconds (otherwise every request would wait for the timeout).
"""
import json
import logging

import redis.asyncio as aioredis

from .config import CACHE_TTL, REDIS_URL
from .guard import Breaker

log = logging.getLogger("tiimdi.cache")

r: aioredis.Redis | None = None
_down = Breaker()


def _client():
    """Redis client, or None if it recently failed to respond."""
    return None if (r is None or _down.is_open) else r


def _fail(e: Exception) -> None:
    if not _down.is_open:
        log.warning("Redis недоступен (%s), работаем без кеша", e.__class__.__name__)
    _down.trip(10, "redis")


async def init_cache() -> None:
    global r
    r = aioredis.from_url(REDIS_URL, decode_responses=True,
                          socket_connect_timeout=1, socket_timeout=2)
    await ping()


async def close_cache() -> None:
    if r:
        try:
            await r.aclose()
        except Exception:
            pass


async def ping() -> bool:
    """For /health: whether Redis responds right now."""
    if r is None:
        return False
    try:
        await r.ping()
        _down.reset()
        return True
    except Exception as e:
        _fail(e)
        return False


async def _version(c) -> str:
    return (await c.get("cache_ver")) or "0"


async def cache_get(key: str):
    c = _client()
    if not c:
        return None
    try:
        raw = await c.get(f"c:{await _version(c)}:{key}")
        return json.loads(raw) if raw else None
    except Exception as e:
        _fail(e)
        return None


async def cache_set(key: str, value) -> None:
    c = _client()
    if not c:
        return
    try:
        await c.set(f"c:{await _version(c)}:{key}", json.dumps(value, default=str), ex=CACHE_TTL)
    except Exception as e:
        _fail(e)


async def invalidate() -> None:
    """Called after any write: bumping the version makes all old cache keys unreachable."""
    c = _client()
    if not c:
        return
    try:
        await c.incr("cache_ver")
    except Exception as e:
        _fail(e)


async def first_time(key: str, ttl: int = 86400) -> bool:
    """True the first time `key` is seen within ttl (used to allow one upvote per client per deal)."""
    c = _client()
    if not c:
        return True
    try:
        return bool(await c.set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        _fail(e)
        return True


async def incr_ttl(key: str, ttl: int) -> int | None:
    """Counter with a time to live. Returns the new value, or None if Redis is unavailable.

    If the key somehow ended up without a TTL (TTL = -1), the TTL is set again,
    so an "eternal" lock cannot occur.
    """
    c = _client()
    if not c:
        return None
    try:
        async with c.pipeline(transaction=False) as p:
            p.incr(key)
            p.ttl(key)
            n, left = await p.execute()
        if n == 1 or left < 0:
            await c.expire(key, ttl)
        return int(n)
    except Exception as e:
        _fail(e)
        return None


async def get_int(key: str) -> int | None:
    c = _client()
    if not c:
        return None
    try:
        raw = await c.get(key)
        return int(raw) if raw is not None else 0
    except Exception as e:
        _fail(e)
        return None


async def rate_limit(key: str, limit: int, window: int = 60) -> bool:
    """True if the request is allowed."""
    n = await incr_ttl(key, window)
    return True if n is None else n <= limit


async def raw_get(key: str):
    """Cache not tied to a version (for AI responses: they need not be invalidated on every write)."""
    c = _client()
    if not c:
        return None
    try:
        raw = await c.get(f"raw:{key}")
        return json.loads(raw) if raw else None
    except Exception as e:
        _fail(e)
        return None


async def raw_set(key: str, value, ttl: int = 3600) -> None:
    c = _client()
    if not c:
        return
    try:
        await c.set(f"raw:{key}", json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        _fail(e)
