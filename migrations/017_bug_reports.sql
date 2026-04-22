CREATE TABLE bug_reports (
    id             SERIAL PRIMARY KEY,
    user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    description    TEXT NOT NULL,
    screenshot_url TEXT,
    device_info    JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_bug_reports_created_at ON bug_reports(created_at DESC);
