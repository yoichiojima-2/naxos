CREATE TABLE egress_routes (
    token         text PRIMARY KEY,
    session_id    text NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    credential_id text REFERENCES vault_credentials (id) ON DELETE CASCADE,
    target_url    text NOT NULL,
    header        text NOT NULL DEFAULT 'authorization',
    value_prefix  text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX egress_routes_session ON egress_routes (session_id);
