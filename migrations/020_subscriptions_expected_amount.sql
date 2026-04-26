-- Store the paise amount that was expected at order creation so the webhook
-- can verify the captured amount exactly instead of range-checking.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS expected_amount_paise INTEGER;
