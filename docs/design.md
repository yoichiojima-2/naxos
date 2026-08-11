# naxos v2 — Claude Managed Agents on Google Cloud

Design document. Status: approved 2026-08-10. All project documentation is written in English.

## 1. Context and goal

naxos v1 was a Phase-1 walking skeleton: `roles.json` "tenants", per-role Cloud Run Jobs fired by Cloud Scheduler, a single-table audit log, and a Next.js chat UI behind IAP. It proved the loop (Scheduler → Cloud Run Job → Claude Agent SDK → Slack) with audit and a kill switch. That code is preserved on the `poc` branch and is reference-only.

v2 rebuilds the platform greenfield as a **faithful Google Cloud implementation of Claude Managed Agents (CMA)** — the same object model, REST surface, and event vocabulary as `platform.claude.com/docs/en/managed-agents`, running entirely inside the org's GCP boundary. The platform's original reason to exist is unchanged (see CLAUDE.md): the data boundary (Vertex-only model exit), internal-system integration inside that boundary, and execution-level governance that the Claude app cannot provide.

Decisions fixed with the owner:

- **Faithful API clone** of the CMA REST surface and `{domain}.{action}` event types.
- **Container per session**: each session's agent loop + tools run in an isolated container.
- **V1 scope**: agents / environments / sessions / events core, scheduled deployments, permission policies (`always_ask` approval), vaults **with a real egress proxy** (full CMA credential fidelity — credentials never enter the sandbox), memory stores. Outcomes, multiagent, and webhooks are out of v1.
- **Cold resume accepted**: idle sessions release their container; the next event relaunches it (CMA's `rescheduling` status). UI shows a "waking up" state.
- **Environments are Terraform-provisioned**: the API only registers rows; the per-environment SA / sandbox Job / bucket come from a `for_each` over `terraform/environments.json`. The control plane never holds IAM-admin.

## 2. Core design stance

**Sessions are durable state, not processes.** A session lives in Postgres (metadata, event log) and GCS (SDK transcript, workspace). A container — one Cloud Run Job execution — exists only while the session is actively processing events. When the queue drains, the sandbox checkpoints to GCS and exits; the next incoming event relaunches it. This is the only shape that satisfies both CMA-style per-session isolation and scale-to-zero under the ¥100k/month cap.

## 3. Components

| Component | GCP resource | Scaling | SA |
|---|---|---|---|
| `naxos-api` — /v1 REST + SSE, serves the UI static export | Cloud Run Service, IAP directly on Cloud Run (no LB) | min=0, request timeout 3600s (SSE) | `sa-api` |
| `naxos-internal` — sandbox↔control-plane channel, scheduler targets, reconciler (same image as api, entrypoint flag) | Cloud Run Service, internal ingress, IAM auth | min=0 | `sa-api` |
| `naxos-egress` — credential-substituting proxy for MCP/HTTP egress | Cloud Run Service, internal ingress, IAM auth | min=0 | `sa-egress` (sole secretAccessor on vault secrets) |
| `naxos-sbx-{env}` — session sandbox: Claude Agent SDK loop + tools; one Job per environment, one execution per session wake | Cloud Run Job (`max_retries=0`, task timeout 3600s, self-checkpoint at ~55 min) | zero when idle | `sa-env-{env}` |
| Deployment cron `naxos-deploy-{id}`; reconciler tick (1/min) | Cloud Scheduler | — | `sa-scheduler` |
| State | Cloud SQL Postgres `db-g1-small`, private IP, Direct VPC egress from Cloud Run | always-on (the one fixed cost) | — |
| Workspaces | GCS bucket per environment `naxos2-sess-{env}` | — | env SA + `sa-api` only |
| Audit | BigQuery `audit.runs` + `audit.tool_calls`, written **only by the control plane** | — | `sa-api` |
| UI — agents, session chat/timeline, approval inbox, deployments, vaults, memory, skills, kill switch | Next.js static export baked into the api image | — | — |

The **environment is the tenant/isolation boundary** (as in CMA). Agents are cheap DB rows; environments carry the service account, the sandbox Job, and the session bucket, fanned out by Terraform from `environments.json` (the v1 `roles.json` pattern, re-derived). Many agents can share an environment.

## 4. Session lifecycle and event flow

Statuses: `idle | running | rescheduling | terminated`, with `stop_reason` on idle: `end_turn | requires_action | budget_reached | retries_exhausted`.

```
create ──▶ [idle]  (no container; workspace created lazily in GCS)
   │   user event → INSERT session_events (per-session seq; processed_at NULL = queued)
   ▼   no live lease? jobs.run(naxos-sbx-{env}, args=["--session", id])
[rescheduling]
   ▼   sandbox boot:
[running]   1. OIDC ID token → POST /internal/sessions/{id}/claim   (lease)
            2. GET /internal/sessions/{id}/config   (resolved agent version, tools,
               permission policies, egress routes, memory snapshot)
            3. restore gs://…/{id}/transcript.jsonl + ws/ ; write memory files
            4. ClaudeSDKClient(resume=sdk_session_id, cwd=ws, mcp_servers=…,
               can_use_tool=permission_hook)  — Vertex AI backend
            loop: long-poll /internal/sessions/{id}/queue?wait=25
                  (queued events + control signals: interrupt / kill / terminate)
                  → SDK turn → batch-POST agent.*/span.* events to /internal
   ▼   queue empty → emit session.status_idle(stop_reason) → linger ≈120s
[idle]  new event during linger → back to [running] warm
        linger expires → checkpoint (transcript + ws delta + memory writeback)
        → release lease → exit → cost 0
   ▼   next user event → relaunch (cold resume, ~10–40s)
[terminated]  explicit terminate; live sandbox told over the control channel
```

**Client-facing streaming.** `GET /v1/sessions/{id}/events?stream=sse&after={seq}` — the API polls `session_events` every 1s, emits SSE frames with `id:{seq}`, honors `Last-Event-ID` for reconnect replay, sends a ping comment every 15s. This is v1's `EventStream` re-derived but DB-backed: replay survives instance loss, so no Cloud Run session affinity is needed anywhere. LISTEN/NOTIFY is a later latency optimization, deliberately not built in v1.

**Interrupt.** `user.interrupt` is inserted as an event and surfaced immediately on the control channel; the sandbox calls `client.interrupt()`. Jumps the queue, per CMA.

**Permission pause (`always_ask`).** The gate is a **`PreToolUse` hook**, not `can_use_tool` — see §4.1 for why. On every tool call:

1. Check the kill switch (`agents.disabled`; 15s cache + control-channel push).
2. Match the tool against the agent version's permission policy. `always_allow` → allow, record the decision in audit.
3. `always_ask` → look up `tool_confirmations` by `(session_id, call_hash)` where `call_hash = sha256(tool_name + canonical_json(input))`. Stored decision → return it. **The key is the call hash, not `tool_use_id`** — the SDK assigns a fresh `tool_use_id` when a pending call is replayed after resume (measured; §4.1).
4. No decision → insert a pending row, emit `session.status_idle(stop_reason=requires_action)`, long-poll for the decision within the linger window. If it arrives, answer in-process (warm path). If not, **checkpoint and exit** — a blocked container costs nothing.
5. A later `user.tool_confirmation` stores the decision and relaunches the sandbox. The sandbox resumes with a synthetic continuation prompt; the model re-issues the same tool call, the hook fires again, and step 3 answers instantly.

### 4.1 Spike results (2026-08-10, `claude-agent-sdk` 0.2.134)

Three measured findings, all load-bearing:

- **Resume replays the pending tool call — R1 is resolved.** Killing the process inside the permission callback (mid-decision) and then resuming with `resume=<session_id>` plus a continuation prompt made the model re-issue the *same tool with the same input*, and the callback fired again. The pause/release/resume design works as specified.
- **`tool_use_id` is not stable across resume.** The replayed call carried a new `toolu_…` id. Confirmation records must therefore be keyed on a canonical hash of `(tool_name, input)`, not on `tool_use_id`. (`tool_use_id` is still recorded for audit.)
- **`can_use_tool` is not a complete gate; use a `PreToolUse` hook.** Two shadowing behaviors: any whole-tool entry in `allowed_tools` auto-approves before the callback runs (the SDK warns about this explicitly), and under the default permission mode the CLI auto-approves calls it judges read-only — a read-only `echo` never reached the callback while a file-writing `bash` did. A permission policy that claims "every `always_ask` call is gated" cannot be built on `can_use_tool`; the `PreToolUse` hook fires for every call and is the correct mechanism.
- **Resume needs a nudge.** A resumed session does not continue on its own; the sandbox sends a synthetic continuation user message after restoring. This is internal and is not persisted as a `user.message` event.

**Budget.** The harness checks `cost_usd + accrued >= budget_usd` before dispatching each queued user event and after each model response; on breach it interrupts, emits `session.status_idle(stop_reason=budget_reached)`, checkpoints, exits. Deviation from CMA: this is a post-response check (the SDK has no pre-request hook), so worst-case overshoot is one model call. `PATCH /v1/sessions/{id}` raises the budget; the next event resumes the session.

**Crash safety.** The sandbox heartbeats its lease every 30s. A 1-minute reconciler relaunches sessions that have queued events and an expired lease, up to `MAX_RETRIES=3` per batch, then emits `session.error` + `session.status_idle(stop_reason=retries_exhausted)`. The Job never self-retries (`max_retries=0`); the sandbox self-checkpoints at ~55 min and exits cleanly so a fresh execution can continue.

## 5. Vault egress proxy (full CMA fidelity)

Credentials never enter the sandbox:

- `POST /v1/vaults/{id}/credentials` writes the secret value straight to Secret Manager. Postgres stores metadata and the secret ref only; values are write-only and never returned by the API.
- Agent config declares MCP servers by their real URL. The sandbox's resolved config rewrites MCP URLs to `naxos-egress` with an opaque route token. The proxy authenticates the caller (OIDC; the SA must match the session's environment), resolves session → vault_ids → credential matched by target URL, injects the Authorization/header, and forwards to the real server.
- Env-var-style credentials for CLIs: the sandbox env carries an opaque placeholder; the proxy substitutes it in request headers for declared HTTP targets routed through it. Transparent interception of arbitrary egress is out of scope — bash traffic not routed through the proxy simply has no credential. This limitation is documented, not hidden.
- Only `sa-egress` holds `secretAccessor` on vault secrets. Environment SAs hold none.

