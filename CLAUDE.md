# naxos

A Google Cloud implementation of Claude Managed Agents (CMA): a secure, multi-tenant agent platform that mirrors the CMA object model (Agent → Environment → Session → Events), REST surface, and event vocabulary — running entirely inside the org's GCP boundary. Always *available* rather than always running (scale-to-zero), with governance (audit / approval / kill switch) built into the platform itself.

The v1 PoC lives on the `poc` branch (reference only). v2 is a greenfield rebuild; the full design is `docs/design.md` — read it before changing architecture-level code.

## Why this exists — and what it must not become

This platform does NOT compete with the Claude app or with hosted CMA on UX, model quality, or feature velocity. It exists only where they structurally can't go:

- **(b) Data boundary** — the load-bearing axis: model access via Vertex AI only, data never leaves the org boundary. Hosted CMA runs the loop and sandbox on Anthropic's cloud; this platform keeps both inside the project.
- **(c) Internal-system integration inside the boundary** — self-hosted MCP + Vertex is the only way to connect internal/closed-network systems without egress to Anthropic. Valuable only combined with (b).
- **(e) Execution-level governance** — per-tool-call audit (who / which tool / what args / what result), human approval gates on agent actions, instant per-agent kill switch, per-tenant IAM.
- (a) shared/sub-hourly/event-driven unattended execution and (d) own UI survive only narrowed, mostly in combination with (b).

A task that fits none of these axes belongs in the Claude app or hosted CMA, not here.

## Core concepts (CMA object model)

- **Agent** — a versioned DB row: instructions, model, tools, permission policy, vault/memory refs. Updates create immutable versions; sessions pin a version. Cheap to create via API.
- **Environment** — the tenant/isolation boundary: per-environment service account, sandbox Cloud Run Job, session GCS bucket. Provisioned by Terraform from `terraform/environments.json` (`for_each` fan-out); the API only registers rows (409 until provisioned). Adding a tenant = a JSON entry + apply, never a new app.
- **Session** — durable state, not a process: metadata + event log in Postgres, SDK transcript + workspace in GCS. A Cloud Run Job execution exists only while the session is actively processing; idle sessions checkpoint and release their container; the next event relaunches (`rescheduling` → cold resume).
- **Events** — CMA vocabulary (`user.message`, `user.interrupt`, `user.tool_confirmation`, `agent.message`, `agent.tool_use`, `session.status_idle` with stop_reason, `span.model_request_*`, …), append-only per-session `seq`, SSE streaming with `Last-Event-ID` replay.

## Architecture (summary — details in docs/design.md)

Single GCP project, `asia-northeast1`. Components:

- `naxos-api` — Cloud Run Service, IAP directly on Cloud Run (no LB), min=0: /v1 REST + SSE + UI static export
- `naxos-internal` — same image, internal ingress: sandbox↔control-plane channel, scheduler targets, 1-min reconciler
- `naxos-egress` — credential-substituting proxy: vault secrets never enter the sandbox; only `sa-egress` can read them
- `naxos-sbx-{env}` — Cloud Run Job per environment; one execution per session wake runs the Claude Agent SDK loop + tools
- Cloud SQL Postgres (db-g1-small, private IP) for control-plane state; BigQuery `audit.runs` + `audit.tool_calls` written only by the control plane; Cloud Scheduler for deployments + reconciler

## Governance (never retrofit)

- **Audit**: every tool call → `audit.tool_calls` with decision (`auto_allowed|user_allowed|user_denied|killed`); every wake-to-idle burst → `audit.runs` with principal and cost.
- **Kill switch**: `agents.disabled`, checked at event accept AND inside `can_use_tool` before every tool call (15s cache + control-channel push). Disabling pauses the agent's deployments.
- **Approval gate**: permission policy `always_ask` → pending `tool_confirmations` row → `session.status_idle(requires_action)` → container released while waiting → `user.tool_confirmation` resumes.
- **Budget**: per-session hard cap; `budget_reached` pauses, raising the budget resumes.

## Hard constraints (do not violate)

- **Vertex AI is the only model exit.** Never introduce direct Anthropic API calls. *Temporary exception: an Anthropic API key (Secret Manager, never in code or state) until Claude models are enabled in Vertex Model Garden — revisit before real internal data is connected.*
- **Governance lives in the control plane and sandbox harness**, where agent config cannot bypass it. Tools are restricted by never passing them to the SDK, not by prompt.
- **The control plane never holds IAM-admin.** Environments are Terraform-provisioned.
- **Single project, `asia-northeast1`.** If a model needs another region, surface the data-residency implication — don't silently decide.
- **Budget: ¥100k/month hard cap, ¥70k target.** Infra ≈ ¥10k (Cloud SQL is the only always-on cost); everything else scale-to-zero. Global cap on concurrent sandbox executions (default 5).

## Conventions

- **All documentation, design docs, and internal-facing text in English.** Code, identifiers, and commit messages in English.
- Keep code simple: standard documented patterns over clever abstractions; no comments unless they state a constraint the code can't express.
- Terraform owns topology (one root module, GCS state backend, no module abstraction): SAs, IAM, Cloud SQL, BigQuery, buckets, service/job shells, budget alerts. gcloud/CI owns image deploys and secret values. Scheduler jobs for deployments are created by the control plane (`sa-api` has a narrow custom Scheduler role), not Terraform.
- Repo: `control-plane/` (FastAPI, entrypoints api|internal), `sandbox-runner/` (claude-agent-sdk harness), `egress-proxy/`, `shared/` (pydantic event/config models), `ui/` (Next.js static export), `terraform/`, `docs/`.

## Open issues

- R1 **resolved** (2026-08-10 spike, docs/design.md §4.1): resume replays the pending tool call, so the pause/release/resume design holds. Two corrections it forced: confirmations are keyed on `sha256(tool_name + canonical input)` because `tool_use_id` changes across resume, and the permission gate is a **`PreToolUse` hook** — `can_use_tool` is shadowed by `allowed_tools` entries and by the CLI's read-only auto-approval, so it cannot gate every call.
- R2 **open, needs a decision**: Claude on Vertex resolves only on the **`global`** endpoint — not `asia-northeast1` (nor `us-east5`) — and this project's Vertex quota for it is currently zero (live `rawPredict` → 429 `RESOURCE_EXHAUSTED`). So (1) using Vertex means accepting `global` inference rather than regional residency, and (2) a quota-increase request gates removing the Anthropic API-key exception. Do not connect real internal data until this is settled.
- Deferred: outcomes, multiagent, webhooks, memory versioning
