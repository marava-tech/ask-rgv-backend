-- Migration 027: quota_packs table for one-time conversation pack purchases

CREATE TABLE IF NOT EXISTS quota_packs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      TEXT NOT NULL,
    purchase_token  TEXT NOT NULL UNIQUE,
    turns_remaining INT  NOT NULL DEFAULT 0,
    purchased_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quota_packs_user_id ON quota_packs (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quota_packs_token ON quota_packs (purchase_token);