## 6. Data model

### Postgres (control-plane state)

```sql
environments (id, name, service_account_email, sandbox_job_name, session_bucket,
              cpu, memory, archived_at, created_at)

agents (id, name, environment_id, latest_version, disabled, archived_at,
        created_at, updated_at)

agent_versions (agent_id, version, PRIMARY KEY (agent_id, version),
        instructions, model, tools jsonb, permission_policy jsonb,
        vault_ids text[], memory_store_ids text[],
        default_budget_usd numeric, max_turns int, created_by, created_at)

sessions (id, agent_id, agent_version, overrides jsonb, environment_id,
        status CHECK IN ('idle','running','rescheduling','terminated'),
        stop_reason, budget_usd, cost_usd DEFAULT 0,
        vault_ids text[], memory_store_ids text[], resources jsonb,
        sdk_session_id, lease_id uuid, lease_expires_at, execution_name,
        retry_count DEFAULT 0, last_event_seq DEFAULT 0,
        created_by, created_at, updated_at, terminated_at)

session_events (id bigserial, session_id, seq, UNIQUE (session_id, seq),
        type, payload jsonb,        -- ≤64KB; oversize truncated, full copy in transcript
        principal, processed_at,    -- NULL on user.* rows = still queued
        created_at)
        -- seq assigned under SELECT … FOR UPDATE on the session row

tool_confirmations (id, session_id,
        call_hash, UNIQUE (session_id, call_hash),  -- sha256(tool_name + canonical_json(input))
        tool_use_id,                                -- audit only; not stable across resume
        tool_name, input jsonb,
        status CHECK IN ('pending','allowed','denied','expired'),
        requested_at, expires_at, decided_by, decided_at)

deployments (id, agent_id, agent_version,   -- NULL = latest at fire time
        name, cron, timezone DEFAULT 'Asia/Tokyo', initial_events jsonb,
        budget_usd, paused DEFAULT false, archived_at, scheduler_job_name,
        created_by, created_at)

deployment_runs (id, deployment_id, session_id,
        status CHECK IN ('queued','running','succeeded','failed'),
        error_type,    -- session_error|budget_reached|timeout|retries_exhausted|infra_error
        fired_at, finished_at)

vaults (id, name, archived_at, created_at)
vault_credentials (id, vault_id, name,
        type,          -- 'env' | 'header'
        secret_ref,    -- Secret Manager resource name; value NEVER in Postgres
        target jsonb,  -- e.g. {"mcp_server": "github"} or {"host": "api.example.com"}
        created_at)

memory_stores (id, name, created_at)
memories (id, store_id, path, UNIQUE (store_id, path),
        content,       -- ≤64KB; Postgres is source of truth
        updated_by,    -- principal or 'agent:{session_id}'
        created_at, updated_at)
        -- mounted as ws/memory/{store}/…, written back at checkpoint
        -- versioning deferred (documented)

skills (id, name UNIQUE,      -- ^[a-z0-9][a-z0-9-]{0,63}$, the mount directory name
        description, archived_at, created_by, created_at, updated_at)
skill_files (id, skill_id, path, UNIQUE (skill_id, path),
        content,              -- ≤64KB per file
        updated_by, created_at, updated_at)
        -- agent_versions.skill_ids / sessions.skill_ids reference these
```

