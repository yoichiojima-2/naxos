import json

import pytest
from naxos_shared.events import SessionConfig

from naxos_sbx import artifacts
from naxos_sbx.artifacts import ArtifactTools


def _config() -> SessionConfig:
    return SessionConfig.model_validate(
        {
            "session_id": "session_x",
            "agent_id": "agent_x",
            "agent_version": 1,
            "environment_id": "env_x",
            "model": "claude-sonnet-5",
            "session_bucket": "bucket-x",
        }
    )


class _Channel:
    def __init__(self):
        self.session_id = "session_x"
        self.registered = []
        self.deleted = []
        self.shared = []

    async def register_artifact(self, name, content_type, size_bytes, description):
        self.registered.append((name, content_type, size_bytes, description))
        return {"id": "art_1", "name": name, "version": len(self.registered)}

    async def list_artifacts(self):
        return {
            "data": [
                {
                    "name": "report.md",
                    "description": None,
                    "version": 2,
                    "size_bytes": 10,
                    "share_url": "/v1/artifacts/shared/tok",
                }
            ]
        }

    async def delete_artifact(self, name):
        self.deleted.append(name)
        return {"deleted": True}

    async def share_artifact(self, name, shared):
        self.shared.append((name, shared))
        return {"name": name, "share_url": "/v1/artifacts/shared/tok" if shared else None}


@pytest.fixture
def uploads(monkeypatch):
    calls = []

    async def fake_upload(bucket, path, source, content_type):
        calls.append((bucket, path, source.read_bytes(), content_type))

    async def fake_delete(bucket, path):
        calls.append(("deleted", bucket, path))

    monkeypatch.setattr(artifacts, "_upload_blob", fake_upload)
    monkeypatch.setattr(artifacts, "_delete_blob", fake_delete)
    return calls


@pytest.fixture
def tools(tmp_path, uploads):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.md").write_text("# findings")
    return ArtifactTools(_Channel(), _config(), tmp_path)


async def test_create_uploads_and_registers(tools, uploads):
    result = await tools.create({"path": "out/report.md", "description": "weekly"})
    assert "Published artifact 'report.md'" in result["content"][0]["text"]
    bucket, path, body, content_type = uploads[0]
    assert (bucket, path) == ("bucket-x", "sessions/session_x/artifacts/report.md")
    assert body == b"# findings"
    assert content_type == "text/markdown"
    assert tools.channel.registered == [("report.md", "text/markdown", 10, "weekly")]


async def test_create_honors_custom_name(tools, uploads):
    await tools.create({"path": "out/report.md", "name": "summary.md"})
    assert uploads[0][1] == "sessions/session_x/artifacts/summary.md"


async def test_create_rejects_escape_and_missing_files(tools, uploads):
    escaped = await tools.create({"path": "../secrets.txt"})
    assert escaped.get("is_error")
    missing = await tools.create({"path": "out/nope.md"})
    assert missing.get("is_error")
    assert uploads == []


async def test_create_rejects_oversize(tools, uploads, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 5)
    result = await tools.create({"path": "out/report.md"})
    assert result.get("is_error")
    assert uploads == []


async def test_list_returns_summaries(tools):
    result = await tools.list({})
    assert json.loads(result["content"][0]["text"])[0]["name"] == "report.md"


async def test_delete_removes_blob_and_row(tools, uploads):
    await tools.delete({"name": "report.md"})
    assert tools.channel.deleted == ["report.md"]
    assert uploads == [("deleted", "bucket-x", "sessions/session_x/artifacts/report.md")]


async def test_share_and_unshare(tools):
    shared = await tools.share({"name": "report.md"})
    assert "/v1/artifacts/shared/tok" in shared["content"][0]["text"]
    await tools.unshare({"name": "report.md"})
    assert tools.channel.shared == [("report.md", True), ("report.md", False)]


def test_harness_exposes_artifact_server():
    from types import SimpleNamespace

    from naxos_sbx.harness import Harness

    harness = Harness(SimpleNamespace(session_id="session_x"), _config(), "/tmp")
    options = harness.options()
    assert "artifacts" in options.mcp_servers
