CREATE TABLE sessions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID REFERENCES users(id),
    device_id              TEXT,
    turn_count             INT NOT NULL DEFAULT 0,
    session_title          TEXT,
    total_duration_seconds INT NOT NULL DEFAULT 0,
    language               TEXT CHECK (language IN ('en', 'te', 'hi')),
    started_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at               TIMESTAMPTZ
);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_user_id_started_at ON sessions(user_id, started_at DESC);