### Skills (org-shared agent capabilities)

A skill is the Agent Skills format — a folder with a `SKILL.md` entry file plus
supporting files — stored org-wide in Postgres, shared by all environments the
same way memory stores and vaults are. `agent_versions.skill_ids` attaches
skills to an agent; sessions copy the list at creation (overridable per
session, like vaults and memory).

- **Mount**: the sandbox materialises the session's skills under
  `ws/.claude/skills/{name}/` on every wake (internal `GET
  /sessions/{id}/skills`) and enables `setting_sources=["project"]` so the SDK
  discovers them; the `Skill` tool is appended to the allowlist when the agent
  restricts tools. A skill is mounted only if it is unarchived and has a
  `SKILL.md`.
- **Read-only from the sandbox** — deliberate deviation from memory: there is
  no skill writeback path, and the mount tree is rebuilt from Postgres on every
  wake, so a prompt-injected agent cannot poison a skill shared by every other
  agent. Skills change only through the API, by a human principal.
- **Governance unchanged**: `Skill` invocations pass through the same
  `PreToolUse` gate as every other tool call — the permission policy and kill
  switch apply, and the call lands in `audit.tool_calls`.
- **Not versioned** (like memory, documented): editing a skill changes it for
  every agent that references it, including pinned agent versions.

