CREATE TABLE artifacts (
    id             text PRIMARY KEY,
    session_id     text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    agent_id       text NOT NULL REFERENCES agents (id),
    environment_id text NOT NULL REFERENCES environments (id),
    name           text NOT NULL,
    description    text,
    content_type   text NOT NULL DEFAULT 'application/octet-stream',
    size_bytes     bigint NOT NULL DEFAULT 0,
    version        int NOT NULL DEFAULT 1,
    share_token    text UNIQUE,
    shared_at      timestamptz,
    shared_by      text,
    created_by     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, name)
);

CREATE INDEX artifacts_by_agent ON artifacts (agent_id, created_at DESC);
