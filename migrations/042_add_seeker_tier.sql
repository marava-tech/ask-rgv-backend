-- Migration 042: Add 'seeker' to user tier check constraint
-- The backend code (queries.py) has transitioned to using 'seeker' as the base tier,
-- but the DB constraint was still restricted to ('free', 'fan', 'super_fan').

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_tier_check;

ALTER TABLE users ADD CONSTRAINT users_tier_check 
    CHECK (tier IN ('free', 'seeker', 'fan', 'super_fan'));

-- Also update existing 'free' users to 'seeker' to maintain consistency
UPDATE users SET tier = 'seeker' WHERE tier = 'free';
