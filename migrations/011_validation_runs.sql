CREATE TABLE validation_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    overall_score FLOAT NOT NULL,
    passed        BOOLEAN NOT NULL,
    report_json   JSONB NOT NULL,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
