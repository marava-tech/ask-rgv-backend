CREATE TABLE IF NOT EXISTS waitlist_signups (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    language        TEXT NOT NULL,
    is_rgv_fan      TEXT,
    source          TEXT,
    app_promo_code  TEXT NOT NULL UNIQUE,
    merch_promo_code TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS waitlist_merch_interest (
    id          BIGSERIAL PRIMARY KEY,
    waitlist_id BIGINT NOT NULL REFERENCES waitlist_signups(id) ON DELETE CASCADE,
    categories  TEXT[] NOT NULL
);

CREATE INDEX IF NOT EXISTS waitlist_signups_email_idx ON waitlist_signups(email);
CREATE INDEX IF NOT EXISTS waitlist_signups_created_at_idx ON waitlist_signups(created_at DESC);
