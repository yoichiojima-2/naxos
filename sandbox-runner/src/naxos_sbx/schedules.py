"""Scheduling tools exposed to the agent as an in-process MCP server.

The durable path for "run this again later, on a schedule": each tool call goes
through the control plane, which records a platform deployment (Cloud Scheduler
backed, visible and governable in the Deployments UI) for this session's agent.
The CLI's own scheduling tools (CronCreate and friends) are disallowed in the
sandbox because they live in the container's memory — a scheduled job would
silently die when the idle session releases its container, and the platform
could neither see nor pause it. Every tool call here passes the PreToolUse
permission gate like any other tool, so scheduling is audited and subject to
policy and the kill switch.
"""

import json
import logging
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from .control import ControlChannel

log = logging.getLogger(__name__)


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


class ScheduleTools:
    def __init__(self, channel: ControlChannel) -> None:
        self.channel = channel

    async def create(self, args: dict[str, Any]) -> dict[str, Any]:
        record = await self.channel.create_deployment(
            name=args["name"],
            cron=args["cron"],
            prompt=args["prompt"],
            timezone=args.get("timezone") or "Asia/Tokyo",
            budget_usd=args.get("budget_usd"),
        )
        return _text(
            f"Created deployment '{record['name']}' (id {record['id']}): cron "
            f"'{record['cron']}' in {record['timezone']}. Each firing starts a fresh "
            "session of this agent with the stored prompt — it will not have this "
            "conversation's context, so the prompt must stand alone. The deployment "
            "outlives this session; users can see, pause, and archive it on the "
            "platform's Deployments page."
        )

    async def list(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = (await self.channel.list_deployments()).get("data", [])
        return _text(json.dumps(rows, indent=2))

    async def delete(self, args: dict[str, Any]) -> dict[str, Any]:
        record = await self.channel.archive_deployment(args["deployment_id"])
        return _text(f"Archived deployment {record['id']}; its schedule no longer fires.")


def _guarded(handler):
    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await handler(args)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            return _error(
                f"control plane rejected the request ({exc.response.status_code}): {detail}"
            )
        except Exception as exc:
            log.exception("schedule tool failed")
            return _error(f"schedule operation failed: {exc}")

    return wrapped


def build_server(channel: ControlChannel):
    tools_ = ScheduleTools(channel)

    create = tool(
        "schedule_create",
        "Schedule this agent to run unattended on a recurring cron schedule. Creates "
        "a durable platform deployment: each firing starts a fresh session of this "
        "agent with the given prompt, with no memory of the current conversation — "
        "write the prompt as a complete standalone instruction. The deployment "
        "survives this session and is visible and manageable in the platform UI. "
        "This is the only way to schedule recurring work; session-local timers do "
        "not survive the session.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "human-readable deployment name"},
                "cron": {
                    "type": "string",
                    "description": "5-field cron expression (minute hour day month weekday)",
                },
                "prompt": {
                    "type": "string",
                    "description": "standalone instruction sent to the agent on each firing",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone for the cron schedule; defaults to Asia/Tokyo",
                },
                "budget_usd": {
                    "type": "number",
                    "description": "optional per-run budget cap in USD",
                },
            },
            "required": ["name", "cron", "prompt"],
        },
    )(_guarded(tools_.create))

    list_ = tool(
        "schedule_list",
        "List this agent's scheduled deployments (agent-created and operator-created), "
        "with ids, cron schedules, and prompts.",
        {"type": "object", "properties": {}},
    )(_guarded(tools_.list))

    delete = tool(
        "schedule_delete",
        "Archive a scheduled deployment by id so it stops firing. Only deployments "
        "created by the agent can be archived; operator-created ones are read-only.",
        {
            "type": "object",
            "properties": {"deployment_id": {"type": "string"}},
            "required": ["deployment_id"],
        },
    )(_guarded(tools_.delete))

    return create_sdk_mcp_server(name="schedules", version="1.0.0", tools=[create, list_, delete])
