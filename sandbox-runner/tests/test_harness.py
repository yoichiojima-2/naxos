import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from naxos_sbx.harness import STREAM_CHUNK_CHARS, _result_text

from .conftest import make_config, make_harness


def test_main_module_imports():
    import naxos_sbx.main  # noqa: F401


def test_options_passes_tools_as_allowlist():
    options = make_harness(
        make_config(tools=["Bash", "Read"], max_turns=7, effort="high")
    ).options()
    assert options.allowed_tools == ["Bash", "Read"]
    assert options.model == "claude-sonnet-5"
    assert options.max_turns == 7
    assert options.effort == "high"


def test_options_effort_defaults_to_none():
    assert make_harness(make_config()).options().effort is None


def test_options_empty_tools_means_unrestricted():
    assert make_harness(make_config(tools=[])).options().allowed_tools == []
    assert make_harness(make_config()).options().allowed_tools == []


def test_options_mount_skills_as_local_plugin():
    options = make_harness(
        make_config(tools=["Bash"], skill_names=["deploy-helper"]), plugin_dir="/plugin"
    ).options()
    assert options.plugins == [{"type": "local", "path": "/plugin"}]
    assert options.skills == ["naxos:deploy-helper"]
    assert options.allowed_tools == ["Bash"]


def test_options_always_isolate_filesystem_settings():
    # setting_sources=[] is the governance guarantee: agent-writable workspace
    # settings (hooks, permissions) are never loaded, skills or not.
    assert make_harness(make_config(tools=["Bash"])).options().setting_sources == []
    with_skills = make_harness(make_config(skill_names=["x"]), plugin_dir="/plugin").options()
    assert with_skills.setting_sources == []


def test_options_without_skills_load_no_plugin():
    options = make_harness(make_config(tools=["Bash"]), plugin_dir="/plugin").options()
    assert options.plugins == []
    assert options.skills is None


def test_cost_accumulates_on_top_of_prior_bursts():
    harness = make_harness(make_config(cost_usd=5.0))
    harness._accumulate_cost(0.3)
    assert harness.cost_usd == 5.3
    harness._accumulate_cost(None)
    assert harness.cost_usd == 5.3


class _Channel:
    def __init__(self, verdict):
        self.session_id = "session_x"
        self.run_id = "run_x"
        self.verdict = verdict
        self.emitted = []

    async def ask_permission(self, call_hash, tool_name, tool_input, tool_use_id):
        return self.verdict

    async def emit(self, events, run_id):
        self.emitted.extend(events)


