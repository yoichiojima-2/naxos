import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock, call

import pytest

from src.gcs import CloudStorage


def make_cs() -> CloudStorage:
    cs = CloudStorage(project="test-project")
    cs.client = Mock()
    return cs


def test_client_omits_project_when_unset(monkeypatch):
    client_cls = Mock()
    monkeypatch.setattr("src.gcs.storage.Client", client_cls)

    clients = [CloudStorage(project=None).client, CloudStorage(project="p").client]

    assert clients == [client_cls.return_value, client_cls.return_value]
    assert client_cls.call_args_list == [call(), call(project="p")]


def test_list_objects_caps_results():
    cs = make_cs()
    cs.client.list_blobs.return_value = [Mock(), Mock()]
    cs.client.list_blobs.return_value[0].name = "a.txt"
    cs.client.list_blobs.return_value[1].name = "b.txt"

    assert cs.list_objects("bucket", prefix="logs/") == ["a.txt", "b.txt"]
    assert cs.client.list_blobs.call_args.kwargs["max_results"] == cs.max_list_results


def test_read_text_small_object():
    cs = make_cs()
    blob = Mock(size=5)
    blob.download_as_bytes.return_value = b"hello"
    cs.client.bucket.return_value.get_blob.return_value = blob

    assert cs.read_text("bucket", "a.txt") == "hello"
    assert blob.download_as_bytes.call_args.kwargs == {"start": 0, "end": cs.max_read_bytes - 1}


def test_read_text_marks_truncation():
    cs = make_cs()
    blob = Mock(size=cs.max_read_bytes * 2)
    blob.download_as_bytes.return_value = b"x" * 10
    cs.client.bucket.return_value.get_blob.return_value = blob

    text = cs.read_text("bucket", "big.txt")

    assert f"...[truncated: showing {cs.max_read_bytes} of {blob.size} bytes]" in text


def test_read_text_missing_object():
    cs = make_cs()
    cs.client.bucket.return_value.get_blob.return_value = None

    with pytest.raises(FileNotFoundError):
        cs.read_text("bucket", "missing.txt")


def test_get_object_info():
    cs = make_cs()
    blob = Mock(
        size=42,
        content_type="text/plain",
        updated=datetime(2026, 7, 1, tzinfo=UTC),
        storage_class="STANDARD",
    )
    cs.client.bucket.return_value.get_blob.return_value = blob

    info = cs.get_object_info("bucket", "a.txt")

    assert info["uri"] == "gs://bucket/a.txt"
    assert info["size_bytes"] == 42


def test_download_prefix_skips_prefix_itself(tmp_path):
    cs = make_cs()
    blobs = [Mock(), Mock(), Mock()]
    blobs[0].name = "skills/bigquery/"
    blobs[1].name = "skills/bigquery/SKILL.md"
    blobs[2].name = "skills/bigquery/examples/queries.md"
    cs.client.list_blobs.return_value = blobs

    count = cs.download_prefix("bucket", "skills/bigquery/", tmp_path)

    assert count == 2
    blobs[0].download_to_filename.assert_not_called()
    blobs[1].download_to_filename.assert_called_once_with(tmp_path / "SKILL.md")
    blobs[2].download_to_filename.assert_called_once_with(tmp_path / "examples" / "queries.md")
    assert (tmp_path / "examples").is_dir()


def test_exists():
    cs = make_cs()
    cs.client.bucket.return_value.blob.return_value.exists.return_value = True

    assert cs.exists("bucket", "disabled/ops") is True
    cs.client.bucket.return_value.blob.assert_called_once_with("disabled/ops")


def test_tools_are_read_only():
    names = {t.name for t in make_cs().tools()}

    assert names == {"list_gcs_objects", "get_gcs_object_info", "read_gcs_object"}


def test_tool_returns_error_as_result():
    cs = make_cs()
    cs.client.bucket.return_value.get_blob.return_value = None
    read_tool = next(t for t in cs.tools() if t.name == "read_gcs_object")

    result = asyncio.run(read_tool.handler({"bucket": "b", "path": "missing.txt"}))

    assert "Failed: gs://b/missing.txt not found" in result["content"][0]["text"]