### Storage split

- **Postgres** — all queryable control-plane state (above).
- **GCS** (`naxos2-sess-{env}`) — `sessions/{id}/transcript.jsonl` (SDK session file) and `sessions/{id}/ws/**` (workspace).
- **BigQuery `audit`** — append-only governance record:

```
audit.runs(run_id, session_id, agent_id, environment_id, deployment_run_id,
           trigger_type,        -- interactive | deployment | api
           principal, started_at, ended_at, status, stop_reason,
           num_turns, input_tokens, output_tokens, cost_usd, approx_cost_jpy, model)
           -- a "run" = one wake-to-idle processing burst of a session

audit.tool_calls(run_id, session_id, agent_id, principal, ts, tool_use_id,
           tool_name, args_redacted,
           decision,            -- auto_allowed | user_allowed | user_denied | killed
           result_status, latency_ms, error)
```

## 7. API surface (v1)

All under IAP; the principal is the verified IAP JWT email. Programmatic clients authenticate as IAP-authorized service accounts (ID token). Deliberate deviation: no API keys.

```
POST   /v1/agents                          create (version 1)
GET    /v1/agents · GET /v1/agents/{id}    list / get (?version=)
POST   /v1/agents/{id}/versions            new immutable version (CMA "update = new version")
POST   /v1/agents/{id}/archive
PATCH  /v1/agents/{id}                     {disabled: bool}    -- kill switch (explicit deviation)

POST   /v1/environments                    register; 409 until Terraform-provisioned (deviation)
GET    /v1/environments[/{id}] · POST /v1/environments/{id}/archive

POST   /v1/sessions                        {agent: {id, version?} | agent_with_overrides,
                                            initial_events?, budget?, vault_ids?,
                                            memory_store_ids?, resources?}
GET    /v1/sessions[?agent_id&status] · GET /v1/sessions/{id}
PATCH  /v1/sessions/{id}                   raise budget
POST   /v1/sessions/{id}/terminate
POST   /v1/sessions/{id}/events            user.message | user.interrupt |
                                           user.tool_confirmation | user.custom_tool_result
GET    /v1/sessions/{id}/events?after={seq}&limit=      list (cursor = seq)
GET    /v1/sessions/{id}/events?stream=sse&after={seq}  SSE (Last-Event-ID honored)

POST   /v1/deployments · GET /v1/deployments[/{id}]
POST   /v1/deployments/{id}/pause | /unpause | /archive | /run
GET    /v1/deployments/{id}/runs

POST   /v1/vaults · GET /v1/vaults[/{id}] · POST /v1/vaults/{id}/archive
POST   /v1/vaults/{id}/credentials         write-only; value → Secret Manager directly
GET    /v1/vaults/{id}/credentials         metadata only
DELETE /v1/vaults/{id}/credentials/{cid}

POST   /v1/memory_stores · GET /v1/memory_stores[/{id}]
POST   /v1/memory_stores/{id}/memories · GET (list) · GET/PUT/DELETE …/memories/{mid}

POST   /v1/skills · GET /v1/skills[/{id}] · POST /v1/skills/{id}/archive
POST   /v1/skills/{id}/files               upsert by path
GET    /v1/skills/{id}/files · GET/DELETE …/files/{fid}
```

