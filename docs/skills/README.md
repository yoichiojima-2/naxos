# Sample skills

Skills in the [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) format: a folder with a `SKILL.md` entry file plus optional supporting files. Skills live in Postgres and are attached to agents via `agent_versions.skill_ids` — these folders are source material, never read at runtime.

## Seeding

The control plane seeds every folder here on startup, create-once: a folder is imported only while no skill (active or archived) has ever used its name. After that the Postgres copy is the live one — edits through the API/UI are never overwritten by a restart, archiving a seeded skill does not resurrect it, and later changes to these folders reach an existing deployment only via the upload script below (which syncs files into the existing skill).

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
