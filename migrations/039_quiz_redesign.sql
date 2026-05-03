-- Add multi-select flag to quiz_questions
ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS allows_multiple_select BOOLEAN NOT NULL DEFAULT false;

-- Wipe responses and assessments (pre-launch: no real user data)
TRUNCATE TABLE user_quiz_responses;
TRUNCATE TABLE user_rgv_assessments;

-- Migrate user_quiz_responses schema: drop old columns, add new array column
ALTER TABLE user_quiz_responses DROP COLUMN IF EXISTS selected_option_index;
ALTER TABLE user_quiz_responses DROP COLUMN IF EXISTS other_text;
ALTER TABLE user_quiz_responses ADD COLUMN IF NOT EXISTS selected_option_indices INTEGER[] NOT NULL DEFAULT '{}';

-- Re-seed questions (wipe first, then insert v2 set)
TRUNCATE TABLE quiz_questions CASCADE;

INSERT INTO quiz_questions (question_text, options, allows_multiple_select, display_order) VALUES
(
  'Where do you think sadness comes from? (Pick all that feel true to you)',
  '["When things don''t go the way you expected","When you remember how good things used to be","Sadness is useful sometimes — it makes you slow down and think","It''s just part of life. You don''t need to understand it, just get through it"]'::jsonb,
  true,
  1
),
(
  'Every great moment in your life eventually becomes just a memory. So is it really worth chasing those moments?',
  '["Yes — even if the moment is gone, the memory itself is worth it","The memory isn''t the point — what you actually learned from it is","Not really — you''ll just end up missing it more than if it never happened","Great moments only make sense later when you look back. The moment itself is overrated."]'::jsonb,
  false,
  2
),
(
  'There are 8 billion people on this planet. What makes you different from most of them? (Pick what honestly feels true)',
  '["Nothing, really — I''m just another person trying to figure things out","The way I think about things","The things I''ve been through","Honestly, I don''t know yet"]'::jsonb,
  true,
  3
),
(
  'What do you think people are actually most afraid of — deep down?',
  '["Dying","Being completely forgotten after they''re gone","Finding out they wasted their life","Never being truly understood by anyone"]'::jsonb,
  false,
  4
),
(
  'A close friend asks: ''Am I good at this?'' — and you know they''re not. What do you do?',
  '["Tell them the truth. It''s better for them in the long run.","Say something in the middle — not a lie, but not the full truth either","Ask them why they''re asking before you say anything","Tell them what they need to hear right now, even if it''s not fully true"]'::jsonb,
  false,
  5
),
(
  'When someone becomes very successful — what do you actually think happened?',
  '["They put in work that most people weren''t willing to put in","They got lucky at exactly the right moment","Both — but luck decides which hardworking people actually make it","They had the right people around them at the right time"]'::jsonb,
  false,
  6
),
(
  'If you found out you only had one year left to live — what would you actually do? (Pick honestly, not what sounds right)',
  '["Spend every moment with the people I love","Finally do the things I kept putting off","Try to leave something behind that lasts","Honestly — panic for a while before doing anything useful"]'::jsonb,
  true,
  7
),
(
  'Do you think life is fair?',
  '["No — and that''s just how it is","No — but that''s not a reason to stop trying","Sometimes yes, sometimes no — it depends on too many things","Whether it''s fair or not doesn''t really matter to me"]'::jsonb,
  false,
  8
),
(
  'In general — do you think most people are basically good, or are they mostly looking out for themselves?',
  '["Mostly good — people have good intentions even when they fall short","Mostly self-interested — not evil, just looking out for themselves","It completely depends on what the situation asks of them","I''ve stopped trying to answer this. People surprise you both ways."]'::jsonb,
  false,
  9
),
(
  'Can people truly change who they are — or do they mostly just change how they act?',
  '["Yes, people can genuinely change — it just takes a lot of time and effort","People change how they behave, but who they are underneath stays the same","Small things change over time without you noticing, but the core stays","Some things can change completely. Other things never will. Depends on the person."]'::jsonb,
  false,
  10
),
(
  'Why do you think most people do anything at all — wake up, work, build things, love people?',
  '["Because they''re afraid of what it would feel like to stop","Because meaning is something you create, not something you find","Because connection to other people is the only thing that actually matters","Honestly, I don''t think most people know why. They just keep going."]'::jsonb,
  false,
  11
);
