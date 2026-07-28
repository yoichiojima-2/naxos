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
  bq.py      # BigQuery tools: scan/row/timeout caps, per-tenant cost labels
  gcs.py     # Cloud Storage tools: read-only, size-capped reads
  main.py    # CLI entrypoint
ws/          # agent workspace; .claude/skills/ holds investigation skills
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

## Design

See [CLAUDE.md](CLAUDE.md) for the full architecture: tenants as
configuration, Vertex AI as the only model exit, audit to BigQuery,
Scheduler-triggered Cloud Run jobs, and the phased rollout plan.
