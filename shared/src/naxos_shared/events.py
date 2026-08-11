from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    RESCHEDULING = "rescheduling"
    TERMINATED = "terminated"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    REQUIRES_ACTION = "requires_action"
    BUDGET_REACHED = "budget_reached"
    RETRIES_EXHAUSTED = "retries_exhausted"


class EventType(StrEnum):
    USER_MESSAGE = "user.message"
    USER_INTERRUPT = "user.interrupt"
    USER_TOOL_CONFIRMATION = "user.tool_confirmation"
    USER_CUSTOM_TOOL_RESULT = "user.custom_tool_result"
    AGENT_MESSAGE = "agent.message"
    AGENT_THINKING = "agent.thinking"
    AGENT_TOOL_USE = "agent.tool_use"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_ARTIFACT = "agent.artifact"
    SESSION_STATUS_RUNNING = "session.status_running"
    SESSION_STATUS_IDLE = "session.status_idle"
    SESSION_STATUS_RESCHEDULING = "session.status_rescheduling"
    SESSION_STATUS_TERMINATED = "session.status_terminated"
    SESSION_ERROR = "session.error"
    SPAN_MODEL_REQUEST_START = "span.model_request_start"
    SPAN_MODEL_REQUEST_END = "span.model_request_end"


USER_EVENT_TYPES = {
    EventType.USER_MESSAGE,
    EventType.USER_INTERRUPT,
    EventType.USER_TOOL_CONFIRMATION,
    EventType.USER_CUSTOM_TOOL_RESULT,
}


class PermissionMode(StrEnum):
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_ASK = "always_ask"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class EventIn(BaseModel):
    """An event sent to a session by a client."""

    type: EventType
    content: list[TextBlock] = Field(default_factory=list)
    call_hash: str | None = None
    result: Decision | None = None
    deny_message: str | None = None

    def validate_for_send(self) -> None:
        if self.type not in USER_EVENT_TYPES:
            raise ValueError(f"{self.type} is not a client-sendable event type")
        if self.type is EventType.USER_MESSAGE and not self.content:
            raise ValueError("user.message requires content")
        if self.type is EventType.USER_TOOL_CONFIRMATION and not (
            self.call_hash and self.result
        ):
            raise ValueError("user.tool_confirmation requires call_hash and result")


class PermissionRule(BaseModel):
    tool: str
    mode: PermissionMode


class PermissionPolicy(BaseModel):
    default: PermissionMode = PermissionMode.ALWAYS_ASK
    rules: list[PermissionRule] = Field(default_factory=list)

    def mode_for(self, tool_name: str) -> PermissionMode:
        for rule in self.rules:
            if rule.tool == tool_name or fnmatchcase(tool_name, rule.tool):
                return rule.mode
        return self.default


class SessionConfig(BaseModel):
    """Everything the sandbox needs to run one session, resolved by the control plane."""

    session_id: str
    agent_id: str
    agent_version: int
    environment_id: str
    instructions: str | None = None
    model: str
    tools: list[str] = Field(default_factory=list)
    permission_policy: PermissionPolicy = Field(default_factory=PermissionPolicy)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    skill_names: list[str] = Field(default_factory=list)
    session_bucket: str
    sdk_session_id: str | None = None
    budget_usd: float | None = None
    cost_usd: float = 0.0
    max_turns: int | None = None
    disabled: bool = False
