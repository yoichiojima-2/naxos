import pytest

from naxos_cp import config, gcs

from .test_session_flow import make_agent

MESSAGE = {"type": "user.message", "content": [{"type": "text", "text": "hello"}]}


async def make_agent_session_artifact(client, internal_client):
    _, agent = await make_agent(client)
    session = (
        await client.post(
            "/v1/sessions", json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]}
        )
    ).json()
    artifact = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/artifacts",
            json={"name": "report.md", "content_type": "text/markdown", "size_bytes": 1},
        )
    ).json()
    return agent, session, artifact


async def favorite(client, entity_type, entity_id):
    return await client.post(
        "/v1/favorites", json={"entity_type": entity_type, "entity_id": entity_id}
    )


async def listed(client):
    data = (await client.get("/v1/favorites")).json()["data"]
    return [(f["entity_type"], f["entity_id"]) for f in data]


async def test_round_trip_and_idempotency(client):
    _, agent = await make_agent(client)

    created = await favorite(client, "agent", agent["id"])
    assert created.status_code == 201
    assert created.json()["principal"] == "local-dev"

    again = await favorite(client, "agent", agent["id"])
    assert again.status_code == 201
    assert again.json()["id"] == created.json()["id"]
    assert await listed(client) == [("agent", agent["id"])]

    assert (await client.delete(f"/v1/favorites/agent/{agent['id']}")).status_code == 200
    assert (await client.delete(f"/v1/favorites/agent/{agent['id']}")).status_code == 200
    assert await listed(client) == []


async def test_unknown_entity_and_type_are_rejected(client):
    assert (await favorite(client, "agent", "agent_missing")).status_code == 404
    assert (await favorite(client, "deployment", "depl_x")).status_code == 422
    assert (await client.delete("/v1/favorites/deployment/depl_x")).status_code == 422


@pytest.mark.parametrize("entity_type", ["agent", "session", "artifact", "skill"])
async def test_every_entity_type_round_trips(client, internal_client, launched, entity_type):
    agent, session, artifact = await make_agent_session_artifact(client, internal_client)
    skill = (await client.post("/v1/skills", json={"name": "notes"})).json()
    ids = {
        "agent": agent["id"],
        "session": session["id"],
        "artifact": artifact["id"],
        "skill": skill["id"],
    }

    assert (await favorite(client, entity_type, ids[entity_type])).status_code == 201
    assert await listed(client) == [(entity_type, ids[entity_type])]


async def test_favorites_are_scoped_per_principal(client, monkeypatch):
    _, agent = await make_agent(client)
    assert (await favorite(client, "agent", agent["id"])).status_code == 201

    monkeypatch.setattr(config, "DEV_PRINCIPAL", "other@example.com")
    assert await listed(client) == []
    assert (await favorite(client, "agent", agent["id"])).status_code == 201
    await client.delete(f"/v1/favorites/agent/{agent['id']}")

    monkeypatch.setattr(config, "DEV_PRINCIPAL", "local-dev")
    assert await listed(client) == [("agent", agent["id"])]


async def test_session_delete_clears_session_and_artifact_favorites(
    client, internal_client, launched, monkeypatch
):
    async def fake_delete_prefix(bucket, prefix):
        pass

    monkeypatch.setattr(gcs, "delete_prefix", fake_delete_prefix)
    agent, session, artifact = await make_agent_session_artifact(client, internal_client)
    await favorite(client, "session", session["id"])
    await favorite(client, "artifact", artifact["id"])
    await favorite(client, "agent", agent["id"])

    # A live wake blocks deletion, so the favorites survive with the session.
    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 409
    assert len(await listed(client)) == 3

    from naxos_cp import db

    async with db.transaction() as conn:
        await conn.execute("UPDATE sessions SET status = 'idle' WHERE id = $1", session["id"])
    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 200
    assert await listed(client) == [("agent", agent["id"])]


async def test_artifact_delete_clears_its_favorite(client, internal_client, launched, monkeypatch):
    async def fake_delete(bucket, path):
        pass

    monkeypatch.setattr(gcs, "delete", fake_delete)
    _, session, artifact = await make_agent_session_artifact(client, internal_client)
    await favorite(client, "artifact", artifact["id"])

    assert (await client.delete(f"/v1/artifacts/{artifact['id']}")).status_code == 200
    assert await listed(client) == []

    second = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/artifacts",
            json={"name": "draft.md", "content_type": "text/markdown", "size_bytes": 1},
        )
    ).json()
    await favorite(client, "artifact", second["id"])
    deleted = await internal_client.delete(f"/internal/sessions/{session['id']}/artifacts/draft.md")
    assert deleted.status_code == 200
    assert await listed(client) == []
