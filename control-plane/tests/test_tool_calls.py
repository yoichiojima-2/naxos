import pytest
from naxos_shared.ids import call_hash, canonical_json

from naxos_cp import db

from .test_internal_flow import start_session

ALWAYS_ASK = {"permission_policy": {"default": "always_ask", "rules": []}}
ALWAYS_ALLOW = {"permission_policy": {"default": "always_allow", "rules": []}}


async def ask(internal_client, sid, tool_name="Bash", tool_input=None, tool_use_id="tu_1"):
    tool_input = {"command": "ls"} if tool_input is None else tool_input
    return (
        await internal_client.post(
            f"/internal/sessions/{sid}/permission",
            json={
                "call_hash": call_hash(tool_name, tool_input),
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
            },
        )
    ).json()


async def rows(session_id: str) -> list[dict]:
    async with db.transaction() as conn:
        return [
            dict(r)
            for r in await conn.fetch(
                "SELECT * FROM tool_calls WHERE session_id = $1 ORDER BY id", session_id
            )
        ]


@pytest.mark.parametrize(
    ("overrides", "label"),
    [
        (ALWAYS_ALLOW, "auto_allowed"),
        (ALWAYS_ASK, "awaiting_confirmation"),
        ({**ALWAYS_ALLOW, "tools": ["Read"]}, "not_allowed"),
    ],
)
async def test_every_gate_decision_is_recorded(client, internal_client, launched, overrides, label):
    _, session = await start_session(client, launched, **overrides)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")

    verdict = await ask(internal_client, sid)
    assert verdict["label"] == label

    (row,) = await rows(sid)
    assert row["decision"] == label
    assert row["tool_name"] == "Bash"
    assert row["approved_by"] is None


async def test_the_record_does_not_depend_on_the_sandbox_reporting_it(
    client, internal_client, launched
):
    """The whole point: the row is committed at the gate, before the tool runs, so a
    sandbox that dies without ever posting an event still leaves evidence."""
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")

    await ask(internal_client, sid)

    events = (await client.get(f"/v1/sessions/{sid}/events")).json()["data"]
    assert not [e for e in events if e["type"] == "agent.tool_use"]
    (row,) = await rows(sid)
    assert row["decision"] == "auto_allowed"


async def test_args_are_the_canonical_bytes_the_call_hash_covers(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    tool_input = {"z": 1, "a": {"nested": True}}

    await ask(internal_client, sid, tool_input=tool_input)

    (row,) = await rows(sid)
    assert row["args_json"] == canonical_json(tool_input)
    assert row["args_truncated"] is False
    assert row["call_hash"] == call_hash("Bash", tool_input)


async def test_oversized_args_are_truncated_and_flagged(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    tool_input = {"command": "x" * 10_000}

    await ask(internal_client, sid, tool_input=tool_input)

    (row,) = await rows(sid)
    assert row["args_truncated"] is True
    assert len(row["args_json"]) < 10_000
    # The hash still identifies the full call even though the args were capped.
    assert row["call_hash"] == call_hash("Bash", tool_input)


async def test_result_attaches_to_its_call(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid, tool_use_id="tu_result")

    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "events": [
                {
                    "type": "agent.tool_result",
                    "payload": {"tool_use_id": "tu_result", "is_error": False, "content": "ok"},
                }
            ]
        },
    )

    (row,) = await rows(sid)
    assert row["result_status"] == "ok"
    assert row["error"] is None
    assert row["latency_ms"] is not None
    assert row["resulted_at"] is not None


async def test_a_failing_call_records_its_error(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid, tool_use_id="tu_err")

    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "events": [
                {
                    "type": "agent.tool_result",
                    "payload": {
                        "tool_use_id": "tu_err",
                        "is_error": True,
                        "content": "permission denied",
                    },
                }
            ]
        },
    )

    (row,) = await rows(sid)
    assert row["result_status"] == "error"
    assert row["error"] == "permission denied"


async def test_a_denial_is_not_overwritten_by_the_synthetic_tool_result(
    client, internal_client, launched
):
    """The CLI produces an error tool-result for a call it was told to deny; that
    must not relabel the record as a mere failure."""
    _, session = await start_session(client, launched, **ALWAYS_ALLOW, tools=["Read"])
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid, tool_use_id="tu_denied")

    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "events": [
                {
                    "type": "agent.tool_result",
                    "payload": {"tool_use_id": "tu_denied", "is_error": True, "content": "denied"},
                }
            ]
        },
    )

    (row,) = await rows(sid)
    assert row["decision"] == "not_allowed"
    assert row["result_status"] == "denied"


async def test_the_pause_and_its_approval_are_two_chained_records(
    client, internal_client, launched
):
    """An auditor should see the attempt, the pause, who approved it, and the call
    that then ran — chained by call_hash across a tool_use_id that changed."""
    _, session = await start_session(client, launched, **ALWAYS_ASK)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]

    await ask(internal_client, sid, tool_use_id="tu_first")
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "stop_reason": "requires_action"},
    )
    await client.post(
        f"/v1/sessions/{sid}/events",
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
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid, tool_use_id="tu_second")

    paused, approved = await rows(sid)
    assert paused["decision"] == "awaiting_confirmation"
    assert approved["decision"] == "user_allowed"
    assert approved["approved_by"] == "local-dev"
    assert paused["call_hash"] == approved["call_hash"]
    assert paused["tool_use_id"] != approved["tool_use_id"]


