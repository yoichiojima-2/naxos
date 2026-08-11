CREATE TABLE skills (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    description text,
    archived_at timestamptz,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- archived skills keep their row (sessions may still reference the id) but
-- release the name for reuse
CREATE UNIQUE INDEX skills_active_name ON skills (name) WHERE archived_at IS NULL;

CREATE TABLE skill_files (
    id         text PRIMARY KEY,
    skill_id   text NOT NULL REFERENCES skills (id) ON DELETE CASCADE,
    path       text NOT NULL,
    content    text NOT NULL,
    updated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (skill_id, path)
);

ALTER TABLE agent_versions ADD COLUMN skill_ids text[] NOT NULL DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN skill_ids text[] NOT NULL DEFAULT '{}';
