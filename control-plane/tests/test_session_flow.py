from naxos_shared.ids import call_hash

ENV = {
    "name": "default",
    "service_account_email": "sa-env-default@example.iam.gserviceaccount.com",
    "sandbox_job_name": "naxos-sbx-default",
    "session_bucket": "naxos2-sess-default",
}
AGENT = {"name": "ops", "model": "claude-sonnet-5", "instructions": "You watch logs."}


async def make_agent(client, **overrides):
    env = (await client.post("/v1/environments", json=ENV)).json()
    body = {"environment_id": env["id"], **AGENT, **overrides}
    agent = (await client.post("/v1/agents", json=body)).json()
    return env, agent


async def test_agent_versions_are_immutable_and_incrementing(client):
    _, agent = await make_agent(client)
    assert agent["version"] == 1

    updated = await client.post(
        f"/v1/agents/{agent['id']}/versions",
        json={
            "environment_id": agent["environment_id"],
            "name": "ops",
            "model": "claude-sonnet-5",
            "instructions": "You watch logs and metrics.",
        },
    )
    assert updated.json()["version"] == 2

    v1 = (await client.get(f"/v1/agents/{agent['id']}", params={"version": 1})).json()
    assert v1["instructions"] == "You watch logs."


async def test_agent_requires_registered_environment(client):
    response = await client.post("/v1/agents", json={"environment_id": "env_missing", **AGENT})
    assert response.status_code == 409


async def test_session_with_initial_events_wakes_a_sandbox(client, launched):
    _, agent = await make_agent(client)
    session = (
        await client.post(
            "/v1/sessions",
            json={
                "agent": {"id": agent["id"]},
                "initial_events": [
                    {"type": "user.message", "content": [{"type": "text", "text": "hello"}]}
                ],
            },
        )
    ).json()

    assert session["status"] == "rescheduling"
    assert launched == [("naxos-sbx-default", session["id"])]

    events = (await client.get(f"/v1/sessions/{session['id']}/events")).json()["data"]
    assert [e["type"] for e in events] == ["user.message"]
    assert events[0]["processed_at"] is None


async def test_session_without_initial_events_starts_idle(client, launched):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    assert session["status"] == "idle"
    assert launched == []


async def test_kill_switch_rejects_new_events(client, launched):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()

    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": True})
    response = await client.post(
        f"/v1/sessions/{session['id']}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "hi"}]}]},
    )
    assert response.status_code == 409
    assert launched == []


async def test_disabled_agent_cannot_start_a_session(client):
    _, agent = await make_agent(client)
    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": True})
    response = await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})
    assert response.status_code == 409


async def test_terminated_session_rejects_events(client, launched):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    await client.post(f"/v1/sessions/{session['id']}/terminate")

    response = await client.post(
        f"/v1/sessions/{session['id']}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "hi"}]}]},
    )
    assert response.status_code == 409


async def test_tool_confirmation_requires_a_pending_request(client, launched):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()

    response = await client.post(
        f"/v1/sessions/{session['id']}/events",
        json={
            "events": [
                {
                    "type": "user.tool_confirmation",
                    "call_hash": call_hash("Bash", {"command": "ls"}),
                    "result": "allow",
                }
            ]
        },
    )
    assert response.status_code == 409


async def test_event_sequence_is_per_session_and_monotonic(client, launched):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()

    for text in ("one", "two", "three"):
        await client.post(
            f"/v1/sessions/{session['id']}/events",
            json={
                "events": [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
            },
        )

    events = (await client.get(f"/v1/sessions/{session['id']}/events")).json()["data"]
    assert [e["seq"] for e in events] == [1, 2, 3]

    after_first = (
        await client.get(f"/v1/sessions/{session['id']}/events", params={"after": 1})
    ).json()["data"]
    assert [e["seq"] for e in after_first] == [2, 3]
