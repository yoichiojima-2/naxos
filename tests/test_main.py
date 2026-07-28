import sys
from unittest.mock import Mock

from src import main


def test_build_options_mounts_only_role_servers(monkeypatch):
    monkeypatch.setattr(
        main,
        "ROLES",
        {
            "both": {"servers": ["bq", "gcs"], "permission_mode": "default", "max_turns": 5},
            "bq-only": {"servers": ["bq"], "permission_mode": "default", "max_turns": 5},
        },
    )

    assert set(main.build_options("both", Mock()).mcp_servers) == {"bq", "gcs"}
    assert set(main.build_options("bq-only", Mock()).mcp_servers) == {"bq"}


def test_build_options_applies_role_config():
    options = main.build_options("ops", Mock(), resume="s1")

    assert options.permission_mode == main.ROLES["ops"]["permission_mode"]
    assert options.max_turns == main.ROLES["ops"]["max_turns"]
    assert options.cwd == str(main.WS)
    assert options.resume == "s1"


def test_parse_args_role_defaults_to_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main", "do something"])
    monkeypatch.setenv("ROLE", "analyst")

    args = main.parse_args()

    assert args.prompt == "do something"
    assert args.role == "analyst"
    assert args.resume is None


def test_is_disabled_checks_marker_object(monkeypatch):
    monkeypatch.setattr(main, "BUCKET", "bucket")
    cs = Mock()
    cs.exists.return_value = True

    assert main.is_disabled(cs, "ops") is True
    assert cs.exists.call_args.args == ("bucket", "disabled/ops")


def test_slack_message_has_footer():
    run = main.AgentRun(text="all good", session_id="s1", cost_usd=0.0263)

    message = main.slack_message("ops", run)

    assert message.startswith("[ops] all good\n---\n")
    assert "cost $0.0263" in message
    assert "session s1" in message


def test_slack_message_truncates_long_text():
    run = main.AgentRun(text="x" * 5000, session_id="s1", cost_usd=0.1)

    message = main.slack_message("ops", run)

    assert "x" * 3000 + "…" in message
    assert "x" * 3001 not in message


def test_restore_session_skips_when_local_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SESSION_DIR", tmp_path)
    (tmp_path / "s1.jsonl").touch()
    cs = Mock()

    main.restore_session(cs, "ops", "s1")

    cs.download_file.assert_not_called()


def test_restore_session_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(main, "BUCKET", "bucket")
    cs = Mock()

    main.restore_session(cs, "ops", "s1")

    assert cs.download_file.call_args.args == ("bucket", "sessions/ops/s1.jsonl", tmp_path / "s1.jsonl")


def test_save_session_uploads(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(main, "BUCKET", "bucket")
    (tmp_path / "s1.jsonl").touch()
    cs = Mock()

    main.save_session(cs, "ops", "s1")

    assert cs.upload_file.call_args.args == ("bucket", "sessions/ops/s1.jsonl", tmp_path / "s1.jsonl")


def test_save_session_skips_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SESSION_DIR", tmp_path)
    cs = Mock()

    main.save_session(cs, "ops", "s1")

    cs.upload_file.assert_not_called()


def test_sync_skills_replaces_dest(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WS", tmp_path)
    monkeypatch.setattr(main, "BUCKET", "bucket")
    cs = Mock()
    cs.download_prefix.return_value = 1
    dest = tmp_path / ".claude" / "skills"
    stale = dest / "stale-skill"
    stale.mkdir(parents=True)

    main.sync_skills(cs, ["bigquery", "cloud-storage"])

    assert not stale.exists()
    assert dest.is_dir()
    calls = cs.download_prefix.call_args_list
    assert calls[0].args == ("bucket", "skills/bigquery/", dest / "bigquery")
    assert calls[1].args == ("bucket", "skills/cloud-storage/", dest / "cloud-storage")
