# naxos — Claude Managed Agents on Google Cloud

Design document. All project documentation is written in English.

## 1. Context and goal

naxos is a **faithful Google Cloud implementation of Claude Managed Agents (CMA)** — the same object model, REST surface, and event vocabulary as `platform.claude.com/docs/en/managed-agents`, running entirely inside your own GCP boundary. The platform's reason to exist (see CLAUDE.md): the data boundary (Vertex-only model exit), internal-system integration inside that boundary, and execution-level governance that a hosted platform cannot provide.

Fixed design decisions:

- **Faithful API clone** of the CMA REST surface and `{domain}.{action}` event types.
- **Container per session**: each session's agent loop + tools run in an isolated container.
- **Initial scope**: agents / environments / sessions / events core, scheduled deployments, permission policies (`always_ask` approval), vaults **with a real egress proxy** (full CMA credential fidelity — credentials never enter the sandbox), memory stores. Outcomes, multiagent, and webhooks are deferred.
- **Cold resume accepted**: idle sessions release their container; the next event relaunches it (CMA's `rescheduling` status). UI shows a "waking up" state.
- **Environments are Terraform-provisioned**: the API only registers rows; the per-environment SA / sandbox Job / bucket come from a `for_each` over `terraform/environments.json`. The control plane never holds IAM-admin.

## 2. Core design stance

**Sessions are durable state, not processes.** A session lives in Postgres (metadata, event log) and GCS (SDK transcript, workspace). A container — one Cloud Run Job execution — exists only while the session is actively processing events. When the queue drains, the sandbox checkpoints to GCS and exits; the next incoming event relaunches it. This is the only shape that satisfies both CMA-style per-session isolation and scale-to-zero within the cost envelope of §9.

## 3. Components

| Component | GCP resource | Scaling | SA |
|---|---|---|---|
| `naxos-api` — /v1 REST + SSE, serves the UI static export | Cloud Run Service, IAP directly on Cloud Run (no LB) | min=0, request timeout 3600s (SSE) | `sa-api` |
| `naxos-internal` — sandbox↔control-plane channel, scheduler targets, reconciler (same image as api, entrypoint flag) | Cloud Run Service, internal ingress, IAM auth | min=0 | `sa-api` |
| `naxos-egress` — credential-substituting proxy for MCP/HTTP egress | Cloud Run Service, internal ingress, IAM auth | min=0 | `sa-egress` (sole secretAccessor on vault secrets) |
| `naxos-mcp-{name}` — self-hosted connector: an unmodified upstream MCP server (Slack, Atlassian, Notion, Google Workspace) | Cloud Run Service, IAM auth, one per `connectors.json` entry | min=0, max=1 | `sa-mcp-{name}` (sole accessor of its own credential secrets) |
| `naxos-sbx-{env}` — session sandbox: Claude Agent SDK loop + tools; one Job per environment, one execution per session wake | Cloud Run Job (`max_retries=0`, task timeout 3600s, self-checkpoint at ~55 min) | zero when idle | `sa-env-{env}` |
| Deployment cron `naxos-deploy-{id}`; reconciler tick (1/min) | Cloud Scheduler | — | `sa-scheduler` |
| State | Cloud SQL Postgres `db-g1-small`, private IP, Direct VPC egress from Cloud Run | always-on (the one fixed cost) | — |
| Workspaces | GCS bucket per environment `naxos2-sess-{env}` | — | env SA + `sa-api` only |
| Audit | BigQuery `audit.runs` + `audit.tool_calls`, written **only by the control plane** | — | `sa-api` |
| UI — chat-first, macOS-style (sidebar of conversations + Messages-like live chat with inline approvals) with a console area: all sessions, agents, deployments, artifacts, monitoring, vaults, memory, skills, kill switch | Next.js static export baked into the api image | — | — |

The **environment is the tenant/isolation boundary** (as in CMA). Agents are cheap DB rows; environments carry the service account, the sandbox Job, and the session bucket, fanned out by Terraform from `environments.json`. Many agents can share an environment.

## 4. Session lifecycle and event flow

Statuses: `idle | running | rescheduling | terminated`, with `stop_reason` on idle: `end_turn | requires_action | budget_reached | retries_exhausted`.

```
create ──▶ [idle]  (no container; workspace created lazily in GCS)
   │   user event → INSERT session_events (per-session seq; processed_at NULL = queued)
   ▼   no live lease? jobs.run(naxos-sbx-{env}, args=["--session", id])
[rescheduling]  wake() emits session.status_rescheduling → UI "waking up" state
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

**Client-facing streaming.** `GET /v1/sessions/{id}/events?stream=sse&after={seq}` — the API replays from the cursor, then tails `session_events` on Postgres LISTEN/NOTIFY, emitting SSE frames with `id:{seq}`, honoring `Last-Event-ID` for reconnect replay, with a ping comment every 15s of quiet. Because the stream is DB-backed, replay survives instance loss, so no Cloud Run session affinity is needed anywhere.

**Token-level partial text.** The append-only event log cannot hold a row per token, so live typing rides *transient* `agent.message_delta` frames instead: the sandbox batches the SDK's partial-message text deltas (~0.3s cadence, ≤1000 chars/frame) to `POST /internal/sessions/{id}/stream`, which fans them out to SSE listeners over a second LISTEN/NOTIFY channel — nothing is stored. Delta frames carry no SSE `id:` line, so `Last-Event-ID` replay semantics are untouched; each frame carries a `stream` id that the persisted `agent.message` repeats, letting clients drop late deltas instead of duplicating text. Audit is unaffected: the persisted message remains the only durable record, and a lost delta loses nothing.

**Interrupt.** `user.interrupt` is inserted as an event and surfaced immediately on the control channel; the sandbox calls `client.interrupt()`. Jumps the queue, per CMA.

**Permission pause (`always_ask`).** The gate is a **`PreToolUse` hook**, not `can_use_tool` — see §4.1 for why. On every tool call:

1. Check the kill switch (`agents.disabled`; 15s cache + control-channel push).
2. Match the tool against the agent version's permission policy — first matching rule wins; rules match exact tool names or fnmatch-style globs (e.g. `mcp__artifacts__*`). `always_allow` → allow.
3. `always_ask` → look up `tool_confirmations` by `(session_id, call_hash)` where `call_hash = sha256(tool_name + canonical_json(input))`. Stored decision → return it. **The key is the call hash, not `tool_use_id`** — the SDK assigns a fresh `tool_use_id` when a pending call is replayed after resume (measured; §4.1).
4. No decision → insert a pending row, emit `session.status_idle(stop_reason=requires_action)`, long-poll for the decision within the linger window. If it arrives, answer in-process (warm path). If not, **checkpoint and exit** — a blocked container costs nothing.
5. A later `user.tool_confirmation` stores the decision and relaunches the sandbox. The sandbox resumes with a synthetic continuation prompt; the model re-issues the same tool call, the hook fires again, and step 3 answers instantly.
6. **Nothing answers.** The pending row carries `expires_at = now() + CONFIRMATION_TTL_HOURS` (default 24h; 0 disables it). The reconciler marks anything past it `expired`, queues the same `user.tool_confirmation(deny)` event a human decision would, and wakes the session — so the replayed call is refused with a reason that tells the agent to stop rather than retry, and the run ends instead of parking forever. Terminated sessions are skipped: nothing would consume the resume event.

Every branch above — including the pause and both denials — commits a `tool_calls` row in the same transaction before answering (§8). A paused call and the approved call that replaces it are two rows chained by `call_hash`, which is what makes the pause, the approver and the eventual execution all readable as one story. `expired` is a decision of its own, distinct from `user_denied`: "nobody answered" and "a human said no" mean different things to whoever reads the record later.

**Being paused is not the same as being seen.** A gate that only works when someone happens to have the right session open is not a control. Three things close that: `GET /v1/tool_confirmations` is a cross-session queue (the UI's Approvals view, with a pending count badged on every page); the gate emits a `naxos.approval_required` log line that a Terraform-provisioned log-based metric and alert policy turn into mail (§8); and expiry above bounds how long a forgotten approval can hold a session. Deciding stays `POST /v1/sessions/{id}/events` — the inbox hands back the `session_id` and `call_hash` that call needs, rather than adding a second decision path that could drift from the audit write and the wake.

### 4.1 Measured SDK resume semantics (`claude-agent-sdk` 0.2.134)

Measured findings, all load-bearing:

- **Resume replays the pending tool call — R1 is resolved.** Killing the process inside the permission callback (mid-decision) and then resuming with `resume=<session_id>` plus a continuation prompt made the model re-issue the *same tool with the same input*, and the callback fired again. The pause/release/resume design works as specified.
- **`tool_use_id` is not stable across resume.** The replayed call carried a new `toolu_…` id. Confirmation records must therefore be keyed on a canonical hash of `(tool_name, input)`, not on `tool_use_id`. (`tool_use_id` is still recorded for audit.)
- **`can_use_tool` is not a complete gate; use a `PreToolUse` hook.** Two shadowing behaviors: any whole-tool entry in `allowed_tools` auto-approves before the callback runs (the SDK warns about this explicitly), and under the default permission mode the CLI auto-approves calls it judges read-only — a read-only `echo` never reached the callback while a file-writing `bash` did. A permission policy that claims "every `always_ask` call is gated" cannot be built on `can_use_tool`; the `PreToolUse` hook fires for every call and is the correct mechanism.
- **Resume needs a nudge.** A resumed session does not continue on its own; the sandbox sends a synthetic continuation user message after restoring. This is internal and is not persisted as a `user.message` event.

**Budget.** The harness checks `cost_usd + accrued >= budget_usd` before dispatching each queued user event and after each model response; on breach it interrupts, emits `session.status_idle(stop_reason=budget_reached)`, checkpoints, exits. Deviation from CMA: this is a post-response check (the SDK has no pre-request hook), so worst-case overshoot is one model call. `PATCH /v1/sessions/{id}` raises the budget; the next event resumes the session.

**Crash safety.** The sandbox heartbeats its lease every 30s. A 1-minute reconciler relaunches sessions that have queued events and an expired lease, up to `MAX_RETRIES=3` per batch, then emits `session.error` + `session.status_idle(stop_reason=retries_exhausted)`. The Job never self-retries (`max_retries=0`); the sandbox self-checkpoints at ~55 min and exits cleanly so a fresh execution can continue.

## 5. Vault egress proxy (full CMA fidelity)

Credentials never enter the sandbox:

- `POST /v1/vaults/{id}/credentials` writes the secret value straight to Secret Manager. Postgres stores metadata and the secret ref only; values are write-only and never returned by the API.
- Agent config declares MCP servers by their real URL. The sandbox's resolved config rewrites MCP URLs to `naxos-egress` with an opaque route token. The proxy authenticates the caller (OIDC; the SA must match the session's environment), resolves session → vault_ids → credential matched by target URL, injects the Authorization/header, and forwards to the real server. Route tokens are re-minted on every wake and deleted when the session terminates.
- Only `sa-egress` holds `secretAccessor` on vault secrets. Environment SAs hold none.
- Transparent interception of arbitrary egress is out of scope — bash traffic not routed through the proxy simply has no credential. This limitation is documented, not hidden. Credentials therefore have one shape, `header`, applied to MCP traffic for a named server; an env-var placeholder form was specified but never had a substitution path, and is not accepted.
- The proxy streams both directions (`httpx` streamed send → `StreamingResponse`) so MCP streamable-HTTP and SSE transports work; a buffering proxy stalls them.

### 5.1 Connectors

naxos ships no connector code. A connector is an existing MCP server, attached to an agent version's `mcp_servers`, reached one of two ways:

| Shape | Path | Credential |
|---|---|---|
| `remote` | vendor-hosted MCP endpoint → `naxos-egress` rewrites the URL and injects the header | vault `header` credential targeting `{"mcp_server": name}` |
| `hosted` | upstream OSS MCP server deployed as a scale-to-zero Cloud Run service in this project (`naxos-mcp-{name}`) | Secret Manager env refs on that service, readable only by its own SA |

`hosted` connectors keep third-party server code and its credentials out of the sandbox entirely, and are how closed-network/internal systems are reached without egress to a vendor. Terraform owns the service shells and IAM (`terraform/connectors.json` → `for_each`, mirroring `environments.json`); `scripts/mirror_connectors.sh` mirrors the upstream images into Artifact Registry (Cloud Run cannot pull from Docker Hub/ghcr directly) and rolls the services.

**Access is a per-environment opt-in**, declared as a `connectors` list in `environments.json` exactly like `bigquery_datasets` — never implied by an agent naming the server in `mcp_servers`. `run.invoker` on a connector is *network reach, not a tool grant*: the hosted servers disable their own request auth because Cloud Run IAM is the gate, and a sandbox can mint an ID token for any audience from the metadata server, so an environment holding the binding can reach that connector from bash — outside the permission policy, the approval gate and `audit.tool_calls`. Granting it is therefore a statement that the tenant may use that system with its shared credential at all, and it stays Terraform-provisioned and reviewable rather than API-granted.

The sandbox authenticates to both shapes through an in-process localhost forwarder (`naxos_sbx.mcp_gateway`): the SDK's MCP client cannot mint Google OIDC ID tokens, and a token attached once at wake would expire mid-burst, so the forwarder mints per request (audience = the Cloud Run service) and streams both directions. Any `*.run.app` MCP URL is routed through it; other URLs pass through untouched.

`mcp_servers` entries are validated at agent-version create: `{type: http|sse, url, headers?}` only. stdio (`command`) configs are rejected — they would execute inside the sandbox with no egress rewriting and no credential model — as are the reserved names `artifacts` and `schedules`.

Governance is unchanged by either shape: connector tools are ordinary `mcp__{server}__{tool}` calls, so permission globs, the `PreToolUse` approval gate, the kill switch and `audit.tool_calls` all apply. `GET /v1/connectors` serves the curated catalog (a `hosted` entry without a deployed service URL is listed unavailable).

Deferred: OAuth authorization-code flows, refresh tokens, and per-end-user identity. Every connector runs as a single service identity, so SaaS-side audit attributes actions to that identity, not to the human who triggered the session — naxos's own audit still records the human principal. This rules out the OAuth-only hosted endpoints (Atlassian's and Notion's) in favour of self-hosting those servers.

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
        default_budget_usd numeric, max_turns int,
        effort text CHECK IN ('low','medium','high','xhigh','max'),
        created_by, created_at)

sessions (id, agent_id, agent_version, overrides jsonb, environment_id,
        status CHECK IN ('idle','running','rescheduling','terminated'),
        stop_reason, budget_usd, cost_usd DEFAULT 0,
        vault_ids text[], memory_store_ids text[], resources jsonb,
        sdk_session_id, lease_id uuid, lease_expires_at, execution_name,
        turn_principal,    -- actor of the turn being processed, latched at queue claim
        current_run_id,    -- the sandbox's run id, taken at claim; execution_name is a
                           -- full Cloud Run resource path and does not match it
        retry_count DEFAULT 0, last_event_seq DEFAULT 0,
        created_by, created_at, updated_at, terminated_at)

session_events (id bigserial, session_id, seq, UNIQUE (session_id, seq),
        type, payload jsonb,        -- ≤64KB; oversize truncated, full copy in transcript
        principal, processed_at,    -- NULL on user.* rows = still queued
        created_at)
        -- seq assigned under SELECT … FOR UPDATE on the session row

tool_calls (id bigserial, session_id, run_id, agent_id, agent_version, environment_id,
        principal,      -- the turn's actor, or 'deployment:{id}'
        approved_by,    -- the human who answered a gated call
        tool_name, call_hash, tool_use_id,
        args_json,      -- the exact canonical bytes call_hash covers, capped
        args_truncated,
        decision CHECK IN ('auto_allowed','user_allowed','user_denied','not_allowed',
                           'killed','awaiting_confirmation'),
        result_status CHECK IN ('ok','error','denied','no_result'),
        latency_ms, error,
        decided_at, resulted_at, exported_at)
        -- the execution record; written at the permission gate, see §8
        -- no FK: like session_runs it must outlive the session it describes

tool_confirmations (id, session_id,
        call_hash, UNIQUE (session_id, call_hash),  -- sha256(tool_name + canonical_json(input))
        tool_use_id,                                -- audit only; not stable across resume
        tool_name, input jsonb,
        status CHECK IN ('pending','allowed','denied','expired'),
        requested_at, decided_by, decided_at,
        expires_at)   -- CONFIRMATION_TTL_HOURS at insert; the reconciler sweeps it

deployments (id, agent_id, agent_version,   -- NULL = latest at fire time
        name, cron, timezone DEFAULT 'Asia/Tokyo', initial_events jsonb,
        budget_usd, paused DEFAULT false, archived_at, scheduler_job_name,
        created_by, created_at)

deployment_runs (id, deployment_id, session_id,
        status CHECK IN ('queued','running','succeeded','failed','cancelled'),
        error_type,    -- session_error|budget_reached|timeout|retries_exhausted|infra_error
        error_type,    -- + session_error|agent_disabled|cancelled for a closed run
        stop_reason, cost_usd, num_turns,
        fired_at, started_at, finished_at)
        -- Closed at the fired session's checkpoint. A run blocked on an operator
        -- (requires_action, budget_reached — raising the budget resumes) stays
        -- open and keeps accumulating; anything else ends it. Terminal failures
        -- come from the control plane, which is where they are known: a crashed
        -- burst still reports end_turn (the sandbox flags it with `errored`),
        -- wake retries exhausted never reaches a checkpoint at all, and the kill
        -- switch / terminate / delete cancel the run.

session_runs (id, session_id, agent_id, environment_id,
        trigger_type, principal, model, status, stop_reason,
        num_turns, cost_usd,    -- the burst's cost DELTA, not the session total
        started_at, ended_at)
        -- one row per wake-to-idle burst, written at checkpoint alongside the
        -- BigQuery audit.runs row; feeds the monitoring view without granting
        -- the API BigQuery read access

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

artifacts (id, session_id, agent_id, environment_id,
        name, UNIQUE (session_id, name),
        description, content_type, size_bytes,
        version,       -- re-publishing a name bumps it (content overwritten)
        share_token UNIQUE, shared_at, shared_by,   -- NULL = not shared
        created_by,    -- 'agent:{session_id}'
        created_at, updated_at)
        -- content in GCS at sessions/{session_id}/artifacts/{name}

skills (id, name UNIQUE,      -- ^[a-z0-9][a-z0-9-]{0,63}$, the mount directory name
        description, tags text[],   -- free-form labels, list-view filtering only
        archived_at, created_by, created_at, updated_at)
skill_files (id, skill_id, path, UNIQUE (skill_id, path),
        content,              -- ≤64KB per file
        updated_by, created_at, updated_at)
        -- agent_versions.skill_ids / sessions.skill_ids reference these

favorites (id, principal, entity_type CHECK IN ('agent','session','artifact','skill'),
        entity_id, UNIQUE (principal, entity_type, entity_id), created_at)
        -- no FK: entity_type spans tables. Session/artifact hard-delete handlers
        -- clear matching rows; archived agents/skills just drop out of list views
```

### Artifacts

Artifacts are files an agent deliberately publishes as durable outputs — distinct from
the workspace (an implementation detail that gets overwritten) and from memory (agent-
private state). The mechanism:

- The sandbox exposes an **in-process MCP server** (`artifacts`) with five tools:
  `artifact_create` (publish a workspace file; same name = new version),
  `artifact_list`, `artifact_delete`, `artifact_share`, `artifact_unshare`. Because
  they are ordinary tool calls, they pass the `PreToolUse` permission gate — audited
  in `audit.tool_calls`, subject to the agent's permission policy and the kill switch.
- Content goes **directly from the sandbox to the environment's session bucket**
  (the env SA already owns it); only metadata flows through `naxos-internal`, which
  records the row, bumps the version, and appends an `agent.artifact` event
  (`created | updated | deleted | shared | unshared`) to the session timeline.
- **Sharing mints a stable token URL** (`/v1/artifacts/shared/{token}`) that resolves
  independently of session or artifact ids — but it is served by `naxos-api` behind
  IAP, so a "shared" artifact is reachable by anyone in the org and by no one outside
  it. Content never leaves the data boundary; there are no public links by design.
- Size cap `MAX_ARTIFACT_BYTES` (default 10MB), enforced in the sandbox and at
  registration. Humans manage artifacts (download, describe, share, revoke, delete)
  from the UI's Artifacts page or the `/v1/artifacts` API.

### BigQuery

The sandbox image carries no `bq` or `gcloud` CLI, so BigQuery is reached through a
built-in tool rather than a shell command. The mechanism mirrors artifacts:

- The sandbox exposes an **in-process MCP server** (`bigquery`) with four tools:
  `bigquery_list_datasets`, `bigquery_list_tables`, `bigquery_describe_table`, and
  `bigquery_query`. Ordinary tool calls — they pass the `PreToolUse` permission gate,
  land in `audit.tool_calls` with the SQL as the recorded input, and obey the kill
  switch. Requests go straight from the sandbox to `bigquery.googleapis.com` as the
  environment SA, which is what bounds them.
- **The server is registered only when the environment was opted in.** Terraform
  passes the environment's `bigquery_datasets` list to the sandbox job as
  `BIGQUERY_DATASETS`; when it is empty the server is not built, so an environment
  without the grant has no BigQuery tools at all rather than tools that fail. That is
  the same list that drives the IAM grants, so the tool surface and the IAM boundary
  cannot drift apart.
- **The audit dataset is read through authorized views, never through a grant.**
  `naxos_audit` holds every tenant's rows and is never grantable to an environment SA.
  Terraform republishes `runs` and `tool_calls` as authorized views in a separate
  dataset, `naxos_audit_shared`, and grants those views access on the source: an
  environment that lists `naxos_audit_shared` reads the platform's own history while
  holding no permission on `naxos_audit` itself. Writes remain control-plane-only, so
  the single-writer property is unaffected. The `tool_calls` view selects explicit
  columns and omits `args_json` and `error` — the full tool arguments and tool output of
  every tenant — while keeping `call_hash` so an agent can still correlate calls without
  reading their contents. The views select every *row*, so a second environment still
  needs a tenant filter in the view query before this dataset is opted into more than once.
- **Guardrails are enforced in code, not prompt**: only a single read-only
  `SELECT`/`WITH` runs (DML, DDL, and multi-statement scripts are refused before the
  API call); every query is dry-run first and refused if it would scan more than
  `MAX_QUERY_BYTES_BILLED` (default 1GiB), which is also sent as `maximumBytesBilled`
  so the cap holds even if the estimate is wrong; results are capped at
  `MAX_QUERY_ROWS` (default 200) and truncated past 100k characters.
- The job location is resolved once from the readable datasets and cached, because a
  job against a regional dataset otherwise fails as "not found in location US" — and
  the location has to be known before the dry run, so it cannot be read off the query.
- A denial is reported to the agent as terminal (which datasets it can read, and to
  stop rather than retry), since no amount of retrying widens a Terraform-provisioned
  grant.

### Agent-created deployments (scheduling from inside a session)

When a user asks an agent to "do this every morning", the durable answer is a
deployment, not a timer in the sandbox. The mechanism mirrors artifacts:

- The sandbox exposes an **in-process MCP server** (`schedules`) with three tools:
  `schedule_create` (name, cron, standalone prompt, optional timezone/budget),
  `schedule_list`, `schedule_delete`. Ordinary tool calls — they pass the
  `PreToolUse` permission gate, land in `audit.tool_calls`, and obey the kill
  switch. `naxos-internal` creates the deployment for the session's agent,
  unpinned (`agent_version` NULL = latest at fire time), attributed
  `created_by = agent:{session_id}` so operators can tell agent-scheduled work
  from their own; it appears on the Deployments page like any other deployment.
- **Scope**: agents list every unarchived deployment of their agent (so they can
  answer "what's scheduled for you"), but can archive only agent-created ones —
  operator-created deployments are read-only from the sandbox. Runaway
  protection: `MAX_AGENT_DEPLOYMENTS` (default 20) unarchived agent-created
  deployments per agent.
- **The CLI's session-local scheduling tools are disallowed** (`CronCreate`,
  `CronDelete`, `CronList`, `ScheduleWakeup` via `disallowed_tools`): they
  schedule inside the container's memory, and a sandbox execution exists only
  while the session is actively processing — anything they schedule dies unfired
  at the next idle checkpoint, invisible to audit, pause, and the kill switch.

