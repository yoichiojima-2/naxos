import asyncio
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    UserMessage,
)
from naxos_shared.events import EventType, SessionConfig, StopReason
from naxos_shared.ids import call_hash

from . import artifacts, bigquery, schedules
from .control import ControlChannel
from .skills_sync import PLUGIN_NAME

log = logging.getLogger(__name__)

# CLI built-ins that schedule work inside this container's memory. A sandbox
# execution exists only while the session is actively processing, so anything
# they schedule dies unfired at the next idle checkpoint — and the platform can
# neither see, audit, nor pause it. The schedules MCP server is the durable,
# governed replacement.
SESSION_LOCAL_SCHEDULER_TOOLS = ["CronCreate", "CronDelete", "CronList", "ScheduleWakeup"]

# Partial-text flushes ride pg_notify (8000-byte payload cap) as transient
# frames, so chunks stay small and are paced rather than sent per token.
STREAM_FLUSH_SECONDS = 0.3
STREAM_CHUNK_CHARS = 1000

CONTINUE_PROMPT = (
    "Continue the work you were doing before this session was resumed. "
    "If you were waiting on approval for a tool call, that decision is now available."
)


class BudgetReached(Exception):
    pass


def _result_text(content: Any) -> str:
    """Tool result content as the agent saw it, not as Python repr."""
    if isinstance(content, list):
        parts = [
            block["text"]
            if isinstance(block, dict) and isinstance(block.get("text"), str)
            else str(block)
            for block in content
        ]
        return "\n\n".join(parts)[:4000]
    return ("" if content is None else str(content))[:4000]


def _derive_label(allowed: bool, killed: bool, by: str | None) -> str:
    if killed:
        return "killed"
    if not allowed:
        return "not_allowed" if by == "policy" else "user_denied"
    return "user_allowed" if by == "user" else "auto_allowed"


