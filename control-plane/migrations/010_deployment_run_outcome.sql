-- Deployment runs only ever recorded their start: 'running' rows were never
-- closed, so a run had no outcome, duration, or cost. Fold each wake-to-idle
-- burst of the fired session back into its run so the runs view can show what
-- actually happened.
ALTER TABLE deployment_runs
    ADD COLUMN started_at  timestamptz,
    ADD COLUMN stop_reason text,
    ADD COLUMN cost_usd    numeric NOT NULL DEFAULT 0,
    ADD COLUMN num_turns   int NOT NULL DEFAULT 0;

-- 'cancelled' is an operator terminating (or deleting) the fired session: an
-- outcome, not a failure of the schedule.
ALTER TABLE deployment_runs
    DROP CONSTRAINT deployment_runs_status_check,
    ADD CONSTRAINT deployment_runs_status_check
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'));

CREATE INDEX deployment_runs_fired_at ON deployment_runs (fired_at DESC);
CREATE INDEX deployment_runs_open ON deployment_runs (session_id) WHERE finished_at IS NULL;
