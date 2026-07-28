# naxos

PoC of a secure, multi-tenant AI agent platform on GCP.

One runtime that agents are loaded onto: a tenant is only configuration — a
prompt, an allowed-tool list, trigger settings, and a dedicated service
account. Governance (audit trail, approval gate, kill switch) is built into
the platform layer, where tenant config cannot bypass it.

## Current state

Walking-skeleton playground: the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk)
running with guarded GCP tools and filesystem skills.

```
src/
  agent.py   # run_agent(): one agent task -> AgentRun (text, tool calls, thinking, cost)
  audit.py   # records every run to BigQuery audit.runs
  bq.py      # BigQuery tools: scan/row/timeout caps
  gcs.py     # Cloud Storage tools: read-only, size-capped reads
  main.py    # CLI entrypoint
skills/      # out-of-the-box skills, seeded to gs://$BUCKET/skills
terraform/   # service accounts + IAM (state in gs://$BUCKET/terraform)
tests/       # unit tests (mocked GCP clients, no credentials needed): uv run pytest
roles.json   # role -> mounted MCP servers + synced skills
ws/          # agent workspace; skills sync in at startup (gitignored)
notebook/    # experimentation playground
```

Guardrails live in code and IAM, never in prompts: tools are restricted by
not being passed to the agent, query scan size is rejected server-side
before running, and write access is blocked at the service-account level.

## Run

```sh
uv sync
echo 'ANTHROPIC_API_KEY=...' > .env   # or use Vertex AI via CLAUDE_CODE_USE_VERTEX
uv run python -m src.main "how many rows does bigquery-public-data.samples.shakespeare have?"
```

Requires GCP Application Default Credentials (`gcloud auth application-default login`).

On Cloud Run there is one job per role (`naxos-runner-ops`,
`naxos-runner-analyst`), each running as its role's service account with
`ROLE` baked in as an env var. The prompt is given per execution (the
`^@^` prefix keeps commas in the prompt from being split into separate
args):

```sh
gcloud run jobs execute naxos-runner-ops --region asia-northeast1 \
  --args="^@^any prompt here, commas included"
```

Shipping is automated: every push to `main` runs ruff, builds the image
tagged with the commit SHA, and rolls it to both jobs
(`.github/workflows/deploy.yml`, keyless GCP auth via Workload Identity
Federation). To roll back, point the jobs at an earlier commit's tag:

```sh
gcloud run jobs update naxos-runner-ops --region asia-northeast1 \
  --image asia-northeast1-docker.pkg.dev/naxos-503510/cloud-run-source-deploy/naxos-runner:<old-sha>
```

## Skills

`gs://$BUCKET/skills` is the live skill store: users add and edit skills
there directly. `main.py` downloads it into `ws/.claude/skills/` at
startup; on Cloud Run the bucket will be volume-mounted instead. The
runtime only ever reads the bucket. Set `BUCKET` in `.env`.

Out-of-the-box skills are versioned in `skills/` and seeded to the bucket
with (no delete flag, so user-added skills survive):

```sh
gcloud storage rsync --recursive skills "gs://$BUCKET/skills"
```

## Roles

`roles.json` maps a role to the MCP servers and skills its sessions get
(`uv run python -m src.main --role analyst "..."`). This controls which
guarded tools are mounted — the hard data boundary comes from IAM,
since built-in tools like Bash can reach anything the runtime
credentials allow: each role has a service account (`sa-role-<name>`,
managed in `terraform/`) that Cloud Run jobs will run as.

## Audit

Every run is recorded to BigQuery: `audit.runs` (partitioned on
`started_at`) holds the prompt, final answer, tool calls, token usage,
and cost per run. The agent can query its own trail:

```sh
uv run python -m src.main "how much have agent runs cost so far? check audit.runs"
```

## Design

See [CLAUDE.md](CLAUDE.md) for the full architecture: tenants as
configuration, Vertex AI as the only model exit, audit to BigQuery,
Scheduler-triggered Cloud Run jobs, and the phased rollout plan.
