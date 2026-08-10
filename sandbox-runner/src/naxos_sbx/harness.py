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
    ToolUseBlock,
    UserMessage,
)
from naxos_shared.events import EventType, StopReason
from naxos_shared.ids import call_hash

from .control import ControlChannel

log = logging.getLogger(__name__)

CONTINUE_PROMPT = (
    "Continue the work you were doing before this session was resumed. "
    "If you were waiting on approval for a tool call, that decision is now available."
)
PERMISSION_POLL_SECONDS = 2.0


class PermissionDenied(Exception):
    pass


class BudgetReached(Exception):
    pass


class Killed(Exception):
    pass


class Harness:
    """Runs one wake-to-idle burst of a session.

    The permission gate is a PreToolUse hook, not can_use_tool: whole-tool
    allowlist entries and the CLI's read-only auto-approval both bypass
    can_use_tool, so it cannot gate every call.
    """

    def __init__(self, channel: ControlChannel, config: dict[str, Any], cwd: str) -> None:
        self.channel = channel
        self.config = config
        self.cwd = cwd
        self.run_id = os.environ.get("CLOUD_RUN_EXECUTION", channel.session_id)
        self.pending: list[dict[str, Any]] = []
        self.sdk_session_id: str | None = config.get("sdk_session_id")
        self.cost_usd: float = float(config.get("cost_usd") or 0.0)
        self.stop_reason = StopReason.END_TURN
        self.paused_call: dict[str, Any] | None = None
        self._client: ClaudeSDKClient | None = None
        self.interrupted = False

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
            self.stop_reason = StopReason.REQUIRES_ACTION
            raise PermissionDenied(digest)

        allowed = decision == "allow"
        self._queue(
            EventType.AGENT_TOOL_USE,
            {
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
                "call_hash": digest,
                "decision": "auto_allowed" if allowed else "user_denied",
            },
        )
        if verdict.get("killed"):
            self.stop_reason = StopReason.END_TURN
            raise Killed(tool_name)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": verdict.get("reason", ""),
            }
        }

    # --- the loop ---------------------------------------------------------

    def options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=self.cwd,
            system_prompt=self.config.get("instructions"),
            model=self.config.get("model"),
            mcp_servers=self.config.get("mcp_servers") or {},
            max_turns=self.config.get("max_turns"),
            resume=self.sdk_session_id,
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use])]},
        )

    def _budget_exhausted(self) -> bool:
        budget = self.config.get("budget_usd")
        return budget is not None and self.cost_usd >= float(budget)

    async def run(self, prompts: list[str]) -> StopReason:
        if self._budget_exhausted():
            return StopReason.BUDGET_REACHED
        async with ClaudeSDKClient(self.options()) as client:
            self._client = client
            try:
                for prompt in prompts:
                    if self._budget_exhausted():
                        return StopReason.BUDGET_REACHED
                    if self.interrupted:
                        return StopReason.END_TURN
                    await client.query(prompt)
                    try:
                        await self._drain(client)
                    except PermissionDenied:
                        return StopReason.REQUIRES_ACTION
                    except BudgetReached:
                        return StopReason.BUDGET_REACHED
                    except Killed:
                        return StopReason.END_TURN
                    finally:
                        await self.flush()
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
                    elif isinstance(block, ToolUseBlock):
                        pass
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
                self.cost_usd = float(message.total_cost_usd or self.cost_usd)
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
