# naxos

A Google Cloud implementation of Claude Managed Agents: versioned agents, isolated per-session sandboxes on Cloud Run, an event-sourced session API with SSE streaming, approval gates, vaults with an egress credential proxy, scheduled deployments, and full execution-level audit — inside a single GCP project with Vertex AI as the only model exit.

- Design: [`docs/design.md`](docs/design.md)
- Constraints and conventions: [`CLAUDE.md`](CLAUDE.md)
- The previous PoC is preserved on the [`poc`](../../tree/poc) branch (reference only).

## Layout

| Path | What |
|---|---|
| `control-plane/` | FastAPI service — `/v1` REST + SSE (`api` entrypoint) and the sandbox/scheduler/reconciler internal surface (`internal` entrypoint) |
| `sandbox-runner/` | Per-session sandbox: Claude Agent SDK loop, permission hook, budget, checkpoint/resume |
| `egress-proxy/` | Credential-substituting proxy — vault secrets never enter the sandbox |
| `shared/` | Pydantic event/config models shared by the three services |
| `ui/` | Next.js static export, baked into the control-plane image |
| `terraform/` | One root module; `environments.json` fans out per-environment SA / Job / bucket / IAM |
| `docs/` | Design docs (English) |

## Status

Greenfield rebuild in progress — see `docs/design.md` §11 for the phase plan.