@pytest.mark.parametrize(
    ("verdict", "label", "hook_decision"),
    [
        ({"decision": "allow", "by": "policy"}, "auto_allowed", "allow"),
        ({"decision": "allow", "by": "user"}, "user_allowed", "allow"),
        ({"decision": "deny", "by": "user", "reason": "no"}, "user_denied", "deny"),
        # Outside the agent version's tools list: denied by the control plane, not a human.
        ({"decision": "deny", "by": "policy", "reason": "not yours"}, "not_allowed", "deny"),
        ({"decision": "deny", "reason": "agent disabled", "killed": True}, "killed", "deny"),
    ],
)
async def test_pre_tool_use_labels_every_decision(verdict, label, hook_decision):
    """The fallback derivation, for a control plane rolled back to before it
    labelled its own decisions."""
    channel = _Channel(verdict)
    harness = make_harness(make_config(), channel)
    result = await harness._pre_tool_use({"tool_name": "Bash", "tool_input": {}}, "tu_1", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == hook_decision
    assert harness.pending[-1]["payload"]["decision"] == label
    assert harness.killed is (label == "killed")


async def test_pre_tool_use_uses_the_gates_own_label():
    """The gate decided and recorded it; re-deriving here would let the timeline
    drift from the audit record."""
    channel = _Channel(
        {"decision": "allow", "by": "policy", "label": "user_allowed", "tool_call_id": "42"}
    )
    harness = make_harness(make_config(), channel)
    await harness._pre_tool_use({"tool_name": "Bash", "tool_input": {}}, "tu_1", None)
    assert harness.pending[-1]["payload"]["decision"] == "user_allowed"
    assert harness.pending[-1]["payload"]["tool_call_id"] == "42"


def test_usage_tokens_accumulate_across_model_responses():
    harness = make_harness()
    harness._accumulate_usage({"input_tokens": 100, "output_tokens": 20})
    harness._accumulate_usage({"input_tokens": 50, "output_tokens": 5})
    assert (harness.input_tokens, harness.output_tokens) == (150, 25)


def test_usage_tokens_ignore_cache_counters_and_missing_usage():
    """cache_read/creation are deliberately not folded in: the audited number has
    to mean what its name says. cost_usd already carries the billed spend."""
    harness = make_harness()
    harness._accumulate_usage(None)
    harness._accumulate_usage({"cache_read_input_tokens": 9000, "output_tokens": 7})
    assert (harness.input_tokens, harness.output_tokens) == (0, 7)


async def test_pre_tool_use_pending_pauses_the_call():
    channel = _Channel({"decision": "pending"})
    harness = make_harness(make_config(), channel)
    result = await harness._pre_tool_use({"tool_name": "Bash", "tool_input": {}}, "tu_1", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert harness.paused_call is not None
    assert channel.emitted[-1]["payload"]["decision"] == "awaiting_confirmation"


def test_options_stream_partial_messages():
    assert make_harness(make_config()).options().include_partial_messages is True


class _StreamChannel:
    def __init__(self, fail: bool = False):
        self.session_id = "session_x"
        self.run_id = "run-test"
        self.fail = fail
        self.deltas = []

    async def emit_stream(self, delta):
        if self.fail:
            raise RuntimeError("stream endpoint down")
        self.deltas.append(delta)


def _block_start(block_type="text"):
    return {"type": "content_block_start", "content_block": {"type": block_type}}


def _text_delta(text):
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}


async def test_stream_deltas_relay_and_close_in_order():
    channel = _StreamChannel()
    harness = make_harness(make_config(), channel)
    await harness._on_stream_event(_block_start())
    await harness._on_stream_event(_text_delta("hel"))
    await harness._on_stream_event({"type": "content_block_stop"})
    assert "".join(d["text"] for d in channel.deltas) == "hel"
    assert all(d["stream"] == channel.deltas[0]["stream"] for d in channel.deltas)
    assert harness._closed_streams == [channel.deltas[0]["stream"]]


async def test_stream_chunks_stay_under_the_notify_cap():
    channel = _StreamChannel()
    harness = make_harness(make_config(), channel)
    await harness._on_stream_event(_block_start())
    await harness._on_stream_event(_text_delta("x" * (STREAM_CHUNK_CHARS * 2 + 10)))
    await harness._on_stream_event({"type": "content_block_stop"})
    assert all(len(d["text"]) <= STREAM_CHUNK_CHARS for d in channel.deltas)
    assert sum(len(d["text"]) for d in channel.deltas) == STREAM_CHUNK_CHARS * 2 + 10


async def test_stream_ignores_non_text_blocks():
    channel = _StreamChannel()
    harness = make_harness(make_config(), channel)
    await harness._on_stream_event(_block_start("tool_use"))
    await harness._on_stream_event(_text_delta("ignored"))
    await harness._on_stream_event({"type": "content_block_stop"})
    assert channel.deltas == []
    assert harness._closed_streams == []


async def test_stream_failure_falls_back_without_raising():
    channel = _StreamChannel(fail=True)
    harness = make_harness(make_config(), channel)
    await harness._on_stream_event(_block_start())
    await harness._on_stream_event(_text_delta("boom"))
    assert harness._stream_broken is True
    # The closed stream id still tags the persisted message.
    await harness._on_stream_event({"type": "content_block_stop"})
    assert len(harness._closed_streams) == 1


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages

    async def receive_response(self):
        for message in self.messages:
            yield message


class _DrainChannel(_StreamChannel):
    def __init__(self):
        super().__init__()
        self.emitted = []

    async def emit(self, events, run_id):
        self.emitted.extend(events)


async def test_drain_streams_then_supersedes_with_the_persisted_message():
    channel = _DrainChannel()
    harness = make_harness(make_config(), channel)
    stream = [
        StreamEvent(uuid="u1", session_id="s", event=_block_start()),
        StreamEvent(uuid="u2", session_id="s", event=_text_delta("hel")),
        StreamEvent(uuid="u3", session_id="s", event=_text_delta("lo")),
        StreamEvent(uuid="u4", session_id="s", event={"type": "content_block_stop"}),
    ]
    messages = [
        *stream,
        AssistantMessage(content=[TextBlock(text="hello")], model="claude-sonnet-5"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            total_cost_usd=0.1,
        ),
    ]
    await harness._drain(_FakeClient(messages))

    assert "".join(d["text"] for d in channel.deltas) == "hello"
    message = next(e for e in channel.emitted if e["type"] == "agent.message")
    assert message["payload"]["text"] == "hello"
    assert message["payload"]["stream"] == channel.deltas[0]["stream"]
    types = [e["type"] for e in channel.emitted]
    assert types.index("agent.message") < types.index("span.model_request_end")


def test_result_text_unwraps_content_blocks():
    content = [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
    assert _result_text(content) == "first\n\nsecond"


def test_result_text_passes_plain_strings_through():
    assert _result_text("already text") == "already text"
    assert _result_text(None) == ""


def test_result_text_truncates_to_the_event_size_cap():
    assert len(_result_text([{"type": "text", "text": "x" * 5000}])) == 4000
