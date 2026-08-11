import asyncio
import logging
import os
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    UserMessage,
)
from naxos_shared.events import EventType, SessionConfig, StopReason
from naxos_shared.ids import call_hash

from .control import ControlChannel

log = logging.getLogger(__name__)

CONTINUE_PROMPT = (
    "Continue the work you were doing before this session was resumed. "
    "If you were waiting on approval for a tool call, that decision is now available."
)


class BudgetReached(Exception):
    pass


class Harness:
    """Runs one wake-to-idle burst of a session.

    The permission gate is a PreToolUse hook, not can_use_tool: whole-tool
    allowlist entries and the CLI's read-only auto-approval both bypass
    can_use_tool, so it cannot gate every call.
    """

    def __init__(self, channel: ControlChannel, config: SessionConfig, cwd: str) -> None:
        self.channel = channel
        self.config = config
        self.cwd = cwd
        self.run_id = os.environ.get("CLOUD_RUN_EXECUTION", channel.session_id)
        self.pending: list[dict[str, Any]] = []
        self.sdk_session_id: str | None = config.sdk_session_id
        self.initial_cost_usd: float = float(config.cost_usd or 0.0)
        self.cost_usd: float = self.initial_cost_usd
        self.stop_reason = StopReason.END_TURN
        self.paused_call: dict[str, Any] | None = None
        self._client: ClaudeSDKClient | None = None
        self.interrupted = False
        self.killed = False
        self.num_turns = 0

    async def interrupt(self) -> None:
        """Stop the in-flight turn. Called by the queue watcher mid-run."""
        self.interrupted = True
        if self._client is not None:
            try:
                await self._client.interrupt()
            except Exception:
                log.exception("SDK interrupt failed")

    # --- event plumbing ---------------------------------------------------

    def _queue(self, type_: EventType, payload: dict[str, Any]) -> None:
        self.pending.append({"type": str(type_), "payload": payload})

    async def flush(self) -> None:
        events, self.pending = self.pending, []
        await self.channel.emit(events, self.run_id)

    # --- permission gate --------------------------------------------------

    async def _pre_tool_use(self, hook_input, tool_use_id, context):
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {}) or {}
        digest = call_hash(tool_name, tool_input)

        verdict = await self.channel.ask_permission(digest, tool_name, tool_input, tool_use_id)
        decision = verdict.get("decision")

        if decision == "pending":
            # Raising here does not stop the SDK turn (hook exceptions are
            # swallowed and the CLI falls back to its own permission system,
            # observed on GCP). Deny the call and interrupt the turn instead;
            # run() sees paused_call and reports requires_action.
            self.paused_call = {
                "call_hash": digest,
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
            }
            self._queue(
                EventType.AGENT_TOOL_USE,
                {
                    "tool_name": tool_name,
                    "input": tool_input,
                    "tool_use_id": tool_use_id,
                    "call_hash": digest,
                    "decision": "awaiting_confirmation",
                },
            )
            await self.flush()
            if self._client is not None:
                asyncio.create_task(self._client.interrupt())
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "This call requires human approval. Execution is paused; "
                        "do not retry or work around it."
                    ),
                }
            }

        allowed = decision == "allow"
        if verdict.get("killed"):
            label = "killed"
        elif not allowed:
            label = "user_denied"
        elif verdict.get("by") == "user":
            label = "user_allowed"
        else:
            label = "auto_allowed"
        self._queue(
            EventType.AGENT_TOOL_USE,
            {
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
                "call_hash": digest,
                "decision": label,
            },
        )
        if verdict.get("killed"):
            self.killed = True
            if self._client is not None:
                asyncio.create_task(self._client.interrupt())
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": verdict.get("reason", ""),
            }
        }

    # --- the loop ---------------------------------------------------------

    def options(self) -> ClaudeAgentOptions:
        # Skills need project setting_sources so the SDK discovers
        # ws/.claude/skills; the "Skill" tool call still goes through the
        # PreToolUse gate like any other, so the permission policy applies.
        extra: dict[str, Any] = {}
        tools = list(self.config.tools)
        if self.config.skill_names:
            extra["setting_sources"] = ["project"]
            if tools and "Skill" not in tools:
                tools.append("Skill")
        return ClaudeAgentOptions(
            cwd=self.cwd,
            system_prompt=self.config.instructions,
            model=self.config.model,
            mcp_servers=self.config.mcp_servers,
            allowed_tools=tools,
            max_turns=self.config.max_turns,
            resume=self.sdk_session_id,
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use])]},
            **extra,
        )

    def _budget_exhausted(self) -> bool:
        budget = self.config.budget_usd
        return budget is not None and self.cost_usd >= float(budget)

    def _accumulate_cost(self, total_cost_usd: float | None) -> None:
        # The SDK's counter covers this burst only and resets on resume, while
        # sessions.cost_usd accumulates across bursts.
        if total_cost_usd is not None:
            self.cost_usd = self.initial_cost_usd + float(total_cost_usd)

    async def run(self, prompts: list[str]) -> StopReason:
        if self._budget_exhausted():
            return StopReason.BUDGET_REACHED
        async with ClaudeSDKClient(self.options()) as client:
            self._client = client
            try:
                for prompt in prompts:
                    if self._budget_exhausted():
                        return StopReason.BUDGET_REACHED
                    if self.interrupted or self.killed:
                        return StopReason.END_TURN
                    await client.query(prompt)
                    try:
                        await self._drain(client)
                    except BudgetReached:
                        return StopReason.BUDGET_REACHED
                    finally:
                        await self.flush()
                    if self.paused_call is not None:
                        return StopReason.REQUIRES_ACTION
                    if self.killed:
                        return StopReason.END_TURN
            finally:
                self._client = None
        return StopReason.END_TURN

    async def _drain(self, client: ClaudeSDKClient) -> None:
        self._queue(EventType.SPAN_MODEL_REQUEST_START, {})
        async for message in client.receive_response():
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    self.sdk_session_id = message.data.get("session_id", self.sdk_session_id)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        self._queue(EventType.AGENT_MESSAGE, {"text": block.text})
                    elif isinstance(block, ThinkingBlock):
                        self._queue(EventType.AGENT_THINKING, {})
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        self._queue(
                            EventType.AGENT_TOOL_RESULT,
                            {
                                "tool_use_id": block.tool_use_id,
                                "is_error": bool(block.is_error),
                                "content": str(block.content)[:4000],
                            },
                        )
            elif isinstance(message, ResultMessage):
                self._accumulate_cost(message.total_cost_usd)
                self.num_turns += int(message.num_turns or 0)
                self._queue(
                    EventType.SPAN_MODEL_REQUEST_END,
                    {
                        "num_turns": message.num_turns,
                        "cost_usd": self.cost_usd,
                        "is_error": message.is_error,
                    },
                )
                await self.flush()
                if self._budget_exhausted():
                    raise BudgetReached(self.cost_usd)
