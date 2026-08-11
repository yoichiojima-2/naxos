import pytest

from naxos_cp import gcs

from .test_session_flow import make_agent

MESSAGE = {"type": "user.message", "content": [{"type": "text", "text": "make a report"}]}


@pytest.fixture(autouse=True)
def blobs(monkeypatch):
    """In-memory stand-in for the session bucket."""
    blobs: dict[tuple[str, str], bytes] = {}

    async def fake_download(bucket: str, path: str) -> bytes | None:
        return blobs.get((bucket, path))

    async def fake_delete(bucket: str, path: str) -> None:
        blobs.pop((bucket, path), None)

    monkeypatch.setattr(gcs, "download", fake_download)
    monkeypatch.setattr(gcs, "delete", fake_delete)
    return blobs


async def make_session(client):
    _, agent = await make_agent(client)
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    return agent, session


async def register(internal_client, session_id, **overrides):
    body = {"name": "report.md", "content_type": "text/markdown", "size_bytes": 42, **overrides}
    return await internal_client.post(f"/internal/sessions/{session_id}/artifacts", json=body)


async def test_register_lists_and_bumps_versions(client, internal_client, launched):
    agent, session = await make_session(client)

    created = (await register(internal_client, session["id"], description="daily report")).json()
    assert created["version"] == 1
    assert created["created_by"] == f"agent:{session['id']}"

    updated = (await register(internal_client, session["id"], size_bytes=99)).json()
    assert updated["id"] == created["id"]
    assert updated["version"] == 2
    assert updated["description"] == "daily report"

    listed = (await client.get("/v1/artifacts", params={"session_id": session["id"]})).json()
    assert [a["name"] for a in listed["data"]] == ["report.md"]
    by_agent = (await client.get("/v1/artifacts", params={"agent_id": agent["id"]})).json()
    assert len(by_agent["data"]) == 1

    got = (await client.get(f"/v1/artifacts/{created['id']}")).json()
    assert got["size_bytes"] == 99
    assert "session_bucket" not in got

    events = (await client.get(f"/v1/sessions/{session['id']}/events")).json()["data"]
    actions = [e["payload"]["action"] for e in events if e["type"] == "agent.artifact"]
    assert actions == ["created", "updated"]


async def test_agent_share_round_trip(client, internal_client, launched, blobs):
    _, session = await make_session(client)
    created = (await register(internal_client, session["id"])).json()
    blobs[("naxos2-sess-default", f"sessions/{session['id']}/artifacts/report.md")] = b"# hi"

    shared = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/artifacts/share",
            json={"name": "report.md", "shared": True},
        )
    ).json()
    token = shared["share_token"]
    assert shared["share_url"].endswith(f"/#artifacts/shared/{token}")

    again = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/artifacts/share",
            json={"name": "report.md", "shared": True},
        )
    ).json()
    assert again["share_token"] == token

    meta = (await client.get(f"/v1/artifacts/shared/{token}")).json()
    assert meta["id"] == created["id"]
    content = await client.get(f"/v1/artifacts/shared/{token}/content")
    assert content.status_code == 200
    assert content.content == b"# hi"
    assert content.headers["content-type"].startswith("text/markdown")

    unshared = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/artifacts/share",
            json={"name": "report.md", "shared": False},
        )
    ).json()
    assert unshared["share_token"] is None
    assert (await client.get(f"/v1/artifacts/shared/{token}")).status_code == 404

    events = (await client.get(f"/v1/sessions/{session['id']}/events")).json()["data"]
    actions = [e["payload"]["action"] for e in events if e["type"] == "agent.artifact"]
    assert actions == ["created", "shared", "shared", "unshared"]


async def test_user_share_and_unshare(client, internal_client, launched):
    _, session = await make_session(client)
    created = (await register(internal_client, session["id"])).json()

    shared = (await client.post(f"/v1/artifacts/{created['id']}/share")).json()
    assert shared["share_token"]
    again = (await client.post(f"/v1/artifacts/{created['id']}/share")).json()
    assert again["share_token"] == shared["share_token"]

    unshared = (await client.delete(f"/v1/artifacts/{created['id']}/share")).json()
    assert unshared["share_token"] is None


async def test_user_download_patch_and_delete(client, internal_client, launched, blobs):
    _, session = await make_session(client)
    created = (await register(internal_client, session["id"])).json()
    key = ("naxos2-sess-default", f"sessions/{session['id']}/artifacts/report.md")
    blobs[key] = b"body"

    content = await client.get(f"/v1/artifacts/{created['id']}/content")
    assert content.content == b"body"
    assert content.headers["content-disposition"].startswith("attachment")
    assert content.headers["x-content-type-options"] == "nosniff"

    patched = (
        await client.patch(f"/v1/artifacts/{created['id']}", json={"description": "weekly"})
    ).json()
    assert patched["description"] == "weekly"

    untouched = (await client.patch(f"/v1/artifacts/{created['id']}", json={})).json()
    assert untouched["description"] == "weekly"
    cleared = (
        await client.patch(f"/v1/artifacts/{created['id']}", json={"description": None})
    ).json()
    assert cleared["description"] is None

    deleted = (await client.delete(f"/v1/artifacts/{created['id']}")).json()
    assert deleted["deleted"] is True
    assert key not in blobs
    assert (await client.get(f"/v1/artifacts/{created['id']}")).status_code == 404


async def test_agent_delete_emits_event(client, internal_client, launched):
    _, session = await make_session(client)
    await register(internal_client, session["id"])

    response = await internal_client.delete(
        f"/internal/sessions/{session['id']}/artifacts/report.md"
    )
    assert response.status_code == 200
    assert (await client.get("/v1/artifacts")).json()["data"] == []

    events = (await client.get(f"/v1/sessions/{session['id']}/events")).json()["data"]
    actions = [e["payload"]["action"] for e in events if e["type"] == "agent.artifact"]
    assert actions == ["created", "deleted"]

    missing = await internal_client.delete(
        f"/internal/sessions/{session['id']}/artifacts/report.md"
    )
    assert missing.status_code == 404


async def test_register_rejects_oversize_and_bad_names(client, internal_client, launched):
    _, session = await make_session(client)

    too_big = await register(internal_client, session["id"], size_bytes=10 * 1024 * 1024 + 1)
    assert too_big.status_code == 413

    traversal = await register(internal_client, session["id"], name="../escape.md")
    assert traversal.status_code == 400

    bad_chars = await register(internal_client, session["id"], name="a\nb")
    assert bad_chars.status_code == 422
