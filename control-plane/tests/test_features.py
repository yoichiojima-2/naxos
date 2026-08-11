import pytest

from naxos_cp import vaults

from .test_session_flow import make_agent

MESSAGE = {"type": "user.message", "content": [{"type": "text", "text": "run the check"}]}


@pytest.fixture(autouse=True)
def no_gcp(monkeypatch):
    async def fake_store(credential_id: str, value: str) -> None:
        pass

    async def fake_delete(secret_ref: str) -> None:
        pass

    monkeypatch.setattr(vaults, "_secret_name", lambda cid: f"projects/test/secrets/vault-{cid}")
    monkeypatch.setattr(vaults, "_store_secret", fake_store)
    monkeypatch.setattr(vaults, "_delete_secret", fake_delete)


async def test_deployment_run_now_creates_a_session(client, launched):
    _, agent = await make_agent(client)
    deployment = (
        await client.post(
            "/v1/deployments",
            json={
                "name": "nightly",
                "agent_id": agent["id"],
                "cron": "0 3 * * *",
                "initial_events": [MESSAGE],
            },
        )
    ).json()

    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    assert run["status"] == "running"
    assert run["session_id"]
    assert launched == [("naxos-sbx-default", run["session_id"])]

    session = (await client.get(f"/v1/sessions/{run['session_id']}")).json()
    assert session["created_by"] == f"deployment:{deployment['id']}"

    runs = (await client.get(f"/v1/deployments/{deployment['id']}/runs")).json()["data"]
    assert [r["id"] for r in runs] == [run["id"]]


