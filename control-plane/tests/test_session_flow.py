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
    assert [e["type"] for e in events] == ["user.message", "session.status_rescheduling"]
    assert events[0]["processed_at"] is None
    assert events[1]["processed_at"] is not None


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


async def test_sandbox_cap_counts_launched_but_unclaimed_sessions(client, launched, monkeypatch):
    from naxos_cp import config, db, wake

    monkeypatch.setattr(config, "MAX_CONCURRENT_SANDBOXES", 1)
    _, agent = await make_agent(client)
    hello = {"type": "user.message", "content": [{"type": "text", "text": "hello"}]}

    first = (
        await client.post(
            "/v1/sessions", json={"agent": {"id": agent["id"]}, "initial_events": [hello]}
        )
    ).json()
    assert first["status"] == "rescheduling"

    second = (
        await client.post(
            "/v1/sessions", json={"agent": {"id": agent["id"]}, "initial_events": [hello]}
        )
    ).json()
    assert second["status"] == "idle"
    assert [s for _, s in launched] == [first["id"]]

    # A capped session gets no rescheduling event — it never left idle.
    second_events = (await client.get(f"/v1/sessions/{second['id']}/events")).json()["data"]
    assert [e["type"] for e in second_events] == ["user.message"]

    # A re-wake of the launched-but-unclaimed session must not count itself.
    async with db.transaction() as conn:
        assert await wake.wake(conn, first["id"]) is True
    assert [s for _, s in launched] == [first["id"], first["id"]]

    # The re-wake was not a status transition, so no duplicate rescheduling event.
    first_events = (await client.get(f"/v1/sessions/{first['id']}/events")).json()["data"]
    assert [e["type"] for e in first_events] == ["user.message", "session.status_rescheduling"]


async def test_delete_session_removes_rows_and_workspace(client, launched, monkeypatch):
    from naxos_cp import gcs

    wiped = []

    async def fake_delete_prefix(bucket, prefix):
        wiped.append((bucket, prefix))

    monkeypatch.setattr(gcs, "delete_prefix", fake_delete_prefix)
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()

    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 200
    assert (await client.get(f"/v1/sessions/{session['id']}")).status_code == 404
    assert wiped == [("naxos2-sess-default", f"sessions/{session['id']}/")]


async def test_delete_refuses_a_session_that_is_waking(client, launched):
    _, agent = await make_agent(client)
    hello = {"type": "user.message", "content": [{"type": "text", "text": "hello"}]}
    session = (
        await client.post(
            "/v1/sessions", json={"agent": {"id": agent["id"]}, "initial_events": [hello]}
        )
    ).json()
    assert session["status"] == "rescheduling"

    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 409


async def test_delete_refuses_a_session_with_a_live_sandbox(client, launched):
    from naxos_cp import db

    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE sessions SET lease_expires_at = now() + interval '5 minutes' WHERE id = $1",
            session["id"],
        )

    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 409
    assert (await client.get(f"/v1/sessions/{session['id']}")).status_code == 200


async def test_delete_session_keeps_deployment_run_history(client, launched, monkeypatch):
    from naxos_cp import db, gcs

    async def fake_delete_prefix(bucket, prefix):
        pass

    monkeypatch.setattr(gcs, "delete_prefix", fake_delete_prefix)
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO deployments (id, agent_id, name, cron) "
            "VALUES ('dep_1', $1, 'nightly', '0 0 * * *')",
            agent["id"],
        )
        await conn.execute(
            "INSERT INTO deployment_runs (id, deployment_id, session_id, status) "
            "VALUES ('run_1', 'dep_1', $1, 'succeeded')",
            session["id"],
        )

    assert (await client.delete(f"/v1/sessions/{session['id']}")).status_code == 200
    async with db.transaction() as conn:
        run = await conn.fetchrow("SELECT * FROM deployment_runs WHERE id = 'run_1'")
    assert run["session_id"] is None


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
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert [e["type"] for e in events] == [
        "user.message",
        "session.status_rescheduling",
        "user.message",
        "user.message",
    ]

    after_first = (
        await client.get(f"/v1/sessions/{session['id']}/events", params={"after": 2})
    ).json()["data"]
    assert [e["seq"] for e in after_first] == [3, 4]
