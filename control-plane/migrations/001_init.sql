CREATE TABLE environments (
    id                    text PRIMARY KEY,
    name                  text NOT NULL UNIQUE,
    service_account_email text NOT NULL,
    sandbox_job_name      text NOT NULL,
    session_bucket        text NOT NULL,
    cpu                   text NOT NULL DEFAULT '1',
    memory                text NOT NULL DEFAULT '2Gi',
    archived_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id             text PRIMARY KEY,
    name           text NOT NULL,
    environment_id text NOT NULL REFERENCES environments (id),
    latest_version int NOT NULL DEFAULT 1,
    disabled       boolean NOT NULL DEFAULT false,
    archived_at    timestamptz,
    created_by     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_versions (
    agent_id           text NOT NULL REFERENCES agents (id),
    version            int NOT NULL,
    instructions       text,
    model              text NOT NULL,
    tools              jsonb NOT NULL DEFAULT '[]'::jsonb,
    permission_policy  jsonb NOT NULL DEFAULT '{}'::jsonb,
    mcp_servers        jsonb NOT NULL DEFAULT '{}'::jsonb,
    vault_ids          text[] NOT NULL DEFAULT '{}',
    memory_store_ids   text[] NOT NULL DEFAULT '{}',
    default_budget_usd numeric,
    max_turns          int,
    created_by         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, version)
);

CREATE TABLE sessions (
    id                text PRIMARY KEY,
    agent_id          text NOT NULL REFERENCES agents (id),
    agent_version     int NOT NULL,
    overrides         jsonb NOT NULL DEFAULT '{}'::jsonb,
    environment_id    text NOT NULL REFERENCES environments (id),
    title             text,
    status            text NOT NULL DEFAULT 'idle'
                      CHECK (status IN ('idle', 'running', 'rescheduling', 'terminated')),
    stop_reason       text,
    budget_usd        numeric,
    cost_usd          numeric NOT NULL DEFAULT 0,
    vault_ids         text[] NOT NULL DEFAULT '{}',
    memory_store_ids  text[] NOT NULL DEFAULT '{}',
    resources         jsonb NOT NULL DEFAULT '[]'::jsonb,
    sdk_session_id    text,
    lease_id          uuid,
    lease_expires_at  timestamptz,
    execution_name    text,
    retry_count       int NOT NULL DEFAULT 0,
    last_event_seq    bigint NOT NULL DEFAULT 0,
    created_by        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    terminated_at     timestamptz,
    FOREIGN KEY (agent_id, agent_version) REFERENCES agent_versions (agent_id, version)
);

CREATE INDEX sessions_wakeable ON sessions (status, lease_expires_at)
    WHERE status <> 'terminated';

CREATE TABLE session_events (
    id           bigserial PRIMARY KEY,
    session_id   text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    seq          bigint NOT NULL,
    type         text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    principal    text,
    processed_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);

CREATE INDEX session_events_queued ON session_events (session_id, seq)
    WHERE processed_at IS NULL;

CREATE TABLE tool_confirmations (
    id           text PRIMARY KEY,
    session_id   text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    call_hash    text NOT NULL,
    tool_use_id  text,
    tool_name    text NOT NULL,
    input        jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'allowed', 'denied', 'expired')),
    deny_message text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    decided_by   text,
    decided_at   timestamptz,
    UNIQUE (session_id, call_hash)
);

CREATE TABLE deployments (
    id                 text PRIMARY KEY,
    agent_id           text NOT NULL REFERENCES agents (id),
    agent_version      int,
    name               text NOT NULL,
    cron               text NOT NULL,
    timezone           text NOT NULL DEFAULT 'Asia/Tokyo',
    initial_events     jsonb NOT NULL DEFAULT '[]'::jsonb,
    budget_usd         numeric,
    paused             boolean NOT NULL DEFAULT false,
    archived_at        timestamptz,
    scheduler_job_name text,
    created_by         text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deployment_runs (
    id            text PRIMARY KEY,
    deployment_id text NOT NULL REFERENCES deployments (id),
    session_id    text REFERENCES sessions (id),
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    error_type    text,
    error_message text,
    fired_at      timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);

CREATE TABLE vaults (
    id          text PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vault_credentials (
    id         text PRIMARY KEY,
    vault_id   text NOT NULL REFERENCES vaults (id),
    name       text NOT NULL,
    type       text NOT NULL CHECK (type IN ('env', 'header')),
    secret_ref text NOT NULL,
    target     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vault_id, name)
);

CREATE TABLE memory_stores (
    id         text PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memories (
    id         text PRIMARY KEY,
    store_id   text NOT NULL REFERENCES memory_stores (id) ON DELETE CASCADE,
    path       text NOT NULL,
    content    text NOT NULL,
    updated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (store_id, path)
);
