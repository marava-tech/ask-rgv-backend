-- Migration 026: Replace Razorpay columns with Google Play IAP columns on subscriptions table
-- Adds quota_packs table and user_memory table

-- Drop Razorpay-specific columns
ALTER TABLE subscriptions
    DROP COLUMN IF EXISTS razorpay_order_id,
    DROP COLUMN IF EXISTS razorpay_payment_id,
    DROP COLUMN IF EXISTS razorpay_signature,
    DROP COLUMN IF EXISTS expected_amount_paise;

-- Add Google Play IAP columns
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS google_purchase_token TEXT,
    ADD COLUMN IF NOT EXISTS subscription_period TEXT CHECK (subscription_period IN ('monthly', 'annual')),
    ADD COLUMN IF NOT EXISTS product_id TEXT;

CREATE INDEX IF NOT EXISTS idx_subscriptions_purchase_token
    ON subscriptions (google_purchase_token)
    WHERE google_purchase_token IS NOT NULL;