### Skills (org-shared agent capabilities)

A skill is the Agent Skills format — a folder with a `SKILL.md` entry file plus
supporting files — stored org-wide in Postgres, shared by all environments the
same way memory stores and vaults are. `agent_versions.skill_ids` attaches
skills to an agent; sessions copy the list at creation (overridable per
session, like vaults and memory).

- **Mount**: the sandbox materialises the session's skills as a **local
  plugin** (`--plugin-dir`; skill names surface as `naxos:{name}`) in a
  directory *outside* the checkpointed workspace, fetched from internal `GET
  /sessions/{id}/skills` on every wake. A skill is mounted only if it is
  unarchived and has a `SKILL.md`.
- **Settings isolation**: the harness always runs the SDK with
  `setting_sources=[]`. The SDK's default loads `.claude/settings.json` from
  the cwd — which is the agent-writable, checkpoint-persisted workspace — and
  settings can register hooks that execute outside the tool gate. Isolation
  mode closes that hole; skills mount via the plugin mechanism instead of
  project settings for the same reason.
- **Read-only from the sandbox** — deliberate deviation from memory: there is
  no skill writeback path, the plugin tree is outside the workspace checkpoint,
  and it is rebuilt before every SDK turn, so a prompt-injected agent cannot
  poison a skill shared by every other agent — even within its own session.
  Skills change only through the API, by a human principal. Skill file paths
  are validated control-plane-side (relative, no `..`/empty segments) and the
  sandbox skips any file that would land outside its skill directory.
