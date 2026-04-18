CREATE TABLE crisis_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID REFERENCES sessions(id),
    trigger_phrase TEXT NOT NULL,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT now()
);
