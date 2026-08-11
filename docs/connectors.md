# Connectors

naxos writes no connector code. A connector is an existing MCP server attached to an
agent version's `mcp_servers`; the platform supplies credentials, authentication and
governance around it. The design rationale is in [`design.md` §5.1](design.md#51-connectors).

| Connector | Server | Shape | Credential |
|---|---|---|---|
| GitHub | [`github/github-mcp-server`](https://github.com/github/github-mcp-server) (vendor-hosted) | `remote` | fine-grained PAT in a vault |
| Slack | [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server) | `hosted` | bot/user token |
| Jira + Confluence | [`sooperset/mcp-atlassian`](https://github.com/sooperset/mcp-atlassian) (one service covers both) | `hosted` | Atlassian Cloud email + API token |
| Notion | [`makenotion/notion-mcp-server`](https://github.com/makenotion/notion-mcp-server) | `hosted` | internal integration token |
| Google Workspace | [`taylorwilsdon/google_workspace_mcp`](https://github.com/taylorwilsdon/google_workspace_mcp) | `hosted` | the service's own SA with domain-wide delegation |

`GET /v1/connectors` returns this catalog with each entry's resolved URL and
availability; the agent form shows it as toggle chips.

## Two shapes

**`remote`** — the vendor hosts the MCP server. The agent declares the real URL; the
control plane rewrites it to `naxos-egress` with an opaque route token and the proxy
injects the credential. The token never enters the sandbox.

**`hosted`** — the upstream OSS server runs as a scale-to-zero Cloud Run service in this
project (`naxos-mcp-{name}`). Its credentials are Secret Manager env refs on that
service, readable only by its own service account. No vault, no egress route, and
third-party server code never runs inside the sandbox.

Either way the sandbox reaches the service through the in-process localhost forwarder
(`naxos_sbx.mcp_gateway`), which mints a per-request OIDC token for the target — the SDK's
MCP client cannot do this itself, and Cloud Run IAM rejects unauthenticated calls.

## Adding a `remote` connector (GitHub)

```bash
# 1. Store the token. The value goes straight to Secret Manager and is never returned.
curl -X POST "$API/v1/vaults" -d '{"name": "github"}'
curl -X POST "$API/v1/vaults/$VAULT_ID/credentials" -d '{
  "name": "pat",
  "type": "header",
  "value": "github_pat_...",
  "target": {"mcp_server": "github", "header": "authorization", "prefix": "Bearer "}
}'
```

Then create the agent version with the server and a permission rule for its tools. The
`mcp_server` name in the credential target must match the key in `mcp_servers`:

```json
{
  "mcp_servers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}},
  "vault_ids": ["vault_..."],
  "permission_policy": {
    "default": "always_ask",
    "rules": [{"tool": "mcp__github__get_*", "mode": "always_allow"}]
  }
}
```

Reads are auto-allowed here and everything else — including every write — still stops at
the approval gate. Widen the globs deliberately.

## Adding a `hosted` connector

Deployment is three steps, in order:

1. **Provision.** Add or edit the entry in `terraform/connectors.json`, then
   `terraform apply`. This creates the service account, the secret shells, the Cloud Run
   service and the per-environment `run.invoker` bindings.
2. **Set the secret values** (Terraform owns shells, never values):
   ```bash
   printf '%s' "$TOKEN" | gcloud secrets versions add mcp-slack-slack-mcp-xoxp-token --data-file=-
   ```
3. **Mirror the image.** Cloud Run cannot pull from Docker Hub or ghcr, so the upstream
   image is copied into Artifact Registry and rolled out:
   ```bash
   ./scripts/mirror_connectors.sh slack     # omit the name to do all of them
   ```

Pin the upstream version by editing `upstream_image` in `connectors.json` — running
`:latest` means an upstream release changes what your agents can do without review.

Google Workspace additionally needs domain-wide delegation granted to
`naxos2-mcp-gworkspace@{project}.iam.gserviceaccount.com` in the Workspace admin console,
with the scopes the agents need. That step is manual and cannot be Terraformed.

Once the service URL is set on `naxos-api` (Terraform injects `NAXOS_MCP_{NAME}_URL`), the
connector shows as available in the catalog and the UI.

## Governance

A connector tool is an ordinary tool call. `mcp__{server}__{tool}` names go through the
same permission policy globs, the same `PreToolUse` approval gate, the same kill switch,
and land in `audit.tool_calls` like any other call. Nothing about attaching a connector
widens what an agent may do without an explicit policy rule.

**Known limitation.** Connectors run as a single service identity: there is no OAuth
authorization-code flow and no per-end-user identity. Slack, Jira and Notion will attribute
every action to the integration account, not to the human who triggered the session.
naxos's own audit still records the human principal, so "who caused this" is answerable
here — just not from the SaaS side. Scope each connector's token to the least it needs.
