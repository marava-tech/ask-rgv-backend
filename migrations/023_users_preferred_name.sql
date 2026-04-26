ALTER TABLE users ADD COLUMN preferred_name TEXT NULL CHECK (char_length(preferred_name) <= 50);
