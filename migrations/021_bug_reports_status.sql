ALTER TABLE bug_reports
  ADD COLUMN status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','in_progress','resolved','wont_fix')),
  ADD COLUMN admin_notes TEXT,
  ADD COLUMN resolved_at TIMESTAMPTZ,
  ADD COLUMN resolved_by UUID;

CREATE INDEX idx_bug_reports_status ON bug_reports(status);
