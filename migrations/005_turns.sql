CREATE TABLE turns (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    mode                TEXT NOT NULL DEFAULT 'default' CHECK (mode IN ('default', 'hard_truth')),
    user_input          TEXT NOT NULL,
    response            TEXT NOT NULL,
    audio_played_seconds INT NOT NULL DEFAULT 0,
    tokens_used         INT,
    latency_ms          INT,
    rag_chunks_used     INT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_turns_session_id ON turns(session_id);
CREATE INDEX idx_turns_session_id_created_at_asc  ON turns(session_id, created_at ASC);
CREATE INDEX idx_turns_session_id_created_at_desc ON turns(session_id, created_at DESC);
