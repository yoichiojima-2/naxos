# naxos

A Google Cloud implementation of Claude Managed Agents (CMA): a secure, multi-tenant agent platform that mirrors the CMA object model (Agent → Environment → Session → Events), REST surface, and event vocabulary — running entirely inside the org's GCP boundary. Always *available* rather than always running (scale-to-zero), with governance (audit / approval / kill switch) built into the platform itself.

The full design is `docs/design.md` — read it before changing architecture-level code.

## Why this exists — and what it must not become

This platform does NOT compete with the Claude app or with hosted CMA on UX, model quality, or feature velocity. It exists only where they structurally can't go:

- **(b) Data boundary** — the load-bearing axis: model access via Vertex AI only, data never leaves the org boundary. Hosted CMA runs the loop and sandbox on Anthropic's cloud; this platform keeps both inside the project.
- **(c) Internal-system integration inside the boundary** — self-hosted MCP + Vertex is the only way to connect internal/closed-network systems without egress to Anthropic. Valuable only combined with (b).
- **(e) Execution-level governance** — per-tool-call audit (who / which tool / what args / what result), human approval gates on agent actions, instant per-agent kill switch, per-tenant IAM.
- (a) shared/sub-hourly/event-driven unattended execution and (d) own UI survive only narrowed, mostly in combination with (b).

A task that fits none of these axes belongs in the Claude app or hosted CMA, not here.

## Core concepts (CMA object model)

- **Agent** — a versioned DB row: instructions, model, tools, permission policy, effort level, budget/turn caps, vault/memory/skill refs. Updates create immutable versions; sessions pin a version. Cheap to create via API.
- **Environment** — the tenant/isolation boundary: per-environment service account, sandbox Cloud Run Job, session GCS bucket. Provisioned by Terraform from `terraform/environments.json` (`for_each` fan-out); the API only registers rows (409 until provisioned). Adding a tenant = a JSON entry + apply, never a new app.
- **Session** — durable state, not a process: metadata + event log in Postgres, SDK transcript + workspace in GCS. A Cloud Run Job execution exists only while the session is actively processing; idle sessions checkpoint and release their container; the next event relaunches (`rescheduling` → cold resume).
- **Events** — CMA vocabulary (`user.message`, `user.interrupt`, `user.tool_confirmation`, `agent.message`, `agent.tool_use`, `agent.artifact`, `session.status_idle` with stop_reason, `span.model_request_*`, …), append-only per-session `seq`, SSE streaming with `Last-Event-ID` replay. Live partial text rides transient `agent.message_delta` SSE frames (sandbox → internal `/stream` → LISTEN/NOTIFY) that are never persisted or replayed — the persisted `agent.message` is the only durable record.
- **Artifacts** — files an agent deliberately publishes as durable outputs via built-in sandbox tools (`artifact_create/list/delete/share/unshare`, an in-process MCP server, gated and audited like any tool call). Metadata in Postgres, content in the environment's session bucket; sharing mints a stable token URL that stays behind IAP — no public links.
- **BigQuery** — read-only data access via built-in `bigquery_list_datasets/list_tables/describe_table/query` sandbox tools (an in-process MCP server, gated and audited like any tool call). The image has no `bq`/`gcloud` CLI on purpose. The server exists only when Terraform passed the environment's `bigquery_datasets` list in as `BIGQUERY_DATASETS`, so an environment without the IAM opt-in has no BigQuery tools at all; size caps and the read-only restriction are enforced in code, never by prompt.
- **Deployments** — unattended scheduled runs: Cloud Scheduler fires a stored prompt into a fresh session of the agent. Operators create them via API/UI; agents create them from inside a session via built-in `schedule_create/list/delete` sandbox tools (gated and audited like any tool call, `created_by = agent:{session}`, capped at `MAX_AGENT_DEPLOYMENTS`). The CLI's session-local cron tools (`CronCreate` …) are disallowed in the sandbox — they schedule in container memory and die unfired at the next idle checkpoint.
- **Skills** — org-shared Agent Skills folders (`SKILL.md` + files) stored in Postgres and attached via `agent_versions.skill_ids`; mounted read-only into the sandbox as a local plugin outside the checkpointed workspace, rebuilt every wake and before every SDK turn. No writeback path — skills are edited only through the API. The sandbox always runs the SDK with `setting_sources=[]` so agent-written workspace settings (hooks, permissions) are never loaded; `Skill` invocations go through the normal `PreToolUse` permission gate and audit.
- **Vaults** — credentials for MCP servers and declared HTTP targets. Values live only in Secret Manager (never in Postgres, never in the sandbox); the sandbox calls `naxos-egress` on a route token and the proxy substitutes the real headers. Arbitrary bash egress stays credential-less by design.
- **Memory stores** — named file sets in Postgres, materialised into `ws/memory/{name}/` at wake and written back at checkpoint (last write wins; versioning deferred).

Documented deviations from CMA (docs/design.md §7): IAP auth instead of API keys, operator-provisioned environments, budget enforced post-response, per-principal favorites on agents / sessions / artifacts / skills as a naxos-only convenience surface, and auto-derived session titles from the first `user.message` (explicit titles always win). The UI's Monitoring view (`/v1/monitoring/summary`, cost/usage aggregates over `session_runs`) is likewise a naxos operator surface, not part of the object model.

## Architecture (summary — details in docs/design.md)

Single GCP project, `asia-northeast1`. Components:

