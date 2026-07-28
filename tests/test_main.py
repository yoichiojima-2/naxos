import sys
from unittest.mock import Mock

from src import main


def test_build_options_mounts_only_role_servers():
    assert set(main.build_options("ops").mcp_servers) == {"bq", "gcs"}
    assert set(main.build_options("analyst").mcp_servers) == {"bq"}


def test_build_options_applies_role_config():
    options = main.build_options("ops")

    assert options.permission_mode == main.ROLES["ops"]["permission_mode"]
    assert options.max_turns == main.ROLES["ops"]["max_turns"]
    assert options.cwd == str(main.WS)


def test_parse_args_role_defaults_to_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main", "do something"])
    monkeypatch.setenv("ROLE", "analyst")

    args = main.parse_args()

    assert args.prompt == "do something"
    assert args.role == "analyst"


def test_sync_skills_replaces_dest(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WS", tmp_path)
    monkeypatch.setattr(main, "BUCKET", "bucket")
    cs_cls = Mock()
    cs_cls.return_value.download_prefix.return_value = 1
    monkeypatch.setattr(main, "CloudStorage", cs_cls)
    dest = tmp_path / ".claude" / "skills"
    stale = dest / "stale-skill"
    stale.mkdir(parents=True)

    main.sync_skills(["bigquery", "cloud-storage"])

    assert not stale.exists()
    assert dest.is_dir()
    calls = cs_cls.return_value.download_prefix.call_args_list
    assert calls[0].args == ("bucket", "skills/bigquery/", dest / "bigquery")
    assert calls[1].args == ("bucket", "skills/cloud-storage/", dest / "cloud-storage")
