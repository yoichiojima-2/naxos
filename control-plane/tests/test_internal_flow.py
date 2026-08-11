from naxos_shared.ids import call_hash

from .test_session_flow import make_agent

BASH = {"tool_name": "Bash", "input": {"command": "rm -rf /tmp/x"}}
DIGEST = call_hash(BASH["tool_name"], BASH["input"])


async def start_session(client, launched, **agent_overrides):
    _, agent = await make_agent(client, **agent_overrides)
    session = (
        await client.post(
            "/v1/sessions",
            json={
                "agent": {"id": agent["id"]},
                "initial_events": [
                    {"type": "user.message", "content": [{"type": "text", "text": "go"}]}
                ],
            },
        )
    ).json()
    return agent, session


async def test_claim_is_exclusive_and_queue_delivers_once(client, internal_client, launched):
    _, session = await start_session(client, launched)
    sid = session["id"]

    claim = await internal_client.post(f"/internal/sessions/{sid}/claim")
    assert claim.status_code == 200
    assert (await internal_client.post(f"/internal/sessions/{sid}/claim")).status_code == 409

    batch = (
        await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    ).json()
    assert [e["type"] for e in batch["events"]] == ["user.message"]

    again = (
        await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    ).json()
    assert again["events"] == []


async def test_queue_poll_ends_with_terminate_when_session_is_deleted(
    client, internal_client, launched, monkeypatch
):
    import asyncio

    from naxos_cp import db, gcs

    async def fake_delete_prefix(bucket, prefix):
        pass

    monkeypatch.setattr(gcs, "delete_prefix", fake_delete_prefix)
    _, session = await start_session(client, launched)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE sessions SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
            sid,
        )

    poll = asyncio.create_task(
        internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 10})
    )
    await asyncio.sleep(0.1)
    assert (await client.delete(f"/v1/sessions/{sid}")).status_code == 200
    assert (await poll).json()["control"] == "terminate"


async def test_config_carries_resolved_agent_version(client, internal_client, launched):
    agent, session = await start_session(client, launched)
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    config = (await internal_client.get(f"/internal/sessions/{session['id']}/config")).json()
    assert config["agent_id"] == agent["id"]
    assert config["agent_version"] == 1
    assert config["model"] == "claude-sonnet-5"
    assert config["session_bucket"] == "naxos2-sess-default"


async def test_always_ask_pauses_then_resumes_after_confirmation(client, internal_client, launched):
    policy = {"permission_policy": {"default": "always_ask", "rules": []}}
    _, session = await start_session(client, launched, **policy)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]

    ask = {"call_hash": DIGEST, "tool_use_id": "toolu_first", **BASH}
    first = (await internal_client.post(f"/internal/sessions/{sid}/permission", json=ask)).json()
    assert first["decision"] == "pending"

    # The sandbox exits while waiting; the operator approves through the public API.
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "stop_reason": "requires_action"},
    )
    approve = await client.post(
        f"/v1/sessions/{sid}/events",
        json={
            "events": [{"type": "user.tool_confirmation", "call_hash": DIGEST, "result": "allow"}]
        },
    )
    assert approve.status_code == 202

    # A fresh sandbox replays the same call with a different tool_use_id.
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    replay = {"call_hash": DIGEST, "tool_use_id": "toolu_second", **BASH}
    second = (
        await internal_client.post(f"/internal/sessions/{sid}/permission", json=replay)
    ).json()
    assert second["decision"] == "allow"
    assert second["by"] == "user"


async def test_denied_call_stays_denied_across_replays(client, internal_client, launched):
    policy = {"permission_policy": {"default": "always_ask", "rules": []}}
    _, session = await start_session(client, launched, **policy)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.post(
        f"/internal/sessions/{sid}/permission",
        json={"call_hash": DIGEST, "tool_use_id": "toolu_first", **BASH},
    )
    await client.post(
        f"/v1/sessions/{sid}/events",
        json={
            "events": [
                {
                    "type": "user.tool_confirmation",
                    "call_hash": DIGEST,
                    "result": "deny",
                    "deny_message": "not on production",
                }
            ]
        },
    )

    verdict = (
        await internal_client.post(
            f"/internal/sessions/{sid}/permission",
            json={"call_hash": DIGEST, "tool_use_id": "toolu_second", **BASH},
        )
    ).json()
    assert verdict["decision"] == "deny"
    assert verdict["by"] == "user"
    assert verdict["reason"] == "not on production"


async def test_always_allow_needs_no_confirmation(client, internal_client, launched):
    policy = {"permission_policy": {"default": "always_allow", "rules": []}}
    _, session = await start_session(client, launched, **policy)
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    verdict = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/permission",
            json={"call_hash": DIGEST, "tool_use_id": "toolu_x", **BASH},
        )
    ).json()
    assert verdict["decision"] == "allow"
    assert verdict["by"] == "policy"


async def test_kill_switch_denies_a_tool_call_mid_run(client, internal_client, launched):
    agent, session = await start_session(client, launched)
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")
    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": True})

    verdict = (
        await internal_client.post(
            f"/internal/sessions/{session['id']}/permission",
            json={"call_hash": DIGEST, "tool_use_id": "toolu_x", **BASH},
        )
    ).json()
    assert verdict == {"decision": "deny", "reason": "agent disabled", "killed": True}

    batch = (
        await internal_client.get(f"/internal/sessions/{session['id']}/queue", params={"wait": 0})
    ).json()
    assert batch["control"] == "kill"


async def test_checkpoint_releases_the_lease_and_rewakes_on_pending_work(
    client, internal_client, launched
):
    _, session = await start_session(client, launched)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})

    # A message arrives after the sandbox decided to stop.
    await client.post(
        f"/v1/sessions/{sid}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "more"}]}]},
    )
    launched.clear()
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "sdk_session_id": "sdk-1", "cost_usd": 0.42},
    )

    row = (await client.get(f"/v1/sessions/{sid}")).json()
    assert row["sdk_session_id"] == "sdk-1"
    assert float(row["cost_usd"]) == 0.42
    assert launched == [("naxos-sbx-default", sid)]

    # Lease was released, so a fresh sandbox can claim it.
    assert (await internal_client.post(f"/internal/sessions/{sid}/claim")).status_code == 200


async def test_events_from_the_sandbox_land_on_the_stream(client, internal_client, launched):
    _, session = await start_session(client, launched)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "events": [
                {"type": "agent.message", "payload": {"text": "working on it"}},
                {
                    "type": "agent.tool_use",
                    "payload": {"tool_name": "Bash", "decision": "auto_allowed"},
                },
            ]
        },
    )

    types = [e["type"] for e in (await client.get(f"/v1/sessions/{sid}/events")).json()["data"]]
    assert types == [
        "user.message",
        "session.status_running",
        "agent.message",
        "agent.tool_use",
    ]
