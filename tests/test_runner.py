from unittest.mock import Mock

from naxos import runner


def test_build_options_mounts_only_role_servers(monkeypatch):
    monkeypatch.setattr(
        runner,
        "ROLES",
        {
            "both": {"servers": ["bq", "gcs"], "permission_mode": "default", "max_turns": 5},
            "bq-only": {"servers": ["bq"], "permission_mode": "default", "max_turns": 5},
        },
    )

    assert set(runner.build_options("both", Mock()).mcp_servers) == {"bq", "gcs"}
    assert set(runner.build_options("bq-only", Mock()).mcp_servers) == {"bq"}


def test_build_options_applies_role_config():
    options = runner.build_options("ops", Mock(), resume="s1")

    assert options.permission_mode == runner.ROLES["ops"]["permission_mode"]
    assert options.max_turns == runner.ROLES["ops"]["max_turns"]
    assert options.cwd == str(runner.WS)
    assert options.resume == "s1"


def test_is_disabled_checks_marker_object(monkeypatch):
    monkeypatch.setattr(runner, "BUCKET", "bucket")
    cs = Mock()
    cs.exists.return_value = True

    assert runner.is_disabled(cs, "ops") is True
    assert cs.exists.call_args.args == ("bucket", "disabled/ops")


def test_clear_ws_keeps_claude_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WS", tmp_path)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "leftover.txt").touch()
    (tmp_path / "old-dir").mkdir()

    runner.clear_ws()

    assert (tmp_path / ".claude" / "skills").is_dir()
    assert not (tmp_path / "leftover.txt").exists()
    assert not (tmp_path / "old-dir").exists()


def session_env(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "SESSION_DIR", tmp_path / "proj")
    monkeypatch.setattr(runner, "WS", tmp_path / "ws")
    monkeypatch.setattr(runner, "BUCKET", "bucket")
    (tmp_path / "proj").mkdir()
    (tmp_path / "ws").mkdir()


def test_restore_session_downloads_transcript_and_workspace(monkeypatch, tmp_path):
    session_env(monkeypatch, tmp_path)
    cs = Mock()
    cs.download_prefix.return_value = 0

    runner.restore_session(cs, "ops", "s1")

    assert cs.download_file.call_args.args == (
        "bucket-sessions-ops",
        "s1/transcript.jsonl",
        tmp_path / "proj" / "s1.jsonl",
    )
    assert cs.download_prefix.call_args.args == ("bucket-sessions-ops", "s1/ws/", tmp_path / "ws")


def test_restore_session_skips_transcript_when_local_exists(monkeypatch, tmp_path):
    session_env(monkeypatch, tmp_path)
    (tmp_path / "proj" / "s1.jsonl").touch()
    cs = Mock()
    cs.download_prefix.return_value = 0

    runner.restore_session(cs, "ops", "s1")

    cs.download_file.assert_not_called()


def test_save_session_uploads_transcript_and_workspace(monkeypatch, tmp_path):
    session_env(monkeypatch, tmp_path)
    (tmp_path / "proj" / "s1.jsonl").touch()
    (tmp_path / "ws" / "out").mkdir()
    (tmp_path / "ws" / "out" / "report.md").write_text("x")
    (tmp_path / "ws" / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "ws" / ".claude" / "skills" / "SKILL.md").write_text("x")
    cs = Mock()

    runner.save_session(cs, "ops", "s1")

    uploads = [c.args for c in cs.upload_file.call_args_list]
    assert uploads == [
        ("bucket-sessions-ops", "s1/transcript.jsonl", tmp_path / "proj" / "s1.jsonl"),
        ("bucket-sessions-ops", "s1/ws/out/report.md", tmp_path / "ws" / "out" / "report.md"),
    ]


def test_save_session_survives_missing_transcript(monkeypatch, tmp_path):
    session_env(monkeypatch, tmp_path)
    cs = Mock()

    runner.save_session(cs, "ops", "s1")

    cs.upload_file.assert_not_called()


def test_sync_skills_replaces_dest(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WS", tmp_path)
    monkeypatch.setattr(runner, "BUCKET", "bucket")
    cs = Mock()
    cs.download_prefix.return_value = 1
    dest = tmp_path / ".claude" / "skills"
    stale = dest / "stale-skill"
    stale.mkdir(parents=True)

    runner.sync_skills(cs, ["bigquery", "cloud-storage"])

    assert not stale.exists()
    assert dest.is_dir()
    calls = cs.download_prefix.call_args_list
    assert calls[0].args == ("bucket", "skills/bigquery/", dest / "bigquery")
    assert calls[1].args == ("bucket", "skills/cloud-storage/", dest / "cloud-storage")
