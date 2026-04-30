-- Migration 028: user_memory table for Super Fan persistent cross-session memory

CREATE TABLE IF NOT EXISTS user_memory (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    summary     TEXT NOT NULL DEFAULT '',
    key_facts   JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
