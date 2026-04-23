-- X-01: rename *_daily_credits keys to *_weekly_credits to match the actual quota window.
-- The quota resets every Monday (ISO week), not daily. Descriptions are updated accordingly.

UPDATE app_config
SET key = 'anonymous_weekly_credits',
    description = 'Credits per week for anonymous users'
WHERE key = 'anonymous_daily_credits';

UPDATE app_config
SET key = 'free_weekly_credits',
    description = 'Credits per week for free tier'
WHERE key = 'free_daily_credits';

UPDATE app_config
SET key = 'fan_weekly_credits',
    description = 'Credits per week for fan tier'
WHERE key = 'fan_daily_credits';

UPDATE app_config
SET key = 'super_fan_weekly_credits',
    description = 'Credits per week for super_fan tier (-1 = unlimited)'
WHERE key = 'super_fan_daily_credits';