- **Governance unchanged**: `Skill` invocations pass through the same
  `PreToolUse` gate as every other tool call — the permission policy and kill
  switch apply, and the call lands in `audit.tool_calls`.
- **Not versioned** (like memory, documented): editing a skill changes it for
  every agent that references it, including pinned agent versions.
- **Tags** (`skills.tags`, free-form labels, `PATCH /v1/skills/{id}`): an
  organisational aid for a library that grows past what one list can show —
  `GET /v1/skills?tag=` filters, the UI offers the same filter in the skills
  view and in the agent form's skill picker. Tags carry no semantics for
  mounting or governance; the sandbox never sees them.

### Storage split

- **Postgres** — all queryable control-plane state (above).
- **GCS** (`naxos2-sess-{env}`) — `sessions/{id}/transcript.jsonl` (SDK session file), `sessions/{id}/ws/**` (workspace), and `sessions/{id}/artifacts/**` (published artifact content).
- **BigQuery `audit`** — append-only governance record:

```
audit.runs(run_id, session_id, agent_id, environment_id, deployment_run_id,
           trigger_type,        -- interactive | deployment
           principal, started_at, ended_at, status, stop_reason,
           num_turns, input_tokens, output_tokens, cost_usd, approx_cost_jpy, model)
           -- a "run" = one wake-to-idle processing burst of a session

audit.tool_calls(tool_call_id, run_id, session_id, agent_id, agent_version,
           environment_id, principal, approved_by, ts, tool_use_id,
           tool_name, call_hash, args_json, args_truncated,
           decision,            -- auto_allowed | user_allowed | user_denied |
                                -- not_allowed | killed | awaiting_confirmation
           result_status, latency_ms, error)
           -- args_redacted is a dead column: rows written before the execution
           -- record change have it populated and args_json NULL. Dropping it needs
           -- deletion_protection = false and a table recreate.
```

