CREATE TABLE users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_id    TEXT UNIQUE NOT NULL,
    email        TEXT NOT NULL,
    display_name TEXT,
    avatar_url   TEXT,
    tier         TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'fan', 'super_fan')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_google_id ON users(google_id);
