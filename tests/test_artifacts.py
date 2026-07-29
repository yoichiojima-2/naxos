from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from naxos.artifacts import Artifacts, browse


def make_artifacts(tmp_path) -> tuple[Artifacts, Mock]:
    cs = Mock()
    return Artifacts("ops", "artifacts-bucket", tmp_path, cs), cs


def test_publish_file(tmp_path):
    artifacts, cs = make_artifacts(tmp_path)
    (tmp_path / "report.html").write_text("<h1>report</h1>")
    date = datetime.now(UTC).date().isoformat()

    url = artifacts.publish("report.html", "cost-report")

    assert cs.upload_file.call_args.args == (
        "artifacts-bucket",
        f"ops/{date}-cost-report/report.html",
        tmp_path / "report.html",
    )
    assert url == f"https://storage.cloud.google.com/artifacts-bucket/ops/{date}-cost-report/report.html"


def test_publish_directory_points_at_index(tmp_path):
    artifacts, cs = make_artifacts(tmp_path)
    deck = tmp_path / "deck"
    (deck / "assets").mkdir(parents=True)
    (deck / "index.html").write_text("<h1>deck</h1>")
    (deck / "assets" / "style.css").write_text("body{}")
    date = datetime.now(UTC).date().isoformat()

    url = artifacts.publish("deck", "q3-review")

    uploaded = {c.args[1] for c in cs.upload_file.call_args_list}
    assert uploaded == {f"ops/{date}-q3-review/index.html", f"ops/{date}-q3-review/assets/style.css"}
    assert url.endswith("/index.html")


def test_publish_rejects_paths_outside_workspace(tmp_path):
    artifacts, cs = make_artifacts(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    (tmp_path / "secret.txt").write_text("x")

    with pytest.raises(ValueError):
        artifacts.publish("../secret.txt", "leak")

    cs.upload_file.assert_not_called()


def test_publish_missing_path(tmp_path):
    artifacts, _ = make_artifacts(tmp_path)

    with pytest.raises(FileNotFoundError):
        artifacts.publish("nope.html", "x")


def test_browse_groups_objects_into_artifacts():
    cs = Mock()
    cs.list_all.return_value = [
        "ops/2026-07-20-cost-report/report.html",
        "analyst/2026-07-28-q3-review/index.html",
        "analyst/2026-07-28-q3-review/assets/style.css",
        "ops/2026-07-28-raw-dump/a.csv",
        "ops/2026-07-28-raw-dump/b.csv",
    ]

    result = browse(cs, "artifacts-bucket")

    assert cs.list_all.call_args.args == ("artifacts-bucket",)
    assert result == [
        {
            "role": "analyst",
            "date": "2026-07-28",
            "title": "q3-review",
            "url": "https://storage.cloud.google.com/artifacts-bucket/analyst/2026-07-28-q3-review/index.html",
            "files": 2,
        },
        {
            "role": "ops",
            "date": "2026-07-28",
            "title": "raw-dump",
            "url": "https://storage.cloud.google.com/artifacts-bucket/ops/2026-07-28-raw-dump",
            "files": 2,
        },
        {
            "role": "ops",
            "date": "2026-07-20",
            "title": "cost-report",
            "url": "https://storage.cloud.google.com/artifacts-bucket/ops/2026-07-20-cost-report/report.html",
            "files": 1,
        },
    ]


def test_browse_skips_stray_top_level_objects():
    cs = Mock()
    cs.list_all.return_value = ["README.txt", "ops/2026-07-28-report/report.html"]

    result = browse(cs, "artifacts-bucket")

    assert [a["title"] for a in result] == ["report"]