async def test_the_actor_is_whoever_sent_the_turn_not_whoever_opened_the_session(
    client, internal_client, launched, monkeypatch
):
    from naxos_cp import config

    monkeypatch.setattr(config, "DEV_PRINCIPAL", "opener@example.com")
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]

    monkeypatch.setattr(config, "DEV_PRINCIPAL", "sender@example.com")
    await client.post(
        f"/v1/sessions/{sid}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "go"}]}]},
    )
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)

    (row,) = await rows(sid)
    assert row["principal"] == "sender@example.com"


async def test_a_deployment_run_is_attributed_to_the_deployment(client, internal_client, launched):
    from .test_features import make_deployment
    from .test_session_flow import make_agent

    _, agent = await make_agent(client, **ALWAYS_ALLOW)
    deployment = await make_deployment(client, agent)
    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    sid = run["session_id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)

    (row,) = await rows(sid)
    assert row["principal"] == f"deployment:{deployment['id']}"


async def test_the_record_survives_deleting_the_session(
    client, internal_client, launched, monkeypatch
):
    from naxos_cp import gcs

    async def fake_delete_prefix(bucket, prefix):
        pass

    monkeypatch.setattr(gcs, "delete_prefix", fake_delete_prefix)
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint", json={"lease_id": lease, "terminated": True}
    )

    assert (await client.delete(f"/v1/sessions/{sid}")).status_code == 200

    listed = (await client.get("/v1/tool_calls", params={"session_id": sid})).json()["data"]
    assert [r["tool_name"] for r in listed] == ["Bash"]
    assert (await client.get("/v1/monitoring/summary")).json()["totals"]["tool_calls"] == 1


async def test_list_filters_and_pages(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    for i in range(3):
        await ask(internal_client, sid, tool_input={"command": f"ls {i}"})
    await ask(internal_client, sid, tool_name="Read", tool_input={"file_path": "/x"})

    first = (await client.get("/v1/tool_calls", params={"limit": 2})).json()
    assert len(first["data"]) == 2
    assert first["next_cursor"] is not None

    second = (
        await client.get("/v1/tool_calls", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert len(second["data"]) == 2
    assert {r["id"] for r in first["data"]}.isdisjoint({r["id"] for r in second["data"]})

    # A full last page still hands back a cursor; the empty page after it ends the walk.
    third = (
        await client.get("/v1/tool_calls", params={"limit": 2, "cursor": second["next_cursor"]})
    ).json()
    assert third == {"data": [], "next_cursor": None}

    by_tool = (await client.get("/v1/tool_calls", params={"tool_name": "Read"})).json()
    assert [r["tool_name"] for r in by_tool["data"]] == ["Read"]

    by_decision = (await client.get("/v1/tool_calls", params={"decision": "user_denied"})).json()
    assert by_decision["data"] == []


async def test_export_streams_the_filtered_record(client, internal_client, launched):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    await internal_client.post(f"/internal/sessions/{sid}/claim")
    await ask(internal_client, sid)

    response = await client.get("/v1/tool_calls/export", params={"session_id": sid})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.splitlines() if line]
    assert len(lines) == 1
    assert '"tool_name":"Bash"' in lines[0].replace(", ", ",").replace('": ', '":')


async def test_export_to_bigquery_retries_until_it_lands(client, internal_client, launched):
    from naxos_cp import audit

    sent: list[list[dict]] = []
    accept = False

    async def fake_insert(table, rows, row_ids=None):
        if not accept:
            return False
        sent.append(rows)
        assert row_ids == [r["tool_call_id"] for r in rows]
        return True

    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audit, "_insert", fake_insert)
        await internal_client.post(f"/internal/sessions/{sid}/checkpoint", json={"lease_id": lease})
        assert sent == []
        async with db.transaction() as conn:
            assert await conn.fetchval("SELECT count(*) FROM tool_calls WHERE exported_at IS NULL")

        accept = True
        assert await audit.export_tool_calls(sid) == 1

    assert [r["tool_name"] for r in sent[0]] == ["Bash"]
    async with db.transaction() as conn:
        assert not await conn.fetchval("SELECT count(*) FROM tool_calls WHERE exported_at IS NULL")


async def test_an_unfinished_call_is_closed_out_at_the_burst_boundary(
    client, internal_client, launched
):
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await ask(internal_client, sid)

    await internal_client.post(f"/internal/sessions/{sid}/checkpoint", json={"lease_id": lease})

    (row,) = await rows(sid)
    assert row["result_status"] == "no_result"


async def test_a_sandbox_image_that_predates_the_run_id_still_records_correctly(
    client, internal_client, launched
):
    """The control plane deploys before the sandbox image. An old sandbox POSTs an
    empty claim body and no token counts; the record must still line up."""
    _, session = await start_session(client, launched, **ALWAYS_ALLOW)
    sid = session["id"]
    claim = await internal_client.post(f"/internal/sessions/{sid}/claim", json={})
    lease = claim.json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})

    await ask(internal_client, sid, tool_use_id="tu_old")
    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "run_id": "a-run-id-the-control-plane-ignores",
            "events": [
                {
                    "type": "agent.tool_result",
                    "payload": {"tool_use_id": "tu_old", "is_error": False, "content": "ok"},
                }
            ],
        },
    )
    await internal_client.post(f"/internal/sessions/{sid}/checkpoint", json={"lease_id": lease})

    (row,) = await rows(sid)
    assert row["run_id"] == sid
    assert row["result_status"] == "ok"