class Harness:
    """Runs one wake-to-idle burst of a session.

    The permission gate is a PreToolUse hook, not can_use_tool: whole-tool
    allowlist entries and the CLI's read-only auto-approval both bypass
    can_use_tool, so it cannot gate every call.
    """

    def __init__(
        self,
        channel: ControlChannel,
        config: SessionConfig,
        cwd: str,
        plugin_dir: str | None = None,
    ) -> None:
        self.channel = channel
        self.config = config
        self.cwd = cwd
        self.plugin_dir = plugin_dir
        self.run_id = channel.run_id
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
        self.input_tokens = 0
        self.output_tokens = 0
        self._stream_seq = 0
        self._stream_id: str | None = None
        self._stream_text = ""
        self._stream_last_flush = 0.0
        self._stream_broken = False
        self._closed_streams: list[str] = []
        for reserved in ("artifacts", "schedules", "bigquery"):
            if reserved in (config.mcp_servers or {}):
                log.warning(
                    "agent-configured MCP server '%s' is shadowed by the built-in tools", reserved
                )
        self.artifact_server = artifacts.build_server(channel, config, Path(cwd))
        self.schedule_server = schedules.build_server(channel)
        # None unless the environment was opted into BigQuery datasets, so an
        # environment without the grant has no BigQuery tools at all.
        self.bigquery_server = bigquery.build_server()

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

    # --- partial-text streaming -------------------------------------------

    async def _on_stream_event(self, event: dict[str, Any]) -> None:
        """Relay text deltas as transient frames; agent.message supersedes them.

        Stream ids pair the deltas with the persisted message that follows, so
        the UI can drop a late delta instead of replaying its text.
        """
        type_ = event.get("type")
        if type_ == "content_block_start":
            if (event.get("content_block") or {}).get("type") == "text":
                self._stream_seq += 1
                self._stream_id = f"{self.run_id}:{self._stream_seq}"
                self._stream_text = ""
        elif type_ == "content_block_delta" and self._stream_id is not None:
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                self._stream_text += delta.get("text", "")
                now = asyncio.get_running_loop().time()
                if (
                    len(self._stream_text) >= STREAM_CHUNK_CHARS
                    or now - self._stream_last_flush >= STREAM_FLUSH_SECONDS
                ):
                    await self._flush_stream()
        elif type_ == "content_block_stop" and self._stream_id is not None:
            await self._flush_stream()
            self._closed_streams.append(self._stream_id)
            self._stream_id = None

    async def _flush_stream(self) -> None:
        if not self._stream_text or self._stream_id is None or self._stream_broken:
            self._stream_text = "" if self._stream_broken else self._stream_text
            return
        text, self._stream_text = self._stream_text, ""
        self._stream_last_flush = asyncio.get_running_loop().time()
        try:
            for i in range(0, len(text), STREAM_CHUNK_CHARS):
                await self.channel.emit_stream(
                    {"stream": self._stream_id, "text": text[i : i + STREAM_CHUNK_CHARS]}
                )
        except Exception:
            self._stream_broken = True
            log.exception("partial-text streaming failed; falling back to whole messages")

    # --- permission gate --------------------------------------------------

    def _interrupt_client(self) -> None:
        if self._client is not None:
            asyncio.create_task(self._client.interrupt())

    async def _pre_tool_use(self, hook_input, tool_use_id, context):
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {}) or {}
        digest = call_hash(tool_name, tool_input)
        call = {
            "tool_name": tool_name,
            "input": tool_input,
            "tool_use_id": tool_use_id,
            "call_hash": digest,
        }

        verdict = await self.channel.ask_permission(digest, tool_name, tool_input, tool_use_id)
        decision = verdict.get("decision")

        if decision == "pending":
            # Raising here does not stop the SDK turn (hook exceptions are
            # swallowed and the CLI falls back to its own permission system,
            # observed on GCP). Deny the call and interrupt the turn instead;
            # run() sees paused_call and reports requires_action.
            self.paused_call = call
            self._queue(EventType.AGENT_TOOL_USE, {**call, "decision": "awaiting_confirmation"})
            await self.flush()
            self._interrupt_client()
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
        killed = bool(verdict.get("killed"))
        # The gate labels its own decision; deriving it here again would let the
        # timeline drift from the audit record. The fallback covers a control
        # plane rolled back to before it sent one.
        label = verdict.get("label") or _derive_label(allowed, killed, verdict.get("by"))
        self._queue(
            EventType.AGENT_TOOL_USE,
            {**call, "decision": label, "tool_call_id": verdict.get("tool_call_id")},
        )
        if killed:
            self.killed = True
            self._interrupt_client()
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": verdict.get("reason", ""),
            }
        }

    # --- the loop ---------------------------------------------------------

    def options(self) -> ClaudeAgentOptions:
        # setting_sources=[] is SDK isolation mode: filesystem settings in the
        # agent-writable (and checkpointed) workspace are never loaded, so an
        # agent cannot persist hooks or permission rules that would run
        # outside the PreToolUse gate. Skills therefore mount as a local
        # plugin outside the workspace, not via project settings; Skill calls
        # still hit the gate like any other tool.
        extra: dict[str, Any] = {}
        if self.config.skill_names and self.plugin_dir:
            extra["plugins"] = [{"type": "local", "path": self.plugin_dir}]
            extra["skills"] = [f"{PLUGIN_NAME}:{name}" for name in self.config.skill_names]
        return ClaudeAgentOptions(
            cwd=self.cwd,
            system_prompt=self.config.instructions,
            model=self.config.model,
            mcp_servers={
                **self.config.mcp_servers,
                "artifacts": self.artifact_server,
                "schedules": self.schedule_server,
                **({"bigquery": self.bigquery_server} if self.bigquery_server else {}),
            },
            allowed_tools=self.config.tools,
            disallowed_tools=SESSION_LOCAL_SCHEDULER_TOOLS,
            max_turns=self.config.max_turns,
            effort=self.config.effort,
            include_partial_messages=True,
            resume=self.sdk_session_id,
            setting_sources=[],
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use])]},
            **extra,
        )

    def _budget_exhausted(self) -> bool:
        budget = self.config.budget_usd
        return budget is not None and self.cost_usd >= float(budget)

    def _accumulate_usage(self, usage: Any) -> None:
        # Reported as-is. Cache-read and cache-creation tokens are deliberately not
        # folded in: the audited number must mean what its name says. cost_usd
        # already carries the true billed spend.
        if not isinstance(usage, dict):
            return
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)

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
        # Flushing after every message (not once per turn) is what makes the
        # timeline live: tool calls and finished text blocks appear as they
        # happen instead of when the whole turn ends.
        # A turn interrupted mid-message can leave a closed stream id with no
        # persisted message to claim it; stale ids must not tag the next turn.
        self._closed_streams.clear()
        self._stream_id = None
        self._stream_text = ""
        self._queue(EventType.SPAN_MODEL_REQUEST_START, {})
        await self.flush()
        async for message in client.receive_response():
            if isinstance(message, StreamEvent):
                await self._on_stream_event(message.event)
                continue
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    self.sdk_session_id = message.data.get("session_id", self.sdk_session_id)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        payload: dict[str, Any] = {"text": block.text}
                        if self._closed_streams:
                            payload["stream"] = self._closed_streams.pop(0)
                        self._queue(EventType.AGENT_MESSAGE, payload)
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
                                "content": _result_text(block.content),
                            },
                        )
            elif isinstance(message, ResultMessage):
                self._accumulate_cost(message.total_cost_usd)
                self._accumulate_usage(message.usage)
                self.num_turns += int(message.num_turns or 0)
                self._queue(
                    EventType.SPAN_MODEL_REQUEST_END,
                    {
                        "num_turns": message.num_turns,
                        "cost_usd": self.cost_usd,
                        "is_error": message.is_error,
                        "input_tokens": self.input_tokens,
                        "output_tokens": self.output_tokens,
                    },
                )
                await self.flush()
                if self._budget_exhausted():
                    raise BudgetReached(self.cost_usd)
                continue
            await self.flush()
