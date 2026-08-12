-- Pending approvals need a deadline and a queue.
--
-- expires_at was dropped in 003 as unused; it comes back because a confirmation
-- nobody answers holds its session idle forever, and "the agent silently stopped"
-- is the failure mode an approval gate is supposed to prevent.
ALTER TABLE tool_confirmations ADD COLUMN expires_at timestamptz;

-- The approval inbox reads across every session, so it cannot ride the
-- (session_id, call_hash) unique index.
CREATE INDEX tool_confirmations_pending ON tool_confirmations (requested_at)
    WHERE status = 'pending';
CREATE INDEX tool_confirmations_expiring ON tool_confirmations (expires_at)
    WHERE status = 'pending';

-- A call nobody answered in time is not a call a human denied, and the execution
-- record has to be able to say which it was.
ALTER TABLE tool_calls DROP CONSTRAINT tool_calls_decision_check;
ALTER TABLE tool_calls ADD CONSTRAINT tool_calls_decision_check CHECK (decision IN
    ('auto_allowed', 'user_allowed', 'user_denied', 'not_allowed',
     'killed', 'awaiting_confirmation', 'expired'));
