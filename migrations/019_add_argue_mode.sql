-- Add 'argue' to the turns.mode check constraint
ALTER TABLE turns DROP CONSTRAINT turns_mode_check;
ALTER TABLE turns ADD CONSTRAINT turns_mode_check
    CHECK (mode IN ('default', 'hard_truth', 'argue'));