Internal surface (`naxos-internal`, IAM-only): per-session `claim / heartbeat / queue?wait / events / checkpoint / config / skills / memory_writeback`, plus `deployments/{id}/fire` and `reconcile`.

Event types (CMA vocabulary): `user.message`, `user.interrupt`, `user.tool_confirmation`, `user.custom_tool_result`, `agent.message`, `agent.thinking`, `agent.tool_use`, `agent.tool_result`, `session.status_running`, `session.status_idle`, `session.status_terminated`, `session.error`, `span.model_request_start`, `span.model_request_end`.

Documented deviations from CMA: IAP auth instead of API keys; environments operator-provisioned; budget enforced post-response rather than pre-request; `span.*` approximated from the SDK stream; no outcomes / multiagent / webhooks in v1.

## 8. Security model

- **Per-environment SA is the isolation boundary.** `sa-env-{env}` gets `aiplatform.user` (the only model exit), objectAdmin on its own session bucket, and `run.invoker` on `naxos-internal` + `naxos-egress` — nothing else. No BigQuery, no secrets, no other environment's anything. A fully prompt-injected agent is still boxed by IAM.
- **Sandbox ↔ control-plane auth**: OIDC ID token of the env SA → Cloud Run IAM on `naxos-internal`, then an app-level check that the token's SA equals `environments.service_account_email` for the session being touched. No bearer tokens to mint or leak.
- **Tool restriction**: tools not in the agent version's `tools` list are never passed to the SDK. Args are schema-validated in guarded wrapper code with caps enforced in code; errors return as tool results.
- **Kill switch, three levels**: `agents.disabled` (checked at event accept and inside `can_use_tool` before every tool call — the v1 gap fixed); session terminate; environment pause. Disabling an agent also pauses its deployments' Scheduler jobs.
- **Audit**: all agent events flow through `naxos-internal`, so the control plane is the single audit writer — the sandbox cannot forge or skip audit rows. `principal` is the IAP email for user-triggered turns, `deployment:{id}` for cron.
- **IAP** directly on Cloud Run with the custom OAuth client (no-org project); the app verifies the IAP JWT.

