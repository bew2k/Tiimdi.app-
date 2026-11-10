"""PostgreSQL/PostGIS access: pool, schema, CSV seeding, geo queries."""
import asyncio
import csv
import logging
import os

import asyncpg

from .config import DATABASE_URL, DATA_DIR, SHOW_DEMO_DATA

log = logging.getLogger("tiimdi.db")
pool: asyncpg.Pool | None = None


async def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Подключается к БД с повторами: при первом запуске Postgres ещё инициализируется
    и сначала отвечает «connection refused» или «starting up»."""
    global pool
    for attempt in range(1, retries + 1):
        try:
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            break
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as e:
            if attempt == retries:
                raise
            log.warning("БД ещё не готова (%s), попытка %d/%d", e.__class__.__name__, attempt, retries)
            await asyncio.sleep(delay)
    schema = open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8").read()
    async with pool.acquire() as con:
        await con.execute(schema)
        await seed_if_empty(con)
        await hide_bad_reviews(con)


async def hide_bad_reviews(con: asyncpg.Connection) -> None:
    """Отзывы, оставленные до появления фильтра, проверяются при старте: плохие скрываются."""
    from .profanity import has_profanity
    rows = await con.fetch("SELECT id, author, body FROM reviews WHERE status='visible'")
    bad = [r["id"] for r in rows if has_profanity(r["body"]) or has_profanity(r["author"])]
    if bad:
        await con.execute("UPDATE reviews SET status='hidden' WHERE id = ANY($1::int[])", bad)
        await recompute_rating(con, None)


async def close_db() -> None:
    if pool:
        await pool.close()


async def seed_if_empty(con: asyncpg.Connection) -> None:
    """Мок-данные из CSV. CSV считается источником правды для заведений с id из файла (можно править и
    перезапускать); заведения, созданные через API, получают id больше и не трогаются."""
    with open(os.path.join(DATA_DIR, "venues.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    demo_path = os.path.join(DATA_DIR, "demo", "venues.csv")
    demo_rows = []
    if os.path.exists(demo_path):
        with open(demo_path, encoding="utf-8") as f:
            demo_rows = list(csv.DictReader(f))
    if SHOW_DEMO_DATA:
        rows = demo_rows + rows
    else:   # вымышленные заведения убираем и из уже существующей базы
        await con.execute("DELETE FROM venues WHERE id = ANY($1::int[])", [int(r["id"]) for r in demo_rows])
    new_ids = set()
    for r in rows:
        ins = await con.fetchval(
            """INSERT INTO venues (id, name, category, address, rating, geom, hours, phone, photo_url, menu_photo_url)
               VALUES ($1,$2,$3,$4,$5, ST_SetSRID(ST_MakePoint($6,$7),4326)::geography, $8, $9, $10, $11)
               ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, address=EXCLUDED.address,
                 rating=EXCLUDED.rating, geom=EXCLUDED.geom, hours=EXCLUDED.hours, phone=EXCLUDED.phone,
                 photo_url=COALESCE(EXCLUDED.photo_url, venues.photo_url),
                 menu_photo_url=COALESCE(EXCLUDED.menu_photo_url, venues.menu_photo_url)
               RETURNING (xmax = 0)""",
            int(r["id"]), r["name"], r["category"], r["address"], float(r["rating"]) if r.get("rating") else None,
            float(r["lon"]), float(r["lat"]), r.get("hours") or None, r.get("phone") or None, r.get("photo_url") or None, r.get("menu_photo") or None)
        if ins:
            new_ids.add(int(r["id"]))
    await con.execute("SELECT setval('venues_id_seq', (SELECT max(id) FROM venues))")
    # исходный рейтинг берём из CSV, затем накладываем отзывы
    await con.execute("UPDATE venues SET base_rating = rating WHERE id = ANY($1::int[])", [int(r["id"]) for r in rows])
    await con.execute("UPDATE venues SET base_rating = rating WHERE base_rating IS NULL")
    await recompute_rating(con, None)
    deal_files = [os.path.join(DATA_DIR, "deals.csv")]
    if SHOW_DEMO_DATA:
        deal_files.append(os.path.join(DATA_DIR, "demo", "deals.csv"))
    for path in deal_files:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if await con.fetchval("SELECT 1 FROM deals WHERE venue_id=$1 AND title=$2",
                                      int(r["venue_id"]), r["title"]):
                    continue
                if int(r["venue_id"]) not in new_ids and int(r["venue_id"]) < 18:
                    continue
                await con.execute(
                    """INSERT INTO deals (venue_id, title, price, old_price, description, upvotes)
                       VALUES ($1,$2,$3,$4,$5,$6)""",
                    int(r["venue_id"]), r["title"], int(r["price"]),
                    int(r["old_price"]) if r["old_price"] else None, r["description"], int(r["upvotes"]))
    menu_path = os.path.join(DATA_DIR, "menu.csv")
    if os.path.exists(menu_path):
        # menu.csv - источник правды для заведений, которые в нём перечислены: их меню обновляется при каждом старте
        with open(menu_path, encoding="utf-8") as f:
            menu_rows = list(csv.DictReader(f))
        ids = sorted({int(r["venue_id"]) for r in menu_rows})
        await con.execute("DELETE FROM menu_items WHERE venue_id = ANY($1::int[])", ids)
        for r in menu_rows:
            await con.execute("INSERT INTO menu_items (venue_id, name, price, category) VALUES ($1,$2,$3,$4)",
                              int(r["venue_id"]), r["name"], int(r["price"]), r["category"] or "other")


DEAL_SELECT = """
SELECT d.id, d.title, d.price, d.old_price, d.description, d.upvotes, d.ends_at,
       v.id AS venue_id, v.name AS venue_name, v.category, v.address, v.rating,
       ST_Y(v.geom::geometry) AS lat, ST_X(v.geom::geometry) AS lon,
       d.status, v.photo_url AS venue_photo, v.menu_photo_url AS venue_menu_photo, v.hours AS venue_hours, v.phone AS venue_phone, v.verified AS venue_verified
       {extra}
