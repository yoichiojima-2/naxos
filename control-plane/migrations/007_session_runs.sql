-- Per-burst run log for the monitoring view: one row per wake-to-idle burst,
-- mirroring the BigQuery audit.runs row so cost/usage can be queried without
-- granting the API BigQuery read access. cost_usd is the burst's delta.
CREATE TABLE session_runs (
    id             text PRIMARY KEY,
    session_id     text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    agent_id       text NOT NULL,
    environment_id text NOT NULL,
    trigger_type   text NOT NULL,
    principal      text,
    model          text,
    status         text NOT NULL,
    stop_reason    text,
    num_turns      int NOT NULL DEFAULT 0,
    cost_usd       numeric NOT NULL DEFAULT 0,
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX session_runs_ended_at ON session_runs (ended_at);
