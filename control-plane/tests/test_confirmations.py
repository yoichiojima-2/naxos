import pytest
from naxos_shared.ids import call_hash

from naxos_cp import config, db

from .test_internal_flow import start_session
from .test_session_flow import AGENT, ENV
from .test_tool_calls import ALWAYS_ASK, ask, rows


async def pending(client, **params):
    return (await client.get("/v1/tool_confirmations", params=params)).json()


async def parked_sessions(client, internal_client, count: int) -> list[tuple[str, str]]:
    """`count` sessions, each on its own agent, each paused on a gated Bash call.
    The environment is registered once — a second POST of the same name is a 409."""
    env = (await client.post("/v1/environments", json=ENV)).json()
    out = []
    for i in range(count):
        agent = (
            await client.post(
                "/v1/agents",
                json={**AGENT, **ALWAYS_ASK, "name": f"ops-{i}", "environment_id": env["id"]},
            )
        ).json()
        session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
        await internal_client.post(f"/internal/sessions/{session['id']}/claim")
        await ask(internal_client, session["id"])
        out.append((agent["id"], session["id"]))
    return out


async def test_the_inbox_shows_what_is_waiting_across_every_session(
    client, internal_client, launched
):
    """The point of the inbox: one reviewer sees every parked call without having
    to know which sessions to open."""
    parked = await parked_sessions(client, internal_client, 2)

    body = await pending(client)
    assert body["pending"] == 2
    assert {c["session_id"] for c in body["data"]} == {sid for _, sid in parked}
    entry = body["data"][0]
    assert entry["tool_name"] == "Bash"
    assert entry["input"] == {"command": "ls"}
    assert entry["status"] == "pending"
    assert entry["agent_name"]
    assert entry["expires_at"] is not None


async def test_the_inbox_names_the_human_the_agent_is_working_for(
    client, internal_client, launched, monkeypatch
):
    monkeypatch.setattr(config, "DEV_PRINCIPAL", "opener@example.com")
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]

    monkeypatch.setattr(config, "DEV_PRINCIPAL", "sender@example.com")
    await client.post(
        f"/v1/sessions/{sid}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "go"}]}]},
    )
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)

    (entry,) = (await pending(client))["data"]
    assert entry["requested_for"] == "sender@example.com"


async def test_deciding_from_the_inbox_uses_the_same_path_as_the_timeline(
    client, internal_client, launched
):
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid)

    (entry,) = (await pending(client))["data"]
    approved = await client.post(
        f"/v1/sessions/{entry['session_id']}/events",
        json={
            "events": [
                {
                    "type": "user.tool_confirmation",
                    "call_hash": entry["call_hash"],
                    "result": "allow",
                }
            ]
        },
    )
    assert approved.status_code == 202

    assert (await pending(client))["pending"] == 0
    assert (await pending(client, status="allowed"))["data"][0]["decided_by"] == "local-dev"


async def test_the_inbox_filters_by_agent_and_session(client, internal_client, launched):
    parked = await parked_sessions(client, internal_client, 2)
    first_agent, first_session = parked[0]

    scoped = await pending(client, agent_id=first_agent)
    assert [c["agent_id"] for c in scoped["data"]] == [first_agent]
    assert [c["session_id"] for c in (await pending(client, session_id=first_session))["data"]] == [
        first_session
    ]
    # The unfiltered count rides along so a badge does not need a second request.
    assert scoped["pending"] == 2


async def test_an_unanswered_approval_expires_and_unblocks_its_session(
    client, internal_client, launched, monkeypatch
):
    """A pause nobody answers must not park the agent forever. Expiry resumes the
    session down the same path a human decision takes, and the agent is told."""
    monkeypatch.setattr(config, "CONFIRMATION_TTL_HOURS", 0.0001)
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid, tool_use_id="tu_first")
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "stop_reason": "requires_action"},
    )

    async with db.transaction() as conn:
        await conn.execute("UPDATE tool_confirmations SET expires_at = now() - interval '1 hour'")

    swept = (await internal_client.post("/internal/reconcile")).json()
    assert len(swept["expired"]) == 1

    assert (await pending(client))["pending"] == 0
    assert (await pending(client, status="expired"))["data"][0]["decided_by"] is None

    # The session was woken, and the replayed call is refused with a reason that
    # tells the agent to stop rather than retry.
    assert (await client.get(f"/v1/sessions/{sid}")).json()["status"] == "rescheduling"
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    verdict = await ask(internal_client, sid, tool_use_id="tu_second")
    assert verdict["decision"] == "deny"
    assert verdict["label"] == "expired"
    assert "timed out" in verdict["reason"]

    paused, expired = await rows(sid)
    assert paused["decision"] == "awaiting_confirmation"
    assert expired["decision"] == "expired"
    assert expired["approved_by"] is None


async def test_expiry_leaves_a_terminated_session_alone(client, internal_client, launched):
    """Nothing would ever consume the resume event, so the row would sit queued."""
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid)
    await client.post(f"/v1/sessions/{sid}/terminate", json={})

    async with db.transaction() as conn:
        await conn.execute("UPDATE tool_confirmations SET expires_at = now() - interval '1 hour'")

    assert (await internal_client.post("/internal/reconcile")).json()["expired"] == []
    assert (await pending(client))["data"][0]["status"] == "pending"


async def test_expiry_is_off_when_the_ttl_is_zero(client, internal_client, launched, monkeypatch):
    monkeypatch.setattr(config, "CONFIRMATION_TTL_HOURS", 0)
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid)

    (entry,) = (await pending(client))["data"]
    assert entry["expires_at"] is None
    assert (await internal_client.post("/internal/reconcile")).json()["expired"] == []


@pytest.mark.parametrize("status", ["allowed", "denied"])
async def test_a_decided_approval_leaves_the_pending_queue(
    client, internal_client, launched, status
):
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid)

    await client.post(
        f"/v1/sessions/{sid}/events",
        json={
            "events": [
                {
                    "type": "user.tool_confirmation",
                    "call_hash": call_hash("Bash", {"command": "ls"}),
                    "result": "allow" if status == "allowed" else "deny",
                }
            ]
        },
    )

    assert (await pending(client))["data"] == []
    assert (await pending(client, status=status))["data"][0]["session_id"] == sid