FROM deals d JOIN venues v ON v.id = d.venue_id
"""
ACTIVE = "d.status='active' AND d.starts_at <= now() AND (d.ends_at IS NULL OR d.ends_at > now())"


async def deals_nearby(lat: float, lon: float, radius: int, category: str | None,
                       max_price: int | None, limit: int = 200) -> list[dict]:
    """ST_DWithin uses the GiST index; ST_Distance gives metres on the sphere (geography)."""
    sql = DEAL_SELECT.format(
        extra=", ST_Distance(v.geom, ST_SetSRID(ST_MakePoint($2,$1),4326)::geography) AS distance_m"
    ) + f"""WHERE {ACTIVE}
        AND ST_DWithin(v.geom, ST_SetSRID(ST_MakePoint($2,$1),4326)::geography, $3)
        AND ($4::text IS NULL OR v.category = $4)
        AND ($5::int IS NULL OR d.price <= $5)
        ORDER BY distance_m LIMIT $6"""
    async with pool.acquire() as con:
        rows = await con.fetch(sql, lat, lon, float(radius), category, max_price, limit)
    return [dict(r) for r in rows]


async def get_deal(deal_id: int) -> dict | None:
    async with pool.acquire() as con:
        r = await con.fetchrow(DEAL_SELECT.format(extra="") + "WHERE d.id=$1", deal_id)
    return dict(r) if r else None


async def venues_nearby(lat: float, lon: float, radius: int) -> list[dict]:
    async with pool.acquire() as con:
        rows = await con.fetch(
            """SELECT v.id, v.name, v.category, v.address, v.rating, v.photo_url, v.menu_photo_url, v.hours, v.phone, v.verified,
                      ST_Y(v.geom::geometry) AS lat, ST_X(v.geom::geometry) AS lon,
                      ST_Distance(v.geom, ST_SetSRID(ST_MakePoint($2,$1),4326)::geography) AS distance_m,
                      (SELECT count(*) FROM deals d WHERE d.venue_id=v.id AND """ + ACTIVE + """) AS active_deals
               FROM venues v
               WHERE ST_DWithin(v.geom, ST_SetSRID(ST_MakePoint($2,$1),4326)::geography, $3)
               ORDER BY distance_m""",
            lat, lon, float(radius))
    return [dict(r) for r in rows]


async def create_venue(name: str, category: str, address: str | None, lat: float, lon: float) -> dict:
    async with pool.acquire() as con:
        r = await con.fetchrow(
            """INSERT INTO venues (name, category, address, geom)
               VALUES ($1,$2,$3, ST_SetSRID(ST_MakePoint($5,$4),4326)::geography)
               RETURNING id, name, category, address, rating""",
            name, category, address, lat, lon)
    return {**dict(r), "lat": lat, "lon": lon}


async def create_deal(venue_id: int, title: str, price: int, old_price: int | None,
                      description: str | None, ends_at) -> dict | None:
    async with pool.acquire() as con:
        if not await con.fetchval("SELECT 1 FROM venues WHERE id=$1", venue_id):
            return None
        new_id = await con.fetchval(
            """INSERT INTO deals (venue_id, title, price, old_price, description, ends_at)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            venue_id, title, price, old_price, description, ends_at)
    return await get_deal(new_id)