**The write is two-stage, and it has to be.** Postgres is the system of record: the
`tool_calls` row is committed at the permission gate, before the tool runs. BigQuery is
an append-only export of *completed* rows, written at each checkpoint — a streamed row
cannot be updated for ~90 minutes, so a row written at decision time could never gain its
result. The Postgres id is sent as the BigQuery `insertId`, so an export retried after a
failed watermark update is de-duplicated rather than double-counted; a call still open at
the burst boundary is exported as `no_result`.

`latency_ms` is measured control-plane side, from the gate's answer to the arrival of the
reported result, so it includes the sandbox's event batching and is an **upper bound** —
deliberately, rather than trusting a sandbox-supplied number in a field meant as evidence.

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

GET    /v1/connectors                      curated MCP connector catalog (§5.1)

POST   /v1/sessions                        {agent: {id, version?} | agent_with_overrides,
                                            initial_events?, budget?, vault_ids?,
                                            memory_store_ids?, resources?}
GET    /v1/sessions[?agent_id&status] · GET /v1/sessions/{id}
PATCH  /v1/sessions/{id}                   raise budget
POST   /v1/sessions/{id}/terminate
DELETE /v1/sessions/{id}                   rows + GCS prefix; refused while a sandbox
                                           holds a live lease (audit in BigQuery survives)