## 9. Cost model (¥100k cap / ¥70k target)

| Item | Sizing | ¥/month |
|---|---|---|
| Cloud SQL Postgres | db-g1-small, 10GB SSD, no HA, private IP | ~5,500 |
| naxos-api / internal / egress | all min=0 | ~1,000–2,000 |
| Sandbox executions | 1 vCPU / 2GiB ≈ ¥15 per active-hour; generous 150h/mo | ~2,500 |
| Scheduler / GCS / BQ streaming / Secret Manager / logs | | ~1,000 |
| **Infra total** | | **≈ ¥10k** |
| Model (Vertex) headroom | | ~¥60–90k |

Runaway protection: a control-plane global cap on concurrent sandbox executions (default 5). The idle-linger window (~120s) is the main sandbox cost knob. If cold-resume chat UX hurts, `naxos-api` min=1 adds ~¥4k — still inside target.

## 10. Repo layout

```
control-plane/     Python 3.13 + uv + FastAPI; entrypoints: api | internal; plain-SQL
                   migrations (sorted migrations/*.sql, applied at boot, schema_migrations
                   table). DB access is raw SQL over asyncpg — no ORM: the load-bearing
                   queries (seq assignment via UPDATE…RETURNING, SKIP LOCKED claims, lease
                   CAS, LISTEN/NOTIFY) don't fit one, and CRUD is the thin minority.
sandbox-runner/    Python + claude-agent-sdk: main, harness, permissions, budget,
                   workspace, control_channel, memory_sync
egress-proxy/      credential-substituting proxy (FastAPI/httpx)
shared/            pydantic event/config models used by all three
ui/                Next.js static export, baked into the control-plane image
terraform/         one root module; environments.json → for_each (SA, Job, bucket, IAM)
docs/              design docs (English)
.github/workflows/ lint/test + WIF deploy loop
```

## 11. Build order

Each phase ends deployed and demoable.

0. **This document**; old code moved to the `poc` branch.
1. **Spike (blocking)**: Agent SDK resume semantics — pending tool_use replay through `can_use_tool`, transcript restore from a relocated directory, Vertex backend availability in asia-northeast1/global (resolves the model-region open issue).
2. **Walking skeleton**: Terraform base (SQL, buckets, BQ, SAs, services, one `default` environment Job) + agents CRUD (versioned) + sessions + events + the full loop: create session → job launch → SDK turn on Vertex → events → SSE → idle checkpoint → resume. Audit (`runs` + `tool_calls`) and kill switch from day one. Reconciler.
3. **Permissions + budget + interrupt**: `always_ask` round-trip including pause/release/resume, budget enforcement, minimal UI (session timeline + approval inbox).
4. **Deployments**: Scheduler-per-deployment, `deployment_runs` with error types, pause/unpause/run-now, UI tab.
5. **Vaults + egress proxy**: Secret Manager write path, MCP URL rewriting, header substitution, per-vault IAM.
6. **Memory stores**: CRUD + mount/writeback + UI.
7. **Hardening**: second environment (proves the fan-out), budget alerts, cost review at the phase gate.

