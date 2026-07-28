import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from src.agent import run_agent


def result_message(**overrides) -> ResultMessage:
    defaults = {
        "subtype": "success",
        "duration_ms": 1000,
        "duration_api_ms": 800,
        "is_error": False,
        "num_turns": 2,
        "session_id": "s1",
        "total_cost_usd": 0.05,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "result": "final answer",
    }
    return ResultMessage(**{**defaults, **overrides})


def patch_query(monkeypatch, messages):
    async def fake_query(prompt, options):
        for message in messages:
            yield message

    monkeypatch.setattr("src.agent.query", fake_query)


def test_run_agent_collects_everything(monkeypatch):
    patch_query(
        monkeypatch,
        [
            AssistantMessage(
                content=[
                    ThinkingBlock(thinking="let me check", signature=""),
                    ToolUseBlock(id="t1", name="query_bigquery", input={"sql": "select 1"}),
                    TextBlock(text="the answer is 1"),
                ],
                model="claude-x",
            ),
            result_message(),
        ],
    )

    run = asyncio.run(run_agent("prompt", ClaudeAgentOptions()))

    assert run.text == "final answer"
    assert run.session_id == "s1"
    assert run.texts == ["the answer is 1"]
    assert run.thinkings == ["let me check"]
    assert run.tool_calls == [{"name": "query_bigquery", "input": {"sql": "select 1"}}]
    assert run.cost_usd == 0.05
    assert run.num_turns == 2
    assert run.is_error is False


def test_run_agent_falls_back_to_last_text(monkeypatch):
    patch_query(
        monkeypatch,
        [
            AssistantMessage(content=[TextBlock(text="only text")], model="claude-x"),
            result_message(result=None),
        ],
    )

    run = asyncio.run(run_agent("prompt", ClaudeAgentOptions()))

    assert run.text == "only text"


def test_run_agent_reports_error(monkeypatch):
    patch_query(monkeypatch, [result_message(subtype="error", is_error=True, result=None)])

    run = asyncio.run(run_agent("prompt", ClaudeAgentOptions()))

    assert run.is_error is True
    assert run.text == ""
