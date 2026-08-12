from types import SimpleNamespace

from naxos_shared.events import SessionConfig

from naxos_sbx.harness import Harness


def make_config(**overrides) -> SessionConfig:
    base = {
        "session_id": "session_x",
        "agent_id": "agent_x",
        "agent_version": 1,
        "environment_id": "env_x",
        "model": "claude-sonnet-5",
        "session_bucket": "bucket-x",
    }
    return SessionConfig.model_validate({**base, **overrides})


def make_harness(
    config: SessionConfig | None = None, channel=None, plugin_dir: str | None = None
) -> Harness:
    return Harness(
        channel or SimpleNamespace(session_id="session_x", run_id="run_x"),
        config if config is not None else make_config(),
        "/tmp",
        plugin_dir=plugin_dir,
    )
