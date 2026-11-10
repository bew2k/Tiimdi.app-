"""Redis helpers. Если Redis недоступен, API продолжает работать без кеша.

Если Redis не отвечает, 10 секунд не пытаемся к нему обращаться (иначе каждый запрос ждал бы таймаут).
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
    """Клиент Redis или None, если он недавно не отвечал."""
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
    """Для /health: отвечает ли Redis прямо сейчас."""
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
    """Счётчик со сроком жизни. Возвращает новое значение или None, если Redis недоступен.

    Если ключ по какой-то причине остался без срока жизни (TTL = -1), срок ставится заново,
    так что «вечной» блокировки не бывает.
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
    """Кеш без привязки к версии (для ответов ИИ: их не нужно сбрасывать при каждой записи)."""
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
