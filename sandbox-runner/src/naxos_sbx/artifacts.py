"""Artifact tools exposed to the agent as an in-process MCP server.

Content is uploaded straight to the environment's session bucket (the env SA
owns it); only metadata goes through the control plane, which records the
artifact row, emits the agent.artifact event, and mints share tokens. Every
tool call passes the PreToolUse permission gate like any other tool, so
artifact operations are audited and subject to policy and the kill switch.
"""

import asyncio
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from naxos_shared.events import SessionConfig

from .config import MAX_ARTIFACT_BYTES
from .control import ControlChannel
from .mcp_result import error, guarded, text
from .workspace import _storage_client

log = logging.getLogger(__name__)

# Mirrors the control plane's ArtifactIn name rules; checked before the blob
# upload so a rejected registration cannot leave orphaned or overwritten content.
NAME_RE = re.compile(r"^[a-zA-Z0-9._/ -]{1,200}$")


def _valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name)) and ".." not in name.split("/") and not name.startswith("/")


async def _upload_blob(bucket: str, path: str, source: Path, content_type: str) -> None:
    def _upload() -> None:
        _storage_client().bucket(bucket).blob(path).upload_from_filename(
            source, content_type=content_type
        )

    await asyncio.to_thread(_upload)


async def _delete_blob(bucket: str, path: str) -> None:
    def _delete() -> None:
        blob = _storage_client().bucket(bucket).blob(path)
        if blob.exists():
            blob.delete()

    await asyncio.to_thread(_delete)


class ArtifactTools:
    def __init__(self, channel: ControlChannel, config: SessionConfig, ws: Path) -> None:
        self.channel = channel
        self.config = config
        self.ws = ws

    def _blob_path(self, name: str) -> str:
        return f"sessions/{self.channel.session_id}/artifacts/{name}"

    def _resolve(self, path: str) -> Path:
        target = (self.ws / path).resolve()
        if not target.is_relative_to(self.ws.resolve()):
            raise ValueError("path must stay inside the workspace")
        if not target.is_file():
            raise ValueError(f"no file at {path}")
        return target

    async def create(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            source = self._resolve(args["path"])
        except ValueError as exc:
            return error(str(exc))
        name = (args.get("name") or "").strip() or source.name
        if not _valid_name(name):
            return error("artifact names may only contain letters, digits, '. _ / -' and spaces")
        size = source.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            return error(f"artifact is {size} bytes; the limit is {MAX_ARTIFACT_BYTES}")
        content_type = (
            mimetypes.guess_type(name)[0]
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream"
        )
        await _upload_blob(self.config.session_bucket, self._blob_path(name), source, content_type)
        record = await self.channel.register_artifact(
            name=name,
            content_type=content_type,
            size_bytes=size,
            description=args.get("description") or None,
        )
        return text(
            f"Published artifact '{name}' (id {record['id']}, version {record['version']}, "
            f"{size} bytes). It is visible in the platform UI; use artifact_share for a "
            "stable link."
        )

    async def list(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = (await self.channel.list_artifacts()).get("data", [])
        summary = [
            {
                "name": r["name"],
                "description": r.get("description"),
                "version": r["version"],
                "size_bytes": r["size_bytes"],
                "share_url": r.get("share_url"),
            }
            for r in rows
        ]
        return text(json.dumps(summary, indent=2))

    async def delete(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        await self.channel.delete_artifact(name)
        try:
            await _delete_blob(self.config.session_bucket, self._blob_path(name))
        except Exception:
            log.exception("artifact blob delete failed for %s", name)
        return text(f"Deleted artifact '{name}'.")

    async def share(self, args: dict[str, Any]) -> dict[str, Any]:
        record = await self.channel.share_artifact(args["name"], shared=True)
        return text(
            f"Artifact '{record['name']}' is shared at {record['share_url']} "
            "(reachable by anyone in the organization; access still goes through IAP)."
        )

    async def unshare(self, args: dict[str, Any]) -> dict[str, Any]:
        record = await self.channel.share_artifact(args["name"], shared=False)
        return text(f"Artifact '{record['name']}' is no longer shared; its link is revoked.")


def build_server(channel: ControlChannel, config: SessionConfig, ws: Path):
    tools_ = ArtifactTools(channel, config, ws)

    create = tool(
        "artifact_create",
        "Publish a file from your workspace as a named artifact — a durable output "
        "that outlives this session and is visible to users in the platform. "
        "Publishing an existing name replaces its content and bumps its version.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace-relative path of the file"},
                "name": {
                    "type": "string",
                    "description": "artifact name; defaults to the filename",
                },
                "description": {"type": "string", "description": "what this artifact is"},
            },
            "required": ["path"],
        },
    )(guarded(tools_.create, "artifact"))

    list_ = tool(
        "artifact_list",
        "List the artifacts this session has published, with versions and share links.",
        {"type": "object", "properties": {}},
    )(guarded(tools_.list, "artifact"))

    delete = tool(
        "artifact_delete",
        "Delete an artifact this session published, including its content and share link.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )(guarded(tools_.delete, "artifact"))

    share = tool(
        "artifact_share",
        "Share an artifact: mints a stable org-internal link you can give to users.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )(guarded(tools_.share, "artifact"))

    unshare = tool(
        "artifact_unshare",
        "Revoke an artifact's share link.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )(guarded(tools_.unshare, "artifact"))

    return create_sdk_mcp_server(
        name="artifacts", version="1.0.0", tools=[create, list_, delete, share, unshare]
    )
