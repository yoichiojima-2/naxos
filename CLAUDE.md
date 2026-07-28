# naxos

PoC of a secure, multi-tenant agent platform on GCP. Goal: let teams run shared, unattended server-side AI agents against internal systems — always *available* rather than always running (scale-to-zero) — with governance (audit / approval / kill switch) built into the platform itself. The first deployment serves a 5-person working group, but that is a starting point, not a design limit: nothing in the architecture assumes a user count.

Scaling happens along three independent dimensions, none requiring redesign:

- **Users**: access is a Google group behind IAP — onboarding is group membership, and humans appear in the audit log as `principal`
- **Tenants (agents)**: tenants are pure configuration (see below) — horizontal rollout is a tfvars entry per tenant
- **Load**: Cloud Run autoscales both run modes; the sizing levers are Cloud SQL tier and the budget cap, which are deployment parameters, not architecture

## Why this exists — and what it must not become

This platform does NOT compete with the Claude app on UX, model quality, or feature velocity. It exists only where the Claude app structurally can't go — 5 axes, **not of equal strength**: (b) is the load-bearing one, (a) and (c) survive only narrowed and mostly in combination with (b). Capability claims below were verified against Claude's out-of-box features as of **2026-07**; re-verify at each phase gate — they erode fast (Cowork scheduled tasks shipped 2026-02, Routines 2026-06, remote MCP connectors GA).

