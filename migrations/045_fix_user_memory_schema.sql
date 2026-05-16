-- Migration 045: Fix user_memory table schema mismatch
--
-- Migration 006 created user_memory with phrase-based columns (phrase, type, embedding, etc.).
-- Migration 028 tried to CREATE TABLE IF NOT EXISTS with the new summary-based schema,
-- but silently no-oped because the table already existed from migration 006.
-- This migration detects the old schema and replaces it with the correct one.

DO $$
BEGIN
    -- Only act if the 'summary' column is missing (i.e. the old schema is in place)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_memory'
          AND column_name = 'summary'
    ) THEN
        DROP TABLE IF EXISTS user_memory;

        CREATE TABLE user_memory (
            user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            summary     TEXT NOT NULL DEFAULT '',
            key_facts   JSONB NOT NULL DEFAULT '{}',
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;
END $$;