POST   /v1/sessions/{id}/events            user.message | user.interrupt |
                                           user.tool_confirmation | user.custom_tool_result
GET    /v1/sessions/{id}/events?after={seq}&limit=      list (cursor = seq)
GET    /v1/sessions/{id}/events?stream=sse&after={seq}  SSE (Last-Event-ID honored)

POST   /v1/deployments · GET /v1/deployments[/{id}]
POST   /v1/deployments/{id}/pause | /unpause | /archive | /run
GET    /v1/deployments/{id}/runs
GET    /v1/deployments/runs?days=&deployment_id=&status=   -- run history + per-deployment rollup

POST   /v1/vaults · GET /v1/vaults[/{id}] · POST /v1/vaults/{id}/archive
POST   /v1/vaults/{id}/credentials         write-only; value → Secret Manager directly
GET    /v1/vaults/{id}/credentials         metadata only
DELETE /v1/vaults/{id}/credentials/{cid}

POST   /v1/memory_stores · GET /v1/memory_stores · PATCH/DELETE /v1/memory_stores/{id}
       DELETE refuses (409) while the store is attached to an agent or an active session
POST   /v1/memory_stores/{id}/memories · GET (list) · GET/PUT/DELETE …/memories/{mid}

GET    /v1/artifacts[?session_id&agent_id] · GET /v1/artifacts/{id}[/content]
PATCH  /v1/artifacts/{id}                  description
DELETE /v1/artifacts/{id}                  row + blob
POST   /v1/artifacts/{id}/share · DELETE …/share    mint / revoke the share token
GET    /v1/artifacts/shared/{token}[/content]       stable share URL (still behind IAP)

