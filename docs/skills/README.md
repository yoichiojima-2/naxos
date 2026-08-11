# Sample skills

Ready-to-upload skills in the [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) format: a folder with a `SKILL.md` entry file plus optional supporting files. Skills live in Postgres and are attached to agents via `agent_versions.skill_ids` — these folders are only source material; nothing in this directory is read at runtime.

## Uploading

```sh
NAXOS_API=https://<naxos-api-url> scripts/upload_skill.sh docs/skills/bigquery
```

The script creates the skill (or reuses the existing one with the same name) and syncs the folder: local files are upserted, remote files that no longer exist locally are deleted. Set `NAXOS_AUTH` to an extra request header if your access path needs one; against a dev-mode control plane no auth is required.

Then attach the returned `skill_id` to an agent version (`skill_ids` on `POST /v1/agents/{id}/versions`) or per session at creation.

## Samples

| Skill | What |
|---|---|
| `bigquery/` | Querying BigQuery from the sandbox with the REST API: metadata-server auth, dry-run cost checks, schema discovery, pagination, result publishing |
