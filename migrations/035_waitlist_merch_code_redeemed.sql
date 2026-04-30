ALTER TABLE waitlist_signups
  ADD COLUMN IF NOT EXISTS merch_code_redeemed_at TIMESTAMPTZ;