POST   /v1/skills · GET /v1/skills[/{id}] (?tag=) · PATCH /v1/skills/{id} (description, tags)
POST   /v1/skills/{id}/archive
POST   /v1/skills/{id}/files               upsert by path
GET    /v1/skills/{id}/files · GET/DELETE …/files/{fid}

GET    /v1/tool_confirmations?status&agent_id&environment_id&session_id&limit
                                           approval inbox across every session;
                                           decide via POST /v1/sessions/{id}/events

GET    /v1/tool_calls?session_id&agent_id&environment_id&tool_name&principal&decision
                      &result_status&since&until&cursor&limit
                                           the execution record; cursor = last row id
GET    /v1/tool_calls/export?<same filters>  the whole filtered record as NDJSON

GET    /v1/monitoring/summary?days=N       cost/usage aggregates from session_runs
                                           (the deployments runs view reads
                                            /v1/deployments/runs, not this)

GET    /v1/favorites · POST /v1/favorites  per-principal stars on agents / sessions /
DELETE /v1/favorites/{type}/{id}           artifacts / skills, surfaced first in UI lists
```

Internal surface (`naxos-internal`, IAM-only): per-session `claim / heartbeat / queue?wait / events / permission / stream (transient partial-text fan-out) / checkpoint / config / skills / memory_writeback / artifacts (list·register·delete·share) / deployments (list·create·archive)`, plus `deployments/{id}/fire` and `reconcile`.

Event types (CMA vocabulary): `user.message`, `user.interrupt`, `user.tool_confirmation`, `user.custom_tool_result`, `agent.message`, `agent.thinking`, `agent.tool_use`, `agent.tool_result`, `agent.artifact` (deviation: artifact lifecycle in the timeline), `session.status_running`, `session.status_idle`, `session.status_rescheduling` (deviation: extends the CMA status events so the §1 "waking up" UI state is observable over SSE — the `rescheduling` status itself is CMA's), `session.status_terminated`, `session.error`, `span.model_request_start`, `span.model_request_end`. One transient frame type rides the SSE stream without being part of the persisted vocabulary: `agent.message_delta` (§4 token-level partial text) — never stored, never replayed, not client-sendable.

Documented deviations from CMA: IAP auth instead of API keys; environments operator-provisioned; budget enforced post-response rather than pre-request; `span.*` approximated from the SDK stream; no outcomes / multiagent / webhooks initially; per-principal favorites as a naxos-only convenience surface; untitled sessions auto-derive their title from the first `user.message` (explicit titles always win) so lists never show bare ids.

## 8. Security model

- **Per-environment SA is the isolation boundary.** `sa-env-{env}` gets `aiplatform.user` (the only model exit), objectAdmin on its own session bucket, and `run.invoker` on `naxos-internal` + `naxos-egress` — nothing else by default. No secrets, no other environment's anything. BigQuery is a declarative per-environment opt-in: a `bigquery_datasets` list in `environments.json` grants the SA `bigquery.jobUser` plus `dataViewer` on exactly the listed datasets (`naxos_audit` itself is never grantable; the platform's own history is reachable only as authorized views in `naxos_audit_shared`, see §6 BigQuery) and is passed to the sandbox job as `BIGQUERY_DATASETS`, which is what decides whether the BigQuery tools exist at all — so data access stays Terraform-provisioned and reviewable, never API-granted. A fully prompt-injected agent is still boxed by IAM.
- **Sandbox ↔ control-plane auth**: OIDC ID token of the env SA → Cloud Run IAM on `naxos-internal`, then an app-level check that the token's SA equals `environments.service_account_email` for the session being touched. No bearer tokens to mint or leak.
- **Tool restriction**: a non-empty `tools` list on the agent version is enforced in the control plane's permission endpoint — a call to anything outside it is denied before any policy or confirmation lookup, and audited `not_allowed`. It cannot be enforced by the SDK: `allowed_tools` only pre-approves calls (measured, §4.1), and the CLI's built-ins cannot be withheld from the model at all, so the gate is the only place the restriction can actually hold. Entries may be globs, so `mcp__artifacts__*` names a whole built-in server. An empty list means unrestricted. Args are schema-validated in guarded wrapper code with caps enforced in code; errors return as tool results.
- **Approval reaches a human off-app.** The gate logs `naxos.approval_required` when it parks a call. When `alert_email` is set, Terraform creates a log-based metric on that marker plus an alert policy that mails it — no new service, nothing always-on, and the signal stays inside the project. Empty `alert_email` skips all of it. The alert is deliberately coarse (see §14): it says somebody should look, not who must decide.

- **Kill switch, three levels**: `agents.disabled` (checked at event accept and inside the permission gate before every tool call); session terminate; environment pause. Disabling an agent also pauses its deployments' Scheduler jobs.
- **Audit**: the record is written at the permission gate, not reconstructed from what the sandbox reports. Every tool call blocks on `POST /internal/sessions/{id}/permission`, and that endpoint commits the `tool_calls` row inside the same transaction that resolves the decision — so the row exists before the tool runs, the decision label is computed control-plane side, and a call the sandbox never lives to report (OOM, lost lease, the 60-minute execution cap) is still on the record. `agent.tool_use` remains a timeline event and is no longer an audit source; the gate returns its `label` and `tool_call_id` so the two cannot drift. `principal` is the IAP email of whoever sent the turn — latched from the queued events at claim, not the session's creator — or `deployment:{id}` for cron, with `approved_by` naming the human who answered a gated call.

  **What the record proves, and what it does not.** It is complete and single-writer: it covers every call that passed the gate, with its arguments, decision and outcome. It is not tamper-evident against a Postgres-level compromise, and it does not follow a call past the gate — a `Bash` call that spawns subprocesses is one audited call, and what those subprocesses may reach is bounded by IAM, not by audit.
- **IAP** directly on Cloud Run with the custom OAuth client (no-org project); the app verifies the IAP JWT.

## 9. Cost model (reference sizing: ¥100k/month cap, ¥70k target)

| Item | Sizing | ¥/month |
|---|---|---|
| Cloud SQL Postgres | db-g1-small, 10GB SSD, no HA, private IP | ~5,500 |
| naxos-api / internal / egress | all min=0 | ~1,000–2,000 |
| Sandbox executions | 1 vCPU / 2GiB ≈ ¥15 per active-hour; generous 150h/mo | ~2,500 |
| Scheduler / GCS / BQ streaming / Secret Manager / logs | | ~1,000 |
| **Infra total** | | **≈ ¥10k** |
| Model (Vertex) headroom | | ~¥60–90k |

Runaway protection: a control-plane global cap on concurrent sandbox executions (default 5). The idle-linger window (~120s) is the main sandbox cost knob. If cold-resume chat UX hurts, `naxos-api` min=1 adds ~¥4k — still inside target.

`tool_calls` is the only Cloud SQL growth the execution record introduces, and Cloud SQL is the one always-on cost. At the 4KB `MAX_TOOL_ARGS_BYTES` cap, 200k calls is roughly 1GB — comfortable on the 10GB instance, but unbounded over years. A retention sweep is deferred (§14); the BigQuery export is already the archive, so pruning exported rows is the eventual answer rather than growing the disk.

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
terraform/         one root module; environments.json → for_each (SA, Job, bucket, IAM);
                   connectors.json → for_each (self-hosted MCP service, SA, secrets, IAM)
docs/              design docs (English)
.github/workflows/ lint/test + WIF deploy loop
```

