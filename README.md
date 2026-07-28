# naxos

PoC of a secure, multi-tenant AI agent platform on GCP.

One runtime that agents are loaded onto: a tenant is only configuration — a
prompt, an allowed-tool list, trigger settings, and a dedicated service
account. Governance (audit trail, approval gate, kill switch) is built into
the platform layer, where tenant config cannot bypass it.

## Current state

The Phase 1 walking skeleton is complete and running: Cloud Scheduler →
Cloud Run Job → [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk)
with guarded GCP tools → Slack notification, with the audit trail and
kill switch built in from the start. The platform monitors itself
daily (dogfooding).

```
src/
  agent.py   # run_agent(): one agent task -> AgentRun (text, tool calls, thinking, cost)
  audit.py   # records every run to BigQuery audit.runs
  bq.py      # BigQuery tools: scan/row/timeout caps
  gcs.py     # Cloud Storage tools: read-only, size-capped reads
  main.py    # entrypoint: kill-switch check, skills/session sync, run, audit, notify
  slack.py   # webhook notification
skills/      # out-of-the-box skills, seeded to gs://$BUCKET/skills
terraform/   # all topology: SAs + IAM, jobs, scheduler, secrets, WIF (state in gs://$BUCKET/terraform)
tests/       # unit tests (mocked GCP clients, no credentials needed): uv run pytest
roles.json   # role -> servers, skills, notify
ws/          # agent workspace; skills sync in at startup (gitignored)
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
`ROLE` baked in as an env var. The prompt is a single argument, given per
execution. In the console's "Execute job with overrides"
container-arguments field, wrap it in double quotes. From the CLI, gcloud
splits `--args` on commas only, so the `^@^` delimiter prefix is needed
just for prompts containing literal commas:

```sh
gcloud run jobs execute naxos-runner-ops --region asia-northeast1 \
  --args="a prompt without commas needs nothing special"

gcloud run jobs execute naxos-runner-ops --region asia-northeast1 \
  --args="^@^a prompt with commas, like this one"
```

Shipping is automated: every push to `main` runs ruff, builds the image
tagged with the commit SHA, and rolls it to both jobs
(`.github/workflows/deploy.yml`, keyless GCP auth via Workload Identity
Federation). To roll back, point the jobs at an earlier commit's tag:

```sh
gcloud run jobs update naxos-runner-ops --region asia-northeast1 \
  --image asia-northeast1-docker.pkg.dev/naxos-503510/cloud-run-source-deploy/naxos-runner:<old-sha>
```

## Scheduled runs

Terraform creates one Cloud Scheduler job per scheduled role
(`naxos-schedule-<role>`, seeds in `terraform/main.tf`) and thereafter
ignores its cron and prompt — like secret values and images, those are
day-to-day values owned by gcloud, editable without an apply. ops runs
daily at 09:00 JST with a self-monitoring prompt: the platform checks
its own `audit.runs` for errors and cost anomalies.

```sh
# change the cadence
gcloud scheduler jobs update http naxos-schedule-ops --location asia-northeast1 \
  --schedule="*/15 * * * *"

# change the prompt (the request body carries it as a container-arg override)
gcloud scheduler jobs update http naxos-schedule-ops --location asia-northeast1 \
  --message-body="$(jq -n --arg p 'new prompt' \
    '{overrides:{containerOverrides:[{args:[$p]}]}}')"

# see the current values
gcloud scheduler jobs describe naxos-schedule-ops --location asia-northeast1
```

## Kill switch

A marker object in GCS disables a role: the runtime checks it at startup
and refuses to run. Works on scheduled and manual executions alike.

```sh
echo "reason" | gcloud storage cp - "gs://$BUCKET/disabled/ops"   # kill
gcloud scheduler jobs pause naxos-schedule-ops --location asia-northeast1
gcloud storage rm "gs://$BUCKET/disabled/ops"                     # restore
gcloud scheduler jobs resume naxos-schedule-ops --location asia-northeast1
```

## Slack

Roles with `"notify": true` in `roles.json` post each run's final
answer to Slack via the webhook in the `slack-webhook-url` secret —
truncated at 3000 chars, with a footer carrying the cost and the
`session_id` so the conversation can be picked up with `--resume`.
Without `SLACK_WEBHOOK_URL` set, notification is skipped.

## Skills

`gs://$BUCKET/skills` is the live skill store: users add and edit skills
there directly. At startup `main.py` downloads the role's skills into
`ws/.claude/skills/` — locally and on Cloud Run alike. The main bucket
is read-only for the runtime (enforced by IAM, not convention); the
only thing it writes is session transcripts, which live in per-role
buckets (`$BUCKET-sessions-<role>`) so role isolation is plain IAM
with no conditions. Set `BUCKET` in `.env`.

Out-of-the-box skills are versioned in `skills/` and seeded to the bucket
with (no delete flag, so user-added skills survive):

```sh
gcloud storage rsync --recursive skills "gs://$BUCKET/skills"
```

## Roles

`roles.json` maps a role to the MCP servers and skills its sessions get
(`uv run python -m src.main --role analyst "..."`), plus per-role
behavior: `notify` (Slack). This controls which
guarded tools are mounted — the hard data boundary comes from IAM,
since built-in tools like Bash can reach anything the runtime
credentials allow: each role has a service account (`sa-role-<name>`,
managed in `terraform/`) that Cloud Run jobs will run as.

## Audit

Every run is recorded to BigQuery: `audit.runs` (partitioned on
`started_at`) holds the prompt, final answer, tool calls, token usage,
and cost per run, along with the Agent SDK `session_id`. After each run
the transcript and any workspace files the agent produced are saved to
the role's session bucket (`<session_id>/transcript.jsonl` +
`<session_id>/ws/`), and both are restored on demand — so a previous
conversation continues anywhere with `--resume`, files included.
Locally:

```sh
uv run python -m src.main --resume <session_id> "follow-up question"
```

or on Cloud Run — gcloud's comma-splitting yields the three arguments;
for a follow-up containing literal commas, switch the delimiter:

```sh
gcloud run jobs execute naxos-runner-ops \
  --args="--resume,<session_id>,follow-up question"

gcloud run jobs execute naxos-runner-ops \
  --args="^@^--resume@<session_id>@follow-up, with commas"
```

The agent can query its own trail:

```sh
uv run python -m src.main "how much have agent runs cost so far? check audit.runs"
```

## Design

See [CLAUDE.md](CLAUDE.md) for the full architecture: tenants as
configuration, Vertex AI as the only model exit, audit to BigQuery,
Scheduler-triggered Cloud Run jobs, and the phased rollout plan.
