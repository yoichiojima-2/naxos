import sys

from naxos import cli
from naxos.agent import AgentRun


def test_parse_args_role_defaults_to_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli", "do something"])
    monkeypatch.setenv("ROLE", "analyst")

    args = cli.parse_args()

    assert args.prompt == "do something"
    assert args.role == "analyst"
    assert args.resume is None


def test_slack_message_has_footer():
    run = AgentRun(text="all good", session_id="s1", cost_usd=0.0263)

    message = cli.slack_message("ops", run)

    assert message.startswith("[ops] all good\n---\n")
    assert "cost $0.0263" in message
    assert "session s1" in message


def test_slack_message_truncates_long_text():
    run = AgentRun(text="x" * 5000, session_id="s1", cost_usd=0.1)

    message = cli.slack_message("ops", run)

    assert "x" * 3000 + "…" in message
    assert "x" * 3001 not in message
