CREATE TABLE IF NOT EXISTS monitor_runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_size      INT         NOT NULL DEFAULT 50,
    report_json      JSONB       NOT NULL DEFAULT '{}',
    improvement_notes TEXT,
    run_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_monitor_runs_run_at ON monitor_runs (run_at DESC);