## 11. Build order

Each phase ends deployed and demoable.

1. **Spike (blocking)**: Agent SDK resume semantics — pending tool_use replay through `can_use_tool`, transcript restore from a relocated directory, Vertex backend availability in asia-northeast1/global (resolves the model-region open issue).
2. **Walking skeleton**: Terraform base (SQL, buckets, BQ, SAs, services, one `default` environment Job) + agents CRUD (versioned) + sessions + events + the full loop: create session → job launch → SDK turn on Vertex → events → SSE → idle checkpoint → resume. Audit (`runs` + `tool_calls`) and kill switch from day one. Reconciler.
3. **Permissions + budget + interrupt**: `always_ask` round-trip including pause/release/resume, budget enforcement, minimal UI (session timeline + approval inbox).
4. **Deployments**: Scheduler-per-deployment, `deployment_runs` with error types, outcome and duration closed at checkpoint, pause/unpause/run-now, UI tab with a runs view (duration chart, per-deployment success rate and run history).
5. **Vaults + egress proxy**: Secret Manager write path, MCP URL rewriting, header substitution, per-vault IAM.
6. **Memory stores**: CRUD + mount/writeback + UI.
7. **Hardening**: second environment (proves the fan-out), budget alerts, cost review at the phase gate.

## 12. Verification

