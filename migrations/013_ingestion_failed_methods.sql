ALTER TABLE ingestion_log
  ADD COLUMN IF NOT EXISTS failed_methods TEXT[] NOT NULL DEFAULT '{}';
