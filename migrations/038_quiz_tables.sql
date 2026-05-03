-- Quiz questions bank
CREATE TABLE IF NOT EXISTS quiz_questions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_text TEXT NOT NULL,
  options       JSONB NOT NULL,
  display_order INTEGER NOT NULL UNIQUE,
  enabled       BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User quiz responses (one row per answer per submission session)
CREATE TABLE IF NOT EXISTS user_quiz_responses (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  assessment_session_id UUID NOT NULL,
  question_id           UUID NOT NULL REFERENCES quiz_questions(id),
  selected_option_index INTEGER NOT NULL,
  other_text            TEXT,
  answered_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quiz_responses_user ON user_quiz_responses(user_id);
CREATE INDEX IF NOT EXISTS idx_quiz_responses_session ON user_quiz_responses(assessment_session_id);

-- RGV's assessments of users
CREATE TABLE IF NOT EXISTS user_rgv_assessments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_answers     JSONB NOT NULL,
  assessment_text TEXT NOT NULL DEFAULT '',
  assessed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMPTZ NOT NULL,
  version         INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'processing'
);

CREATE INDEX IF NOT EXISTS idx_assessments_user_status ON user_rgv_assessments(user_id, status);

-- Seed 11 worldview questions
INSERT INTO quiz_questions (question_text, options, display_order) VALUES
(
  'Is your life mostly shaped by your choices — or by circumstances beyond your control?',
  '["Mostly my choices — I own my outcomes", "Mostly circumstances — luck and environment dominate", "A rough 50/50 split", "Other (describe):"]'::jsonb,
  1
),
(
  'Would you rather know a painful truth or live comfortably in a pleasant lie?',
  '["Always the truth — I can handle it", "The lie if it genuinely makes me happier", "Depends on whether knowing changes anything", "Other (describe):"]'::jsonb,
  2
),
(
  'What scares you more — death, or a meaningless life?',
  '["Death — non-existence terrifies me", "A meaningless life — I need purpose", "Neither genuinely scares me", "Other (describe):"]'::jsonb,
  3
),
(
  'If everyone around you believes something you''re not sure about — what do you do?',
  '["I go along — social harmony matters", "I quietly hold my doubt without voicing it", "I openly challenge it regardless of the crowd", "Other (describe):"]'::jsonb,
  4
),
(
  'When a film or story moves you emotionally — what do you think is actually happening?',
  '["Craft is triggering biological responses — it''s mechanics", "Truth is resonating with something real in me", "I''m escaping into something I wish were true", "Other (describe):"]'::jsonb,
  5
),
(
  'Do you think most people are fundamentally honest, or fundamentally self-serving?',
  '["Fundamentally honest — self-interest is the exception", "Fundamentally self-serving — honesty is costly", "Neither — people are mostly unconscious, not calculating", "Other (describe):"]'::jsonb,
  6
),
(
  'How do you privately define success — not what you''d say in public?',
  '["Freedom to do exactly what I want without constraint", "Recognition that I''ve done something that matters", "Financial security that removes all fear", "Other (describe):"]'::jsonb,
  7
),
(
  'Is morality something universal and fixed — or does context change everything?',
  '["Universal — some things are wrong regardless of context", "Entirely contextual — morality is a social construct", "There''s a core that''s fixed but a lot that''s negotiable", "Other (describe):"]'::jsonb,
  8
),
(
  'When you fall in love — is that mostly chemistry happening to you, or a choice you make?',
  '["Chemistry — it happens, I don''t control it", "Ultimately a choice — I decide to stay and commit", "The initial spark is chemistry; love is built by choice", "Other (describe):"]'::jsonb,
  9
),
(
  'Do you believe in god — and if yes, why specifically?',
  '["Yes — the universe couldn''t have emerged from nothing", "No — I see no evidence that justifies the belief", "I don''t know and I''m comfortable not knowing", "Other (describe):"]'::jsonb,
  10
),
(
  'If you had complete power with zero accountability for one year — what would you actually do?',
  '["Fix specific systems I see as broken", "Explore every experience currently unavailable to me", "Honestly — nothing much different from now", "Other (describe):"]'::jsonb,
  11
)
ON CONFLICT (display_order) DO NOTHING;
