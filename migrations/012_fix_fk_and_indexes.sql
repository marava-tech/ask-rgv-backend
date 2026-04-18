-- Bug #31: token_hash had only a regular index — UNIQUE constraint prevents duplicate hashes
-- and speeds up the DELETE-based consume_refresh_token lookup
DROP INDEX IF EXISTS idx_refresh_tokens_hash;
CREATE UNIQUE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash);

-- Bug #32: sessions.user_id FK had no ON DELETE clause — deleting a user caused FK violation;
-- SET NULL preserves session history in anonymized form (other FKs use CASCADE)
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_user_id_fkey;
ALTER TABLE sessions
    ADD CONSTRAINT sessions_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;

-- Bug #33: crisis_events.session_id FK had no ON DELETE clause — blocked session deletion
ALTER TABLE crisis_events DROP CONSTRAINT IF EXISTS crisis_events_session_id_fkey;
ALTER TABLE crisis_events
    ADD CONSTRAINT crisis_events_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL;

-- Also add updated_at column to subscriptions if not present (Bug #15B)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
