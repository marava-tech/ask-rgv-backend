-- Migration 041: add seconds_remaining to quota_packs for time-based one-time packs
-- Existing rows (turn-based legacy) keep turns_remaining; new purchases use seconds_remaining.

ALTER TABLE quota_packs
    ADD COLUMN IF NOT EXISTS seconds_remaining INT NOT NULL DEFAULT 0;
