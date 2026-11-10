import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, cache, db, ranking
from .config import TRUST_PROXY
from .profanity import has_profanity

log = logging.getLogger("tiimdi.api")

Category = Literal["canteen", "fastfood", "cafe", "bakery", "grocery", "coffee"]
Sort = Literal["best", "price", "distance", "discount"]
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await cache.init_cache()
    yield
    await cache.close_cache()
    await db.close_db()


app = FastAPI(
    title="Tiimdi API",
    description="Food deals geo-aggregator: PostGIS + Redis. Data is mock data from CSV.",
    version="0.21.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # MVP only
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_DEVICE = re.compile(r"^[A-Za-z0-9-]{16,64}$")


def author_key(request: Request) -> str:
    """Who left the review: device ID from the X-Device-Id header, otherwise the client address.
    Without account login this protects against accidental repeats, not against deliberate vote/review stuffing."""
    d = request.headers.get("x-device-id", "")
    return ("d:" + d) if _DEVICE.match(d) else "ip:" + client_id(request)


def client_id(request: Request) -> str:
    """Client identifier for rate limits and upvotes.

    In Docker and behind a proxy all requests arrive via the proxy, so request.client.host is the same for everyone:
    without this, "1 upvote per day" and "10 deals per minute" would apply to all users at once.
    We take the last address from X-Forwarded-For: our proxy appends it, so the client cannot forge it.
    """
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        last = xff.split(",")[-1].strip()
        if last:
            return last[:64]
    return request.client.host if request.client else "unknown"


Lat = Query(..., ge=-90, le=90, description="Latitude, e.g. 43.2352")
Lon = Query(..., ge=-180, le=180, description="Longitude, e.g. 76.9094")


async def _ranked(lat, lon, radius, category, max_price, sort, limit, budget=None):
    """Shared by /deals/nearby, /deals/trending, /recommend. Cached in Redis."""
    key = f"n:{lat:.4f}:{lon:.4f}:{radius}:{category}:{max_price}:{sort}:{limit}:{budget}"
    hit = await cache.cache_get(key)
    if hit is not None:
        return hit, True
    deals = await db.deals_nearby(lat, lon, radius, category, max_price)
    deals = ranking.enrich(deals, radius, budget)
    deals.sort(key=ranking.SORTS[sort])
    deals = deals[:limit]
    await cache.cache_set(key, deals)
    return deals, False


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "redis": await cache.ping(), "ai": await ai.status()}


@app.get("/deals/nearby", tags=["deals"], summary="Deals within a radius (PostGIS ST_DWithin)")
async def deals_nearby(
    lat: float = Lat, lon: float = Lon,
    radius: int = Query(500, ge=50, le=5000, description="meters"),
    category: Category | None = None,
    max_price: int | None = Query(None, ge=0, description="₸, filter such as 'up to 1000 ₸'"),
    sort: Sort = "best",
    limit: int = Query(20, ge=1, le=100),
):
    deals, cached = await _ranked(lat, lon, radius, category, max_price, sort, limit)
    return {"count": len(deals), "cached": cached, "items": deals}


@app.get("/deals/trending", tags=["deals"], summary="Trending nearby: top by value score")
async def trending(lat: float = Lat, lon: float = Lon,
                   radius: int = Query(1000, ge=50, le=5000), limit: int = Query(5, ge=1, le=20)):
    deals, cached = await _ranked(lat, lon, radius, None, None, "best", limit)
    return {"count": len(deals), "cached": cached, "items": deals}


