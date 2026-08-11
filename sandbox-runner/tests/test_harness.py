from types import SimpleNamespace

import pytest
from naxos_shared.events import SessionConfig

from naxos_sbx.harness import Harness


def _config(**overrides) -> SessionConfig:
    base = {
        "session_id": "session_x",
        "agent_id": "agent_x",
        "agent_version": 1,
        "environment_id": "env_x",
        "model": "claude-sonnet-5",
        "session_bucket": "bucket-x",
    }
    return SessionConfig.model_validate({**base, **overrides})


def _harness(config: SessionConfig, channel=None, plugin_dir=None) -> Harness:
    return Harness(
        channel or SimpleNamespace(session_id="session_x"), config, "/tmp", plugin_dir=plugin_dir
    )


def test_main_module_imports():
    import naxos_sbx.main  # noqa: F401


def test_options_passes_tools_as_allowlist():
    options = _harness(_config(tools=["Bash", "Read"], max_turns=7)).options()
    assert options.allowed_tools == ["Bash", "Read"]
    assert options.model == "claude-sonnet-5"
    assert options.max_turns == 7


def test_options_empty_tools_means_unrestricted():
    assert _harness(_config(tools=[])).options().allowed_tools == []
    assert _harness(_config()).options().allowed_tools == []


def test_options_mount_skills_as_local_plugin():
    options = _harness(
        _config(tools=["Bash"], skill_names=["deploy-helper"]), plugin_dir="/plugin"
    ).options()
    assert options.plugins == [{"type": "local", "path": "/plugin"}]
    assert options.skills == ["naxos:deploy-helper"]
    assert options.allowed_tools == ["Bash"]


def test_options_always_isolate_filesystem_settings():
    # setting_sources=[] is the governance guarantee: agent-writable workspace
    # settings (hooks, permissions) are never loaded, skills or not.
    assert _harness(_config(tools=["Bash"])).options().setting_sources == []
    with_skills = _harness(_config(skill_names=["x"]), plugin_dir="/plugin").options()
    assert with_skills.setting_sources == []


def test_options_without_skills_load_no_plugin():
    options = _harness(_config(tools=["Bash"]), plugin_dir="/plugin").options()
    assert options.plugins == []
    assert options.skills is None


def test_cost_accumulates_on_top_of_prior_bursts():
    harness = _harness(_config(cost_usd=5.0))
    harness._accumulate_cost(0.3)
    assert harness.cost_usd == 5.3
    harness._accumulate_cost(None)
    assert harness.cost_usd == 5.3


class _Channel:
    def __init__(self, verdict):
        self.session_id = "session_x"
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
        ({"decision": "deny", "reason": "agent disabled", "killed": True}, "killed", "deny"),
    ],
)
async def test_pre_tool_use_labels_every_decision(verdict, label, hook_decision):
    channel = _Channel(verdict)
    harness = _harness(_config(), channel)
    result = await harness._pre_tool_use({"tool_name": "Bash", "tool_input": {}}, "tu_1", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == hook_decision
    assert harness.pending[-1]["payload"]["decision"] == label
    assert harness.killed is (label == "killed")


async def test_pre_tool_use_pending_pauses_the_call():
    channel = _Channel({"decision": "pending"})
    harness = _harness(_config(), channel)
    result = await harness._pre_tool_use({"tool_name": "Bash", "tool_input": {}}, "tu_1", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert harness.paused_call is not None
    assert channel.emitted[-1]["payload"]["decision"] == "awaiting_confirmation"
