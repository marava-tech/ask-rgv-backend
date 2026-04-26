ALTER TABLE users ADD COLUMN preferred_language TEXT NULL
  CHECK (preferred_language IS NULL OR preferred_language IN ('te','hi','en'));
