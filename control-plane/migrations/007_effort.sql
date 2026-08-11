ALTER TABLE agent_versions ADD COLUMN effort text
    CHECK (effort IN ('low', 'medium', 'high', 'xhigh', 'max'));
