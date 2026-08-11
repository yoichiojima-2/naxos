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
        "type": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "path": "",
        "credential": "Fine-grained personal access token, stored as a vault "
        '\'header\' credential targeting {"mcp_server": "github"}.',
        "upstream": "https://github.com/github/github-mcp-server",
    },
    {
        "name": "slack",
        "title": "Slack",
        "shape": "hosted",
        "type": "http",
        "path": "/mcp",
        "credential": "Slack user or bot token (SLACK_MCP_XOXP_TOKEN) on the "
        "naxos-mcp-slack service.",
        "upstream": "https://github.com/korotovsky/slack-mcp-server",
    },
    {
        "name": "atlassian",
        "title": "Jira & Confluence",
        "shape": "hosted",
        "type": "http",
        "path": "/mcp",
        "credential": "Atlassian Cloud URL, username and API token on the "
        "naxos-mcp-atlassian service.",
        "upstream": "https://github.com/sooperset/mcp-atlassian",
    },
    {
        "name": "notion",
        "title": "Notion",
        "shape": "hosted",
        "type": "http",
        "path": "/mcp",
        "credential": "Internal integration token (NOTION_TOKEN) on the naxos-mcp-notion service.",
        "upstream": "https://github.com/makenotion/notion-mcp-server",
    },
    {
        "name": "gworkspace",
        "title": "Google Workspace",
        "shape": "hosted",
        "type": "http",
        "path": "/mcp",
        "credential": "Service account key JSON with domain-wide delegation granted in "
        "the Workspace admin console, on the naxos-mcp-gworkspace service.",
        "upstream": "https://github.com/taylorwilsdon/google_workspace_mcp",
    },
]


def url_for(entry: dict) -> str:
    """A remote entry knows its own URL; a hosted one gets its service URL from
    Terraform and appends the endpoint path the upstream server serves at."""
    if entry["shape"] == "remote":
        return entry["url"]
    base = os.environ.get(f"NAXOS_MCP_{entry['name'].upper()}_URL", "")
    return base.rstrip("/") + entry["path"] if base else ""


def entries() -> list[dict]:
    return [
        {
            **entry,
            "url": url_for(entry),
            "available": bool(url_for(entry)),
            "requires_vault": entry["shape"] == "remote",
            "tool_glob": f"mcp__{entry['name']}__*",
        }
        for entry in CATALOG
    ]


@router.get("/connectors")
async def list_connectors(_: str = Depends(principal_of)) -> dict:
    return {"data": entries()}
