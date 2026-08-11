from types import SimpleNamespace

from naxos_sbx.harness import Harness


def _harness(config: dict) -> Harness:
    return Harness(SimpleNamespace(session_id="session_x"), config, "/tmp")


def test_options_passes_tools_as_allowlist():
    options = _harness(
        {"tools": ["Bash", "Read"], "model": "claude-sonnet-5", "max_turns": 7}
    ).options()
    assert options.allowed_tools == ["Bash", "Read"]
    assert options.model == "claude-sonnet-5"
    assert options.max_turns == 7


def test_options_empty_tools_means_unrestricted():
    assert _harness({"tools": []}).options().allowed_tools == []
    assert _harness({}).options().allowed_tools == []