@app.get("/recommend", tags=["deals"], summary="Advice on 'what to get for your budget' (rules, optionally AI)")
async def recommend(lat: float = Lat, lon: float = Lon,
                    budget: int = Query(1200, ge=100, description="₸"),
                    radius: int = Query(1000, ge=50, le=5000),
                    use_ai: bool = Query(False, alias="ai", description="Explanation from AI (cached in Redis)")):
    deals, cached = await _ranked(lat, lon, radius, None, budget, "best", 3, budget)
    if not deals:
        return {"advice": "В этом радиусе нет акций под ваш бюджет — попробуйте увеличить радиус.",
                "items": [], "cached": cached, "advice_source": "rules"}
    advice, source = ranking.advice(deals[0], budget), "rules"
    if use_ai:
        key = "ai:adv:" + hashlib.sha256(
            f"{ai.cache_tag()}|{budget}|{[(d['id'], d['title'], d['price'], d['upvotes']) for d in deals]}".encode()).hexdigest()
        text = await cache.raw_get(key)
        if text is None:
            try:
                text = await ai.explain(deals, budget)
                await cache.raw_set(key, text, ttl=600)
            except ai.AIError:
                text = None                       # AI failed -> silently fall back to the rules
            except Exception:
                log.exception("Сбой при получении совета от ИИ")
                text = None                       # whatever happens, we still return the rule-based advice
        if text:
            advice, source = text, "ai"
    return {"advice": advice, "items": deals, "cached": cached, "advice_source": source}


@app.get("/deals/{deal_id}", tags=["deals"])
async def deal_detail(deal_id: int):
    d = await db.get_deal(deal_id)
    if not d:
        raise HTTPException(404, "Deal not found")
    return d


@app.get("/venues/nearby", tags=["venues"])
async def venues_nearby(lat: float = Lat, lon: float = Lon,
                        radius: int = Query(500, ge=50, le=5000)):
    items = await db.venues_nearby(lat, lon, radius)
    for v in items:
        v["distance_m"] = round(v["distance_m"])
    return {"count": len(items), "items": items}


class VenueIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: Category
    address: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DealIn(BaseModel):
    venue_id: int
    title: str = Field(min_length=3, max_length=160)
    price: int = Field(ge=0, le=100000, description="₸")
    old_price: int | None = Field(None, ge=0, le=100000)
    description: str | None = Field(None, max_length=500)
    ends_at: datetime | None = None


@app.post("/venues", status_code=201, tags=["venues"], summary="Venue registers (cold start)")
async def add_venue(v: VenueIn):
    created = await db.create_venue(v.name, v.category, v.address, v.lat, v.lon)
    await cache.invalidate()
    return created


@app.post("/deals", status_code=201, tags=["deals"], summary="Venue adds a deal")
async def add_deal(body: DealIn, request: Request):
    if body.old_price is not None and body.old_price < body.price:
        raise HTTPException(422, "old_price must be >= price")
    ends_at = body.ends_at
    if ends_at is not None:
        if ends_at.tzinfo is None:                  # a time without a timezone is treated as UTC
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        if ends_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "ends_at must be in the future")
    ip = client_id(request)
    if not await cache.rate_limit(f"rl:deal:{ip}", limit=10, window=60):
        raise HTTPException(429, "Too many requests, try again in a minute")
    d = await db.create_deal(body.venue_id, body.title, body.price, body.old_price,
                             body.description, ends_at)
    if d is None:
        raise HTTPException(404, "Venue not found")
    await cache.invalidate()
    return d


@app.post("/deals/{deal_id}/upvote", tags=["deals"], summary="Confirm the price (once per day per client)")
async def upvote(deal_id: int, request: Request):
    ip = client_id(request)
    if not await cache.first_time(f"uv:{deal_id}:{ip}"):
        raise HTTPException(409, "Already upvoted")
    n = await db.upvote(deal_id)
    if n is None:
        raise HTTPException(404, "Deal not found")
    await cache.invalidate()
    return {"deal_id": deal_id, "upvotes": n}


MAX_IMG = 5 * 1024 * 1024
IMG_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _sniff(b: bytes) -> str | None:
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.get("/ai/status", tags=["ai"])
async def ai_status():
    return await ai.status()


