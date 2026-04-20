CREATE TABLE IF NOT EXISTS app_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT        NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO app_config (key, value, description) VALUES
  ('free_daily_credits',       '5',   'Credits per day for free tier'),
  ('fan_daily_credits',        '60',  'Credits per day for fan tier'),
  ('super_fan_daily_credits',  '-1',  'Credits per day for super_fan tier (-1 = unlimited)'),
  ('anonymous_daily_credits',  '5',   'Credits per day for anonymous users'),
  ('credits_per_minute',       '1',   'Credits consumed per minute of audio usage'),
  ('credits_per_turn_min',     '3',   'Minimum credits charged per conversation turn'),
  ('credits_per_turn_max',     '120', 'Maximum credits charged per conversation turn')
ON CONFLICT (key) DO NOTHING;
