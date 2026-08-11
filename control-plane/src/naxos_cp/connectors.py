"""Curated catalog of MCP connectors this deployment can attach to an agent.

naxos writes no connector logic of its own: each entry points at an existing
MCP server, reached one of two ways.

- ``remote``  — a vendor-hosted MCP endpoint. The agent declares the real URL;
  the control plane rewrites it through naxos-egress and the proxy injects a
  vault credential, so the token never enters the sandbox.
- ``hosted``  — an upstream OSS MCP server deployed as a scale-to-zero Cloud Run
  service in this project. Its credentials are Secret Manager env refs on that
  service, readable only by its own service account; the sandbox never sees
  them and no vault credential is involved. The URL comes from Terraform via
  ``NAXOS_MCP_{NAME}_URL``; an entry with no URL is listed as unavailable.

Either way the agent just gets an MCP server, so permission globs, the
PreToolUse approval gate, the kill switch and tool-call audit all apply
unchanged.
"""

import os

from fastapi import APIRouter, Depends

from .auth import principal_of

router = APIRouter(prefix="/v1")

CATALOG = [
    {
        "name": "github",
        "title": "GitHub",
        "shape": "remote",
        "url": "https://api.githubcopilot.com/mcp/",
        "credential": "Fine-grained personal access token, stored as a vault "
        '\'header\' credential targeting {"mcp_server": "github"}.',
        "upstream": "https://github.com/github/github-mcp-server",
    },
    {
        "name": "slack",
        "title": "Slack",
        "shape": "hosted",
        "credential": "Bot token on the naxos-mcp-slack service.",
        "upstream": "https://github.com/korotovsky/slack-mcp-server",
    },
    {
        "name": "atlassian",
        "title": "Jira & Confluence",
        "shape": "hosted",
        "credential": "Atlassian Cloud email + API token on the naxos-mcp-atlassian service.",
        "upstream": "https://github.com/sooperset/mcp-atlassian",
    },
    {
        "name": "notion",
        "title": "Notion",
        "shape": "hosted",
        "credential": "Internal integration token on the naxos-mcp-notion service.",
        "upstream": "https://github.com/makenotion/notion-mcp-server",
    },
    {
        "name": "gworkspace",
        "title": "Google Workspace",
        "shape": "hosted",
        "credential": "The service's own service account with domain-wide delegation "
        "granted in the Workspace admin console — no key material.",
        "upstream": "https://github.com/taylorwilsdon/google_workspace_mcp",
    },
]


def url_for(name: str) -> str:
    return os.environ.get(f"NAXOS_MCP_{name.upper()}_URL", "")


def entries() -> list[dict]:
    out = []
    for entry in CATALOG:
        url = entry.get("url") or url_for(entry["name"])
        out.append(
            {
                **entry,
                "url": url,
                "available": bool(url),
                "tool_glob": f"mcp__{entry['name']}__*",
            }
        )
    return out


@router.get("/connectors")
async def list_connectors(_: str = Depends(principal_of)) -> dict:
    return {"data": entries()}