@app.post("/menu/extract", tags=["ai"], summary="Menu photo -> list of items (saves nothing)")
async def menu_extract(request: Request, file: UploadFile = File(...)):
    ip = client_id(request)
    if not await cache.rate_limit(f"rl:menu:{ip}", limit=5, window=60):
        raise HTTPException(429, "Слишком часто, подождите минуту")
    data = await file.read(MAX_IMG + 1)
    if len(data) > MAX_IMG:
        raise HTTPException(413, "Файл больше 5 МБ")
    mime = _sniff(data)
    if mime not in IMG_TYPES:                    # trust the content, not the file extension
        raise HTTPException(415, "Нужно фото JPEG, PNG или WebP")
    key = f"ai:menu:{ai.cache_tag()}:{hashlib.sha256(data).hexdigest()}"
    hit = await cache.raw_get(key)
    if hit:
        return {**hit, "cached": True}
    try:
        res = await ai.extract_menu(data, mime)
    except ai.AIError as e:
        raise HTTPException(e.status, str(e))
    await cache.raw_set(key, res, ttl=86400)
    return {**res, "cached": False}


class MenuItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: int = Field(ge=0, le=100000)
    category: str = Field("other", max_length=20)


class MenuIn(BaseModel):
    items: list[MenuItemIn] = Field(min_length=1, max_length=200)
    replace: bool = False


@app.post("/venues/{venue_id}/menu", status_code=201, tags=["venues"], summary="Save the venue's menu")
async def save_menu(venue_id: int, body: MenuIn):
    cats = ai.MENU_CATEGORIES
    rows = [(i.name.strip(), i.price, i.category if i.category in cats else "other") for i in body.items]
    n = await db.save_menu(venue_id, rows, body.replace)
    if n is None:
        raise HTTPException(404, "Venue not found")
    return {"venue_id": venue_id, "saved": n}


@app.get("/venues/{venue_id}/menu", tags=["venues"])
async def get_menu(venue_id: int):
    items = await db.get_menu(venue_id)
    return {"count": len(items), "items": items}


class ReviewIn(BaseModel):
    author: str = Field(min_length=1, max_length=30)
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=3, max_length=500)


@app.get("/venues/{venue_id}/reviews", tags=["reviews"])
async def venue_reviews(venue_id: int):
    return await db.get_reviews(venue_id)


@app.post("/venues/{venue_id}/reviews", status_code=201, tags=["reviews"], summary="Leave a review")
async def add_review(venue_id: int, body: ReviewIn, request: Request, response: Response):
    if not await cache.rate_limit(f"rl:review:{client_id(request)}", limit=5, window=60):
        raise HTTPException(429, "Слишком часто, подождите минуту")
    key = author_key(request)
    if not await cache.rate_limit(f"rl:review:day:{key}", limit=20, window=86400):
        raise HTTPException(429, "Слишком много отзывов за сутки")
    if has_profanity(body.body) or has_profanity(body.author):
        raise HTTPException(400, "Отзыв содержит недопустимые слова")
    r = await db.create_review(venue_id, body.author.strip(), body.rating, body.body.strip(), key)
    if r is None:
        raise HTTPException(404, "Venue not found")
    await cache.invalidate()                      # the venue's rating changed: invalidate the results cache
    response.status_code = 200 if r["updated"] else 201
    return r


class BizRequestIn(BaseModel):
    venue_name: str = Field(min_length=2, max_length=120)
    address: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)
    contact_name: str | None = Field(None, max_length=60)
    comment: str | None = Field(None, max_length=500)


@app.post("/business-requests", status_code=201, tags=["business"],
          summary="Venue application: the 'business' role is granted by a moderator after review")
async def business_request(body: BizRequestIn, request: Request):
    if not await cache.rate_limit(f"rl:biz:{client_id(request)}", limit=3, window=3600):
        raise HTTPException(429, "Слишком много заявок, попробуйте позже")
    rid = await db.create_business_request(body.venue_name.strip(), body.address, body.phone,
                                           body.contact_name, body.comment)
    return {"id": rid, "status": "pending"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
