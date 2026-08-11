CREATE TABLE favorites (
    id          text PRIMARY KEY,
    principal   text NOT NULL,
    entity_type text NOT NULL CHECK (entity_type IN ('agent', 'session', 'artifact', 'skill')),
    entity_id   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal, entity_type, entity_id)
);
