from .test_internal_flow import start_session


async def test_summary_aggregates_runs_and_tool_calls(client, internal_client, launched):
    agent, session = await start_session(client, launched)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.get(f"/internal/sessions/{sid}/queue", params={"wait": 0})
    await internal_client.post(
        f"/internal/sessions/{sid}/events",
        json={
            "run_id": "run-1",
            "events": [
                {
                    "type": "agent.tool_use",
                    "payload": {"tool_name": "Bash", "input": {"command": "ls"}},
                },
                {
                    "type": "agent.tool_use",
                    "payload": {"tool_name": "Read", "decision": "user_denied"},
                },
                {
                    "type": "agent.tool_use",
                    "payload": {"tool_name": "Write", "decision": "awaiting_confirmation"},
                },
            ],
        },
    )
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "run_id": "run-1", "cost_usd": 0.25, "num_turns": 3},
    )

    body = (await client.get("/v1/monitoring/summary")).json()
    assert body["window_days"] == 30
    assert body["totals"] == {"cost_usd": 0.25, "runs": 1, "num_turns": 3, "tool_calls": 2}
    assert body["all_time"] == {"cost_usd": 0.25, "sessions": 1}
    assert len(body["cost_by_day"]) == 1
    assert body["cost_by_day"][0]["cost_usd"] == 0.25
    assert body["cost_by_agent"] == [
        {
            "agent_id": agent["id"],
            "name": agent["name"],
            "cost_usd": 0.25,
            "runs": 1,
            "sessions": 1,
        }
    ]
    assert body["cost_by_model"] == [{"model": "claude-sonnet-5", "cost_usd": 0.25, "runs": 1}]
    assert body["sessions_by_status"] == [{"status": "idle", "count": 1}]
    assert body["tool_usage"] == [
        {"tool_name": "Bash", "calls": 1, "denied": 0},
        {"tool_name": "Read", "calls": 1, "denied": 1},
    ]


async def test_second_checkpoint_records_only_the_cost_delta(client, internal_client, launched):
    _, session = await start_session(client, launched)
    sid = session["id"]
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "run_id": "run-1", "cost_usd": 0.25},
    )
    await client.post(
        f"/v1/sessions/{sid}/events",
        json={"events": [{"type": "user.message", "content": [{"type": "text", "text": "go"}]}]},
    )
    lease = (await internal_client.post(f"/internal/sessions/{sid}/claim")).json()["lease_id"]
    await internal_client.post(
        f"/internal/sessions/{sid}/checkpoint",
        json={"lease_id": lease, "run_id": "run-2", "cost_usd": 0.4},
    )

    body = (await client.get("/v1/monitoring/summary?days=7")).json()
    assert body["window_days"] == 7
    assert body["totals"]["runs"] == 2
    assert round(body["totals"]["cost_usd"], 6) == 0.4
    assert body["all_time"]["cost_usd"] == 0.4


async def test_summary_is_empty_without_activity(client):
    body = (await client.get("/v1/monitoring/summary")).json()
    assert body["totals"] == {"cost_usd": 0, "runs": 0, "num_turns": 0, "tool_calls": 0}
    assert body["cost_by_day"] == []
    assert body["cost_by_agent"] == []
    assert body["deployment_runs"] == []
