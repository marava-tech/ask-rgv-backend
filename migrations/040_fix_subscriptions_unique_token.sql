-- Migration 040: Make google_purchase_token unique on subscriptions table
-- This is required for the INSERT ... ON CONFLICT (google_purchase_token) DO UPDATE logic in queries.py

DROP INDEX IF EXISTS idx_subscriptions_purchase_token;

CREATE UNIQUE INDEX idx_subscriptions_purchase_token
    ON subscriptions (google_purchase_token)
    WHERE google_purchase_token IS NOT NULL;
