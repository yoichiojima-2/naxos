-- The durable per-tool-call execution record. Written by the control plane at
-- the permission gate — the one point every tool call must pass — so the row is
-- committed before the tool runs and survives a sandbox that dies mid-call.
-- No FK to sessions: like session_runs, the record must outlive the session it
-- describes, so deleting a session cannot erase what an agent did.
CREATE TABLE tool_calls (
    id             bigserial PRIMARY KEY,
    session_id     text NOT NULL,
    run_id         text NOT NULL,
    agent_id       text NOT NULL,
    agent_version  int,
    environment_id text NOT NULL,
    principal      text,
    approved_by    text,
    tool_name      text NOT NULL,
    call_hash      text NOT NULL,
    tool_use_id    text,
    -- Text, not jsonb: these are the exact canonical bytes that call_hash was
    -- computed over, so sha256(tool_name || E'\n' || args_json) = call_hash is
    -- checkable by hand. jsonb would re-normalise them and break that.
    args_json      text NOT NULL DEFAULT '',
    args_truncated boolean NOT NULL DEFAULT false,
    decision       text NOT NULL CHECK (decision IN
                     ('auto_allowed', 'user_allowed', 'user_denied', 'not_allowed',
                      'killed', 'awaiting_confirmation')),
    result_status  text CHECK (result_status IN ('ok', 'error', 'denied', 'no_result')),
    latency_ms     int,
    error          text,
    decided_at     timestamptz NOT NULL DEFAULT now(),
    resulted_at    timestamptz,
    exported_at    timestamptz
);

CREATE INDEX tool_calls_decided_at ON tool_calls (decided_at DESC, id DESC);
CREATE INDEX tool_calls_session ON tool_calls (session_id, id DESC);
CREATE INDEX tool_calls_agent ON tool_calls (agent_id, id DESC);
-- tool_use_id is unstable across resume but stable within one execution, so the
-- result join is keyed on (session, run, tool_use_id) and only ever looks at
-- calls still awaiting a result.
CREATE INDEX tool_calls_open ON tool_calls (session_id, run_id, tool_use_id)
    WHERE result_status IS NULL;
CREATE INDEX tool_calls_unexported ON tool_calls (session_id) WHERE exported_at IS NULL;

-- turn_principal: who caused the turn being processed, latched from the queued
-- events when the sandbox claims them, so the hot permission path reads it as a
-- column instead of re-deriving it per tool call.
-- current_run_id: the sandbox's own run id, taken at claim. sessions.execution_name
-- is a full Cloud Run resource path and does not match it.
ALTER TABLE sessions
    ADD COLUMN turn_principal text,
    ADD COLUMN current_run_id text;

ALTER TABLE session_runs
    ADD COLUMN input_tokens  bigint NOT NULL DEFAULT 0,
    ADD COLUMN output_tokens bigint NOT NULL DEFAULT 0;