- **(a) Shared / sub-hourly / event-driven unattended execution** — the Claude app now covers *personal* scheduled runs (Cowork scheduled tasks; Claude Code Routines, 1-hour minimum interval, running in Anthropic's cloud). What it still can't do: team-shared agents with their own identity, sub-hourly cadence (P3 runs every 15 min), and event/webhook triggers with queue and retry semantics
- **(b) Data boundary** — model access via Vertex AI only, so data never leaves the org boundary; everything internal stays inside a VPC
- **(c) Internal-system integration inside the data boundary** — the Claude app now supports custom *remote* MCP connectors, so "no connector exists" is no longer the gap. What it can't do: reach systems not exposed to Anthropic's cloud (on-prem / closed-network), and keep the data internal — a remote MCP call still egresses to Anthropic. Self-hosted MCP inside the VPC + Vertex is the only way to connect internal systems *without* data leaving the boundary. This axis now only has value combined with (b)
- **(d) Own UI** — business-specific screens behind IAP, usable by non-technical members
- **(e) Execution-level governance** — Claude Enterprise does offer audit logs (30-day retention, SIEM export) and RBAC, but at conversation/console level — no per-tool-call execution trace, no human approval gates on agent actions, no per-agent kill switch. This platform audits *what the agent did* (who / which tool / what args / what result), gates irreversible operations on human approval, and can kill a tenant instantly

A task that fits none of these axes belongs in the Claude app, not here. Putting it here is a design error. (Example: meeting recording → summary → team review is a Claude app / Cowork task — it migrates here only if the content itself can't leave the boundary.)

## Core concept: tenants are configuration

This is **one runtime that agents are loaded onto**, not a collection of agent apps. A tenant is only: prompt + allowed-tool list + trigger settings + a dedicated service account. Adding a tenant must never require code changes.

Planned tenants:

- **P3 — operational monitoring** (unattended, the PoC target): watches logs/metrics every 15 min, diagnoses anomalies, notifies Slack with runbook suggestions. Phase 1: read-only, notify only; recovery actions (behind the approval gate) come with Phase 2
- **P11 — internal helpdesk** (interactive, Phase 2): first-line answers from internal rules/manuals; buy-vs-build must be evaluated before building — commercial products are mature here
- **P1 — proposal/estimate support** (interactive, Phase 3): drafts from project DB + estimation logic

## Architecture (target state)

Single GCP project, region `asia-northeast1`. One container image, two run modes:

- **`agent-runner`** — Cloud Run Service, internal ingress, scale-to-zero: interactive tenants (P11/P1)
- **`agent-runner-job`** — Cloud Run Job, launched by Cloud Scheduler with the tenant ID as argument: unattended tenants (P3)

Inside the container (shared by both modes):

- **Claude Agent SDK** — agent loop, Vertex AI backend (no API keys)
- **Guardrail layer** — tools are restricted by *not passing them to the SDK at all* (never by prompt instructions); args schema-validated; write tools flagged for the approval gate
- **DLP wrapper** — Sensitive Data Protection de-identify on all model input and any output leaving the org (e.g. Slack); templates per tenant, managed in Terraform
- **Audit emitter** — every tool call streamed to BigQuery

Surrounding services:

- **Access (Phase 2)**: External HTTPS LB → IAP (allow via Google group, never individual emails) → `agent-ui` Cloud Run Service (chat, approval inbox, admin panel); UI must verify the IAP JWT
- **Triggers**: Cloud Scheduler (cron per tenant), Cloud Tasks (queue/retry), Eventarc/webhooks (later)
- **State**: Cloud SQL PostgreSQL db-g1-small, private IP — `tenants` (config + disabled flag), `sessions`, `runs`, `approvals`
- **Audit**: BigQuery dataset `agent_audit` with `tool_calls(run_id, tenant_id, principal, ts, tool_name, args_redacted, result_status, latency_ms, input_tokens, output_tokens)` and `runs(run_id, tenant_id, trigger_type, started_at, ended_at, status, total_tokens, approx_cost_jpy)`
- **Secrets**: internal-system credentials live in Secret Manager, mounted into MCP servers only — the agent never sees raw credentials
- **Slack**: notifications and interactive approval buttons

### IAM — per-tenant service accounts

Each tenant gets its own SA with minimum roles (e.g. `sa-runner-p3`: `aiplatform.user`, `dlp.user`, `cloudsql.client`, BigQuery read on monitored datasets only, BigQuery write on audit only). UI SA has no model access. This is defense-in-depth: even if prompt injection defeats the guardrail layer, IAM still blocks cross-tenant access.

## Governance (never retrofit — build in from day one)

- **Audit log**: all tool calls to BigQuery, args stored only after DLP redaction; cost per run computed from tokens
- **Kill switch**: `tenants.disabled` flag, checked at run start and before every tool call; admin action also pauses the tenant's Scheduler job
- **Approval gate**: write/irreversible tool calls are queued in `approvals` and block until a human approves via Slack button or UI inbox; timeout = automatic deny
- **Pre-posting verification**: reports lacking supporting evidence/metrics are blocked before posting (production deployments of similar agents found early unfounded conclusions to be the top quality problem)
- **Investigation skills**: system-specific investigation know-how is added incrementally as skills, not baked into one giant prompt

These map to AI Governance Navi ver2.0 controls (No.24 / No.22 / No.32): execution audit trail, human involvement in irreversible actions, instant stop, data minimization.

## Knowledge layer

Agents need shared knowledge, but there are two distinct kinds — don't conflate them:

- **Curated operator knowledge** (runbooks, "how our systems behave," investigation observations): small, human-authored, version-controlled markdown — Obsidian-style linked files, delivered as skills. This is all Phase 1 (P3) needs; **no vector store**.
- **Reference corpus** (P11's manuals/rules, hundreds of docs where the relevant one can't be predicted): the actual RAG case, and only from Phase 2. Use **pgvector on the existing Cloud SQL** (not a separate vector DB), embeddings via **Vertex AI** (never a public embedding API — that would break the data boundary), exposed as an MCP "knowledge search" tool so the agent interface is unchanged when the backend upgrades from file-grep to vectors.

Rules that fall out of the existing model: knowledge collections are **part of tenant config** (P11 sees HR manuals, P3 sees infra runbooks, never each other's); retrieved text still passes **DLP** before the model and is **audited** (what was retrieved is part of the execution trace and enables source citation). Note: a shared knowledge base is *not* a standalone reason to build — Claude Projects already does curated knowledge + retrieval. It earns its place only because here it stays inside the boundary (b) and is tenant-scoped + audited (e).

## PoC scope (Phase 1 — walking skeleton)

The shortest loop, end to end: Scheduler → Cloud Run Job → Agent SDK → Vertex AI → Slack notification, **with audit log and kill switch included from the start**. The platform monitors itself (dogfooding).

Out of scope for Phase 1: UI, LB/IAP, DLP proxy, standalone MCP servers (integrations start in-process inside the SDK; split out to Cloud Run only when shared by multiple tenants), and the approval gate — which is safe to defer only because **Phase 1 P3 gets read-only tools**: it observes and notifies, never executes recovery. Recovery-with-approval arrives together with the gate in Phase 2.

Later phases: Phase 2 = LB/IAP + shared UI + DLP + approval gate + P11 (after buy-vs-build check). Phase 3 = P1 + project-DB MCP + cost measurement with 3 tenants running.

## Hard constraints (do not violate)

- **Vertex AI is the only model exit.** Never introduce Anthropic API keys or direct API calls; org policy restricts egress.
- **Governance lives in the runtime wrapper layer**, where tenant config cannot bypass it.
- **Single project, `asia-northeast1`.** Claude model availability in this region is an open question — if a model requires another region, surface it (data-residency implications), don't silently decide.
- **Budget: ¥100k/month hard cap, ¥70k target** (sized for the current working group — a deployment parameter that grows with adoption, not a platform property). Infra ≈ ¥20k/month (LB+IAP ~3k, UI min=1 ~5k, runner/jobs ~2k, Cloud SQL ~7k, rest ~3k), leaving ~¥50k for model usage. Budget alerts at both thresholds. Prefer scale-to-zero; the only always-on service is the UI (Phase 2).

## Conventions

- Design docs and internal-facing text in Japanese; code, identifiers, and commit messages in English.
- Keep code simple: standard documented patterns over clever abstractions; no comments unless they state a constraint the code can't express.
- Infrastructure as code (Terraform) owns the topology: service accounts, IAM bindings, Scheduler jobs, BigQuery, Cloud SQL, Secret Manager secrets (containers, not values), budget alerts, DLP templates. Keep it boring: one root module, GCS state backend, no module abstraction.
- gcloud/CI owns the day-to-day: image deploys (`gcloud run deploy` with `lifecycle { ignore_changes = [image] }` on the Terraform side), secret *values* (`gcloud secrets versions add` — never in state or git), ad-hoc ops.
- Adding a tenant = a tfvars entry (SA + IAM + Scheduler + config row), never a new app.

## Open issues (resolve explicitly, don't assume)

- Claude model region availability vs. data-residency requirements
- Whether on-prem/closed-network systems exist (would require Cloud VPN / Interconnect)
- UI stack (build lightweight chat UI vs. reuse OSS) — decide at Phase 2 start
- P11 buy vs build — compare against commercial helpdesk products before building
