CREATE TABLE ingestion_log (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type              TEXT NOT NULL DEFAULT 'youtube',
    source_title             TEXT,
    source_url               TEXT NOT NULL,
    video_id                 TEXT,
    source_language          TEXT NOT NULL CHECK (source_language IN ('en', 'te', 'hi')),
    transcript_method        TEXT,
    translated               BOOLEAN NOT NULL DEFAULT false,
    translation_quality_flag BOOLEAN NOT NULL DEFAULT false,
    chunk_count              INT NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'pending' CHECK (
                                 status IN ('pending','extracting_transcript','translating',
                                            'chunking','embedding','complete','failed')),
    current_step             TEXT,
    progress_pct             INT NOT NULL DEFAULT 0,
    enabled                  BOOLEAN NOT NULL DEFAULT true,
    error_message            TEXT,
    ingested_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_ingestion_log_video_id ON ingestion_log(video_id) WHERE video_id IS NOT NULL;
CREATE INDEX idx_ingestion_log_status ON ingestion_log(status);
CREATE INDEX idx_ingestion_log_lang ON ingestion_log(source_language);