async def upvote(deal_id: int) -> int | None:
    async with pool.acquire() as con:
        return await con.fetchval(
            "UPDATE deals SET upvotes = upvotes + 1 WHERE id=$1 RETURNING upvotes", deal_id)


async def save_menu(venue_id: int, items: list[tuple], replace: bool) -> int | None:
    """items: [(name, price, category), ...]. None если заведения нет."""
    async with pool.acquire() as con:
        if not await con.fetchval("SELECT 1 FROM venues WHERE id=$1", venue_id):
            return None
        async with con.transaction():
            if replace:
                await con.execute("DELETE FROM menu_items WHERE venue_id=$1", venue_id)
            await con.executemany(
                "INSERT INTO menu_items (venue_id, name, price, category) VALUES ($1,$2,$3,$4)",
                [(venue_id, *it) for it in items])
    return len(items)


async def get_menu(venue_id: int) -> list[dict]:
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id, name, price, category FROM menu_items WHERE venue_id=$1 ORDER BY category, name", venue_id)
    return [dict(r) for r in rows]


RATING_PRIOR = 5   # «вес» исходного рейтинга: столько отзывов нужно, чтобы отзывы пересилили его наполовину


async def recompute_rating(con: asyncpg.Connection, venue_id: int | None) -> None:
    """Итоговый рейтинг = (5·исходный + сумма оценок) / (5 + число оценок). Один отзыв сдвигает рейтинг слабо."""
    await con.execute(
        """UPDATE venues v SET rating = ROUND(
               ($2::numeric * COALESCE(v.base_rating, v.rating)
                + COALESCE((SELECT sum(r.rating) FROM reviews r WHERE r.venue_id = v.id AND r.status = 'visible'), 0))::numeric
               / ($2::numeric + (SELECT count(*) FROM reviews r WHERE r.venue_id = v.id AND r.status = 'visible')), 1)
           WHERE ($1::int IS NULL OR v.id = $1)""", venue_id, RATING_PRIOR)


async def get_reviews(venue_id: int, limit: int = 50) -> dict:
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id, author, rating, body, created_at FROM reviews WHERE venue_id=$1 AND status='visible' "
            "ORDER BY created_at DESC LIMIT $2", venue_id, limit)
        agg = await con.fetchrow(
            "SELECT count(*) AS n, avg(rating) AS avg FROM reviews WHERE venue_id=$1 AND status='visible'", venue_id)
        shown = await con.fetchval("SELECT rating FROM venues WHERE id=$1", venue_id)
    return {"count": agg["n"], "avg": round(float(agg["avg"]), 1) if agg["avg"] is not None else None,
            "rating": float(shown) if shown is not None else None,
            "items": [dict(r) for r in rows]}


async def create_review(venue_id: int, author: str, rating: int, body: str, author_key: str) -> dict | None:
    """Один отзыв на заведение с одного устройства: повторная отправка обновляет прежний. Поле updated = True."""
    async with pool.acquire() as con:
        if not await con.fetchval("SELECT 1 FROM venues WHERE id=$1", venue_id):
            return None
        r = await con.fetchrow(
            """INSERT INTO reviews (venue_id, author, rating, body, author_key) VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (venue_id, author_key) DO UPDATE
                 SET author=EXCLUDED.author, rating=EXCLUDED.rating, body=EXCLUDED.body, created_at=now()
               RETURNING id, author, rating, body, created_at, (xmax <> 0) AS updated""",
            venue_id, author, rating, body, author_key)
        await recompute_rating(con, venue_id)
    return dict(r)


async def create_business_request(venue_name: str, address, phone, contact_name, comment) -> int:
    async with pool.acquire() as con:
        return await con.fetchval(
            "INSERT INTO business_requests (venue_name, address, phone, contact_name, comment) "
            "VALUES ($1,$2,$3,$4,$5) RETURNING id", venue_name, address, phone, contact_name, comment)
