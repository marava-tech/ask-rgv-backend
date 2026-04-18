-- v2 feature — table created now, populated later
CREATE TABLE user_memory (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT NOT NULL CHECK (type IN ('belief', 'fear', 'desire')),
    phrase     TEXT NOT NULL,
    embedding  FLOAT[] NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 0.5,
    frequency  INT NOT NULL DEFAULT 1,
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_memory_user_id ON user_memory(user_id);
