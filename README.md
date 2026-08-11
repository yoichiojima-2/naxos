# naxos

A Google Cloud implementation of Claude Managed Agents: versioned agents, isolated per-session sandboxes on Cloud Run, an event-sourced session API with SSE streaming, approval gates, vaults with an egress credential proxy, scheduled deployments, and full execution-level audit — inside a single GCP project with Vertex AI as the only model exit.

- Design: [`docs/design.md`](docs/design.md)
- Constraints and conventions: [`CLAUDE.md`](CLAUDE.md)

![Sessions — live agent runs with status, principal, and cost per session](docs/img/sessions.png)

<details>
<summary>A session timeline: event stream, tool calls, and a human approval gate</summary>

![Session timeline — the agent pauses on a gated tool call until a human allows or denies it](docs/img/session-timeline.png)

</details>

## Why

Hosted agent platforms run the agent loop and sandbox on the provider's cloud. naxos exists for workloads that can't leave your own boundary:

- **Data boundary** — model access via Vertex AI only; data never leaves the project.
- **Internal-system integration** — self-hosted MCP servers reach closed-network systems without egress.
- **Execution-level governance** — per-tool-call audit, human approval gates, an instant per-agent kill switch, and per-tenant IAM.
- **Scale-to-zero** — always *available* rather than always running; idle sessions checkpoint to storage and release their container.

## Layout

| Path | What |
|---|---|
| `control-plane/` | FastAPI service — `/v1` REST + SSE (`api` entrypoint) and the sandbox/scheduler/reconciler internal surface (`internal` entrypoint) |
| `sandbox-runner/` | Per-session sandbox: Claude Agent SDK loop, permission hook, budget, checkpoint/resume |
| `egress-proxy/` | Credential-substituting proxy — vault secrets never enter the sandbox |
| `shared/` | Pydantic event/config models shared by the three services |
| `ui/` | Next.js static export, baked into the control-plane image |
| `terraform/` | One root module; `environments.json` fans out per-environment SA / Job / bucket / IAM |
| `docs/` | Design docs |

## Status

Under active development — see the roadmap in [`docs/design.md`](docs/design.md) §11.

## License

[MIT](LICENSE)
