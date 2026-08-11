ALTER TABLE deployment_runs
    DROP CONSTRAINT deployment_runs_session_id_fkey,
    ADD FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL;
