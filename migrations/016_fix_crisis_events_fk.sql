-- Fix crisis_events.session_id FK so session deletes don't error out
ALTER TABLE crisis_events
    DROP CONSTRAINT IF EXISTS crisis_events_session_id_fkey;
ALTER TABLE crisis_events
    ADD CONSTRAINT crisis_events_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL;