## 12. Verification

- Phase 1 spike has an explicit pass/fail: resume replays the pending tool_use, or the fallback is adopted and documented here.
- Every phase ships to the project and runs its end-to-end demo (Phase 2: curl create agent → session → SSE shows `agent.message` → `audit.runs` row exists → flip `disabled` → event rejected).
- Approval flow: `always_ask` tool → container exits during the pause (verify zero cost while pending) → confirm via UI → session resumes and completes.
- Deployments: cron fires → `deployment_runs` row → session completes; error path exercised by archiving the agent.
- Cost gate at Phase 7: one month of billing export reviewed against the ¥10k infra estimate.

## 13. GCP verification (2026-08-11)

The end-to-end system was deployed to the project and verified live:

- **Plain session**: create agent → session with `initial_events` → sandbox Job execution boots → SDK turn (Anthropic API) → `agent.message` → `session.status_idle(end_turn)` → cost and `sdk_session_id` checkpointed. Model reply round-trip ≈ 40s including cold start.
- **Approval cycle**: `always_ask` agent paused at `agent.tool_use(awaiting_confirmation)` → `session.status_idle(requires_action)` → **the sandbox execution exited while waiting** (zero cost while pending) → `user.tool_confirmation(allow)` → fresh execution resumed the SDK session → the model re-issued the same call under a new `tool_use_id` → the stored decision (keyed by call hash) allowed it → command ran and completed. This validates the load-bearing design decision end to end.
- **Audit**: `naxos_audit.runs` (one row per wake-to-idle burst) and `naxos_audit.tool_calls` (per-call decisions) populated by the control plane.
- **Deviation found on GCP**: exceptions raised inside a `PreToolUse` hook are swallowed by the SDK (the CLI falls back to its own permission system). The pause is therefore implemented as deny + interrupt, with `paused_call` state driving `requires_action` — §4 updated accordingly by the code.

Known issues (non-blocking):
- Per-run `cost_usd` delta can go negative on resume bursts — the SDK cost counter resets per burst while the session accumulates; per-burst baseline accounting needed.
- A call approved by a human is audit-labeled `auto_allowed`; should be `user_allowed`.
- Cost parking: `naxos-state` Cloud SQL is stopped (`activation-policy NEVER`) and the reconcile scheduler paused when the platform is idle; restart with `gcloud sql instances patch naxos-state --activation-policy ALWAYS` + `gcloud scheduler jobs resume naxos-reconcile`.

## 14. Risks and open items

- **R1 — resolved.** Resume does replay the pending tool call; see §4.1. The design stands, with confirmations keyed on the call hash and the gate implemented as a `PreToolUse` hook.
- **R2 — open, and it now has a hard finding.** Claude models are **not available in `asia-northeast1`** on Vertex: `getPublisherModel` returns "not found" for `claude-sonnet-5` / `claude-opus-5` in both `asia-northeast1` and `us-east5`, and resolves only on the **`global`** endpoint. A live `rawPredict` against `global` then returned **429 `RESOURCE_EXHAUSTED`** — the project's `global_online_prediction_requests_per_base_model` quota for `anthropic-claude-sonnet` is zero, so a quota-increase request is required before Vertex can serve any traffic.

  Two decisions this forces, both for the owner (per the hard constraint against silently deciding region questions):
  1. **Data residency**: using Vertex at all means using the `global` endpoint — inference is not pinned to `asia-northeast1`. Accept, or keep model traffic off Vertex until a regional endpoint exists.
  2. **Unblocking**: file the Vertex quota-increase request now, since it gates the removal of the Anthropic API-key exception. Until then the key exception stays, and no real internal data may be connected.
- Egress proxy covers MCP + declared HTTP targets only; arbitrary bash egress is credential-less (documented limitation).
- Memory versioning, outcomes, multiagent, webhooks: deferred; schema leaves room.