- `naxos-api` — Cloud Run Service, IAP directly on Cloud Run (no LB), min=0: /v1 REST + SSE + UI static export
- `naxos-internal` — same image, internal ingress: sandbox↔control-plane channel, scheduler targets, 1-min reconciler
- `naxos-egress` — credential-substituting proxy: vault secrets never enter the sandbox; only `sa-egress` can read them
- `naxos-sbx-{env}` — Cloud Run Job per environment; one execution per session wake runs the Claude Agent SDK loop + tools
- Cloud SQL Postgres (db-g1-small, private IP) for control-plane state; BigQuery `audit.runs` + `audit.tool_calls` written only by the control plane; Cloud Scheduler for deployments + reconciler

## Governance (never retrofit)

- **Audit**: every tool call → BigQuery `tool_calls` with decision (`auto_allowed|user_allowed|user_denied|not_allowed|killed`); every wake-to-idle burst → `runs` with principal and cost (dataset `naxos_audit`, `AUDIT_DATASET`). BigQuery is write-only from the control plane — the UI's monitoring view aggregates the Postgres `session_runs` mirror instead, so the API never needs BigQuery read access.
- **Kill switch**: `agents.disabled`, checked at event accept AND inside the sandbox's `PreToolUse` gate before every tool call (15s cache + control-channel push). Disabling pauses the agent's deployments.
- **Approval gate**: permission policy `always_ask` → pending `tool_confirmations` row → `session.status_idle(requires_action)` → container released while waiting → `user.tool_confirmation` resumes.
- **Budget**: per-session hard cap; `budget_reached` pauses, raising the budget resumes.

## Hard constraints (do not violate)

- **Vertex AI is the only model exit.** Never introduce direct Anthropic API calls. *Temporary exception: an Anthropic API key (Secret Manager, never in code or state) until Claude models are enabled in Vertex Model Garden — revisit before real internal data is connected.*
- **Governance lives in the control plane and sandbox harness**, where agent config cannot bypass it. Tool restriction and guardrails are enforced in code at the permission gate, never by prompt — the SDK's `allowed_tools` only pre-approves, so it cannot carry the restriction.
- **The control plane never holds IAM-admin.** Environments are Terraform-provisioned.
- **Single project, `asia-northeast1`.** If a model needs another region, surface the data-residency implication — don't silently decide.
- **Cost: everything scale-to-zero.** Cloud SQL is the only always-on cost (reference sizing in docs/design.md §9 targets ≈ ¥10k/month infra). Global cap on concurrent sandbox executions (default 5).

## Conventions

- **All documentation, design docs, and internal-facing text in English.** Code, identifiers, and commit messages in English.
- Keep code simple: standard documented patterns over clever abstractions; no comments unless they state a constraint the code can't express.
- Terraform owns topology (one root module, GCS state backend, no module abstraction): SAs, IAM, Cloud SQL, BigQuery, buckets, service/job shells, budget alerts. gcloud/CI owns image deploys and secret values. Scheduler jobs for deployments are created by the control plane (`sa-api` has a narrow custom Scheduler role), not Terraform.
- Repo: `control-plane/` (FastAPI, entrypoints api|internal, plus `migrations/`), `sandbox-runner/` (claude-agent-sdk harness), `egress-proxy/`, `shared/` (pydantic event/config models), `ui/` (Next.js static export), `terraform/`, `scripts/` (deploy/status/ops shell), `docs/` (`design.md`, `img/`, `pages/`, sample `skills/`), `.github/workflows/` (CI, WIF deploy, Pages).
- **DB: plain SQL over asyncpg, no ORM.** Schema changes are new append-only `control-plane/migrations/NNN_*.sql` files, applied in sorted order at boot and recorded in `schema_migrations` — never edit an applied migration.
- **Keep README.md in sync with the app.** When a change alters what the README describes — capabilities, layout, status — update it in the same change. The README's UI screenshots live in `docs/img/` (currently `sessions.png`, `session-timeline.png`); when the UI visibly changes what they show, retake them (run the UI, capture the same views at similar width, overwrite the same filenames) or, if you can't run the UI, flag the stale screenshot in your summary instead of leaving it silently outdated.
- **Keep the public docs site in sync.** `docs/pages/` (`index.html`, `design.html`) is a hand-written HTML mirror of README.md and `docs/design.md`, deployed to GitHub Pages by `.github/workflows/pages.yml` on every push to `main` that touches it. There is no generator: a change to the design or the pitch must be reflected there in the same change, or called out as stale.

## Open issues

- R1 **resolved** (measured; docs/design.md §4.1): resume replays the pending tool call, so the pause/release/resume design holds. Two corrections it forced: confirmations are keyed on `sha256(tool_name + canonical input)` because `tool_use_id` changes across resume, and the permission gate is a **`PreToolUse` hook** — `can_use_tool` is shadowed by `allowed_tools` entries and by the CLI's read-only auto-approval, so it cannot gate every call.
- R2 **open, needs a decision**: Claude on Vertex resolves only on the **`global`** endpoint — not `asia-northeast1` (nor `us-east5`) — and a fresh project's Vertex quota for it starts at zero (live `rawPredict` → 429 `RESOURCE_EXHAUSTED`). So (1) using Vertex means accepting `global` inference rather than regional residency, and (2) a quota-increase request gates removing the Anthropic API-key exception. Do not connect real internal data until this is settled.
- Deferred: outcomes, multiagent, webhooks, memory versioning
