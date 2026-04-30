-- Add token family tracking to refresh_tokens for replay-attack detection.
-- Family groups all rotation-linked tokens; reuse of a revoked token invalidates the entire family.
ALTER TABLE refresh_tokens
  ADD COLUMN token_family UUID NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN revoked_at   TIMESTAMPTZ;

CREATE INDEX idx_refresh_tokens_family ON refresh_tokens(token_family);
