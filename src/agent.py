import logging
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    query,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentRun:
    """Collected result of one agent run."""

    text: str = ""
    texts: list[str] = field(default_factory=list)
    thinkings: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None
    usage: dict | None = None
    is_error: bool = False


async def run_agent(prompt: str, options: ClaudeAgentOptions, echo: bool = False) -> AgentRun:
    """Run one agent task to completion and return the collected result.

    With echo=True, blocks are printed as they arrive.
    """
    run = AgentRun()
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    run.tool_calls.append({"name": block.name, "input": block.input})
                    if echo:
                        print(f"[tool] {block.name}: {block.input}")
                elif isinstance(block, TextBlock):
                    run.texts.append(block.text)
                    if echo:
                        print(block.text)
                elif isinstance(block, ThinkingBlock):
                    run.thinkings.append(block.thinking)
                    if echo and block.thinking:
                        print(f"[thinking] {block.thinking}")
        elif isinstance(message, ResultMessage):
            run.text = message.result or (run.texts[-1] if run.texts else "")
            run.cost_usd = message.total_cost_usd
            run.num_turns = message.num_turns
            run.usage = message.usage
            run.is_error = message.is_error
            logger.info(f"run complete: cost_usd={run.cost_usd} turns={run.num_turns} error={run.is_error}")
    return run
