CREATE TABLE rgv_style_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    language        TEXT NOT NULL CHECK (language IN ('en', 'te', 'hi')),
    quote           TEXT NOT NULL,
    style_tags      TEXT[],
    source_video_id TEXT,
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_style_profiles_lang ON rgv_style_profiles(language) WHERE active = true;
