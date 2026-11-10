CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS venues (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,              -- cafe | fastfood | canteen | bakery | grocery | coffee
    address     TEXT,
    rating      NUMERIC(2,1) DEFAULT 4.0,
    geom        geography(Point, 4326) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE venues ADD COLUMN IF NOT EXISTS photo_url TEXT;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS menu_photo_url TEXT;   -- photo of the paper menu
ALTER TABLE venues ADD COLUMN IF NOT EXISTS hours TEXT;           -- '08:00-20:00'
ALTER TABLE venues ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false;   -- confirmed by a moderator
-- GiST (R-Tree) index makes ST_DWithin fast
CREATE INDEX IF NOT EXISTS venues_geom_gix ON venues USING GIST (geom);

CREATE TABLE IF NOT EXISTS deals (
    id          SERIAL PRIMARY KEY,
    venue_id    INT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    price       INT NOT NULL CHECK (price >= 0),      -- KZT
    old_price   INT CHECK (old_price IS NULL OR old_price >= price),
    description TEXT,
    upvotes     INT NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',        -- active | moderation | rejected
    starts_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deals_venue_idx ON deals(venue_id);

CREATE TABLE IF NOT EXISTS menu_items (
    id          SERIAL PRIMARY KEY,
    venue_id    INT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    price       INT NOT NULL CHECK (price >= 0),      -- KZT
    category    TEXT NOT NULL DEFAULT 'other',
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS menu_items_venue_idx ON menu_items(venue_id);

CREATE TABLE IF NOT EXISTS reviews (
    id          SERIAL PRIMARY KEY,
    venue_id    INT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    author      TEXT NOT NULL,
    rating      INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'visible',      -- visible | hidden (moderator)
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reviews_venue_idx ON reviews(venue_id);
-- one review per venue per device (resubmitting replaces the previous one)
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS author_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS reviews_one_per_author ON reviews(venue_id, author_key);
-- base venue rating: the final rating is smoothed by reviews, a single review barely changes it
ALTER TABLE venues ADD COLUMN IF NOT EXISTS base_rating NUMERIC(2,1);

-- venue applications: the "business" role is granted only by a moderator after review
CREATE TABLE IF NOT EXISTS business_requests (
    id            SERIAL PRIMARY KEY,
    venue_name    TEXT NOT NULL,
    address       TEXT,
    phone         TEXT,
    contact_name  TEXT,
    comment       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | rejected
    created_at    TIMESTAMPTZ DEFAULT now()
);