async def test_deployment_fire_inherits_agent_vaults(
    client, internal_client, launched, monkeypatch
):
    from naxos_cp import config as cp_config

    monkeypatch.setattr(cp_config, "EGRESS_URL", "https://egress.example")
    vault = (await client.post("/v1/vaults", json={"name": "creds"})).json()
    await client.post(
        f"/v1/vaults/{vault['id']}/credentials",
        json={
            "name": "github",
            "type": "header",
            "value": "ghp_secret",
            "target": {"mcp_server": "github", "header": "authorization", "prefix": "Bearer "},
        },
    )
    _, agent = await make_agent(
        client,
        mcp_servers={"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}},
        vault_ids=[vault["id"]],
    )
    deployment = (
        await client.post(
            "/v1/deployments",
            json={
                "name": "nightly",
                "agent_id": agent["id"],
                "cron": "0 3 * * *",
                "initial_events": [MESSAGE],
            },
        )
    ).json()

    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    assert run["status"] == "running"
    session = (await client.get(f"/v1/sessions/{run['session_id']}")).json()
    assert session["vault_ids"] == [vault["id"]]

    await internal_client.post(f"/internal/sessions/{run['session_id']}/claim")
    resolved = (await internal_client.get(f"/internal/sessions/{run['session_id']}/config")).json()
    assert resolved["mcp_servers"]["github"]["url"].startswith("https://egress.example/r/")


async def test_deployment_fire_failure_is_recorded(client, monkeypatch):
    from naxos_cp import wake

    async def boom(job_name: str, session_id: str) -> str:
        raise RuntimeError("cloud run down")

    monkeypatch.setattr(wake.sandbox, "launch", boom)
    _, agent = await make_agent(client)
    deployment = (
        await client.post(
            "/v1/deployments",
            json={
                "name": "nightly",
                "agent_id": agent["id"],
                "cron": "0 3 * * *",
                "initial_events": [MESSAGE],
            },
        )
    ).json()

    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    assert run["status"] == "failed"
    assert run["error_type"] == "infra_error"
    assert "cloud run down" in run["error_message"]

    runs = (await client.get(f"/v1/deployments/{deployment['id']}/runs")).json()["data"]
    assert [r["id"] for r in runs] == [run["id"]]


async def test_deployment_records_failure_when_agent_disabled(client, launched):
    _, agent = await make_agent(client)
    deployment = (
        await client.post(
            "/v1/deployments",
            json={
                "name": "nightly",
                "agent_id": agent["id"],
                "cron": "0 3 * * *",
                "initial_events": [MESSAGE],
            },
        )
    ).json()
    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": True})

    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    assert run["status"] == "failed"
    assert run["error_type"] == "agent_disabled"
    assert run["session_id"] is None
    assert launched == []


async def test_deployment_requires_initial_events(client):
    _, agent = await make_agent(client)
    response = await client.post(
        "/v1/deployments",
        json={"name": "x", "agent_id": agent["id"], "cron": "* * * * *", "initial_events": []},
    )
    assert response.status_code == 422


async def test_vault_credentials_are_write_only(client):
    vault = (await client.post("/v1/vaults", json={"name": "team"})).json()
    created = (
        await client.post(
            f"/v1/vaults/{vault['id']}/credentials",
            json={
                "name": "github",
                "type": "header",
                "value": "ghp_secret_value",
                "target": {"mcp_server": "github"},
            },
        )
    ).json()
    assert "value" not in created
    assert "secret_ref" not in created

    listed = (await client.get(f"/v1/vaults/{vault['id']}/credentials")).json()["data"]
    assert len(listed) == 1
    assert "secret_ref" not in listed[0]
    assert "value" not in listed[0]


async def test_memory_round_trip_and_size_cap(client):
    store = (await client.post("/v1/memory_stores", json={"name": "runbooks"})).json()
    put = (
        await client.post(
            f"/v1/memory_stores/{store['id']}/memories",
            json={"path": "incidents/disk-full.md", "content": "Check df -h first."},
        )
    ).json()
    got = (await client.get(f"/v1/memory_stores/{store['id']}/memories/{put['id']}")).json()
    assert got["content"] == "Check df -h first."

    updated = (
        await client.post(
            f"/v1/memory_stores/{store['id']}/memories",
            json={"path": "incidents/disk-full.md", "content": "Check df -h, then du."},
        )
    ).json()
    assert updated["id"] == put["id"]

    too_big = await client.post(
        f"/v1/memory_stores/{store['id']}/memories",
        json={"path": "big.md", "content": "x" * (64 * 1024 + 1)},
    )
    assert too_big.status_code == 413


async def test_session_config_rewrites_mcp_urls_through_egress(
    client, internal_client, launched, monkeypatch
):
    from naxos_cp import config as cp_config

    monkeypatch.setattr(cp_config, "EGRESS_URL", "https://egress.example")
    vault = (await client.post("/v1/vaults", json={"name": "creds"})).json()
    await client.post(
        f"/v1/vaults/{vault['id']}/credentials",
        json={
            "name": "github",
            "type": "header",
            "value": "ghp_secret",
            "target": {"mcp_server": "github", "header": "authorization", "prefix": "Bearer "},
        },
    )
    _, agent = await make_agent(
        client,
        mcp_servers={"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}},
        vault_ids=[vault["id"]],
    )
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    resolved = (await internal_client.get(f"/internal/sessions/{session['id']}/config")).json()
    url = resolved["mcp_servers"]["github"]["url"]
    assert url.startswith("https://egress.example/r/")

    token = url.rsplit("/", 1)[1]
    route = (await internal_client.get(f"/internal/egress/routes/{token}")).json()
    assert route["target_url"] == "https://api.githubcopilot.com/mcp/"
    assert route["header"] == "authorization"
    assert route["secret_ref"].startswith("projects/test/secrets/vault-")


async def test_memory_mount_and_writeback(client, internal_client, launched):
    store = (await client.post("/v1/memory_stores", json={"name": "runbooks"})).json()
    await client.post(
        f"/v1/memory_stores/{store['id']}/memories",
        json={"path": "notes.md", "content": "seed"},
    )
    _, agent = await make_agent(client, memory_store_ids=[store["id"]])
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    mounted = (await internal_client.get(f"/internal/sessions/{session['id']}/memory")).json()
    assert mounted["stores"][store["id"]]["files"] == {"notes.md": "seed"}

    await internal_client.post(
        f"/internal/sessions/{session['id']}/memory",
        json={"stores": {store["id"]: {"notes.md": "updated by agent", "new.md": "fresh"}}},
    )
    listed = (await client.get(f"/v1/memory_stores/{store['id']}/memories")).json()["data"]
    assert {m["path"] for m in listed} == {"notes.md", "new.md"}