- Phase 1 spike has an explicit pass/fail: resume replays the pending tool_use, or the fallback is adopted and documented here.
- Every phase ships to the project and runs its end-to-end demo (Phase 2: curl create agent → session → SSE shows `agent.message` → `audit.runs` row exists → flip `disabled` → event rejected).
- Approval flow: `always_ask` tool → container exits during the pause (verify zero cost while pending) → confirm via UI → session resumes and completes.
- Deployments: cron fires → `deployment_runs` row → session completes and the run closes with its outcome, duration and cost; error path exercised by archiving the agent, and the budget/terminate paths by stopping a fired session.
- Cost gate at Phase 7: one month of billing export reviewed against the ¥10k infra estimate.

## 13. GCP verification

The end-to-end system was deployed to a live project and verified:

- **Plain session**: create agent → session with `initial_events` → sandbox Job execution boots → SDK turn (Anthropic API) → `agent.message` → `session.status_idle(end_turn)` → cost and `sdk_session_id` checkpointed. Model reply round-trip ≈ 40s including cold start.
- **Approval cycle**: `always_ask` agent paused at `agent.tool_use(awaiting_confirmation)` → `session.status_idle(requires_action)` → **the sandbox execution exited while waiting** (zero cost while pending) → `user.tool_confirmation(allow)` → fresh execution resumed the SDK session → the model re-issued the same call under a new `tool_use_id` → the stored decision (keyed by call hash) allowed it → command ran and completed. This validates the load-bearing design decision end to end.
- **Audit**: `naxos_audit.runs` (one row per wake-to-idle burst) and `naxos_audit.tool_calls` (per-call decisions) populated by the control plane.
- **Deviation found on GCP**: exceptions raised inside a `PreToolUse` hook are swallowed by the SDK (the CLI falls back to its own permission system). The pause is therefore implemented as deny + interrupt, with `paused_call` state driving `requires_action` — §4 updated accordingly by the code.

Both issues this run surfaced have since been fixed in code: the negative per-run
`cost_usd` delta on resume bursts (per-burst baseline accounting in the harness plus a
delta at checkpoint) and the human-approved call mislabelled `auto_allowed` (the gate now
labels its own decision, and the sandbox uses that label verbatim).

## 14. Risks and open items

- **R1 — resolved.** Resume does replay the pending tool call; see §4.1. The design stands, with confirmations keyed on the call hash and the gate implemented as a `PreToolUse` hook.
- **R2 — open, and it now has a hard finding.** Claude models are **not available in `asia-northeast1`** on Vertex: `getPublisherModel` returns "not found" for `claude-sonnet-5` / `claude-opus-5` in both `asia-northeast1` and `us-east5`, and resolves only on the **`global`** endpoint. A live `rawPredict` against `global` then returned **429 `RESOURCE_EXHAUSTED`** — a fresh project's `global_online_prediction_requests_per_base_model` quota for `anthropic-claude-sonnet` is zero, so a quota-increase request is required before Vertex can serve any traffic.

  Two decisions this forces, both for the deployment operator (per the hard constraint against silently deciding region questions):
  1. **Data residency**: using Vertex at all means using the `global` endpoint — inference is not pinned to `asia-northeast1`. Accept, or keep model traffic off Vertex until a regional endpoint exists.
  2. **Unblocking**: file the Vertex quota-increase request now, since it gates the removal of the Anthropic API-key exception. Until then the key exception stays, and no real internal data may be connected.
- Egress proxy covers MCP + declared HTTP targets only; arbitrary bash egress is credential-less (documented limitation).
- **No retention sweep on `tool_calls` yet.** Rows accumulate in Postgres after export. The bound is §9's sizing, not a policy; pruning exported rows past a retention window is the follow-up.
- **Approval notification is one channel, and it is coarse.** The log-based alert (§8) mails a single address on any pause; there is no routing to the agent's owner, no per-approver assignment, and no acknowledgement. It answers "somebody should look" rather than "you specifically must decide this". Per-approver routing needs an org/role model the platform does not have.
- **Connectors run as a service identity** (§5.1): no OAuth authorization-code flow, no per-end-user identity. In the SaaS's own audit log every action is attributed to one integration account, so "which human caused this" is answerable from naxos's audit but not from Slack's or Jira's. Revisit if per-user attribution becomes a requirement.
- **A connector granted to a tenant is reachable outside the tool gate.** `run.invoker` is network reach and the hosted servers rely on Cloud Run IAM alone, so an agent with Bash in a granted environment can call that connector directly with a self-minted ID token, bypassing the permission policy, the approval gate and `audit.tool_calls`. Governance inside the sandbox harness cannot close this — only the IAM grant can. Keep `connectors` in `environments.json` to tenants that may use the system with its shared credential, and scope each connector's token to the least it needs. (Same class as the documented credential-less-bash limitation, but here the credential lives server-side.)
- **Connector deployment is not yet verified live**: the egress round-trip with a real token, `hosted` connector cold-start latency inside a turn, and Google Workspace domain-wide delegation all need a deployed project and real credentials.
- Memory versioning, outcomes, multiagent, webhooks: deferred; schema leaves room.
