import pytest

from naxos_cp import connectors, vaults

from .test_session_flow import ENV, make_agent

MESSAGE = {"type": "user.message", "content": [{"type": "text", "text": "go"}]}


@pytest.fixture(autouse=True)
def no_gcp(monkeypatch):
    async def fake_store(credential_id: str, value: str) -> None:
        pass

    monkeypatch.setattr(vaults, "_store_secret", fake_store)


async def test_hosted_connectors_are_unavailable_until_deployed(client, monkeypatch):
    monkeypatch.delenv("NAXOS_MCP_SLACK_URL", raising=False)
    catalog = {c["name"]: c for c in (await client.get("/v1/connectors")).json()["data"]}

    assert catalog["slack"]["available"] is False
    assert catalog["slack"]["url"] == ""
    # A remote connector always has a URL, but needs a vault credential to work.
    assert catalog["github"]["available"] is True
    assert catalog["github"]["requires_vault"] is True

    monkeypatch.setenv("NAXOS_MCP_SLACK_URL", "https://naxos-mcp-slack-1.a.run.app")
    slack = {c["name"]: c for c in connectors.entries()}["slack"]
    assert slack["available"] is True
    # The service URL alone is not the MCP endpoint: the path is appended.
    assert slack["url"] == "https://naxos-mcp-slack-1.a.run.app" + slack["path"]
    assert slack["requires_vault"] is False


async def test_every_catalog_entry_is_attachable(client):
    """The catalog feeds the agent form directly, so each entry must be a config
    the agent-version validator accepts."""
    env = (await client.post("/v1/environments", json=ENV)).json()
    for entry in connectors.CATALOG:
        url = entry["url"] if entry["shape"] == "remote" else f"https://x.run.app{entry['path']}"
        response = await client.post(
            "/v1/agents",
            json={
                "environment_id": env["id"],
                "name": f"uses-{entry['name']}",
                "model": "claude-sonnet-5",
                "mcp_servers": {entry["name"]: {"type": entry["type"], "url": url}},
            },
        )
        assert response.status_code == 201, (entry["name"], response.text)


async def test_mcp_servers_rejects_stdio_configs(client):
    env = (await client.post("/v1/environments", json=ENV)).json()
    response = await client.post(
        "/v1/agents",
        json={
            "environment_id": env["id"],
            "name": "ops",
            "model": "claude-sonnet-5",
            "mcp_servers": {"local": {"command": "python", "args": ["server.py"]}},
        },
    )
    assert response.status_code == 422


async def test_mcp_servers_rejects_reserved_names_and_bad_urls(client):
    env = (await client.post("/v1/environments", json=ENV)).json()
    base = {"environment_id": env["id"], "name": "ops", "model": "claude-sonnet-5"}

    reserved = await client.post(
        "/v1/agents",
        json={**base, "mcp_servers": {"artifacts": {"url": "https://example.com/mcp"}}},
    )
    assert reserved.status_code == 422

    bad_url = await client.post(
        "/v1/agents", json={**base, "mcp_servers": {"x": {"url": "ftp://example.com"}}}
    )
    assert bad_url.status_code == 422

    ok = await client.post(
        "/v1/agents",
        json={**base, "mcp_servers": {"github": {"url": "https://api.githubcopilot.com/mcp/"}}},
    )
    assert ok.status_code == 201
    assert ok.json()["mcp_servers"]["github"] == {
        "type": "http",
        "url": "https://api.githubcopilot.com/mcp/",
    }


async def test_env_credentials_are_rejected(client):
    vault = (await client.post("/v1/vaults", json={"name": "creds"})).json()
    response = await client.post(
        f"/v1/vaults/{vault['id']}/credentials",
        json={"name": "token", "type": "env", "value": "x", "target": {"env_var": "TOKEN"}},
    )
    assert response.status_code == 422


async def test_terminate_deletes_egress_routes(
    client, internal_client, launched, monkeypatch, pool
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
            "target": {"mcp_server": "github"},
        },
    )
    _, agent = await make_agent(
        client,
        mcp_servers={"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}},
        vault_ids=[vault["id"]],
    )
    session = (
        await client.post(
            "/v1/sessions", json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]}
        )
    ).json()

    await internal_client.post(f"/internal/sessions/{session['id']}/claim")
    resolved = (await internal_client.get(f"/internal/sessions/{session['id']}/config")).json()
    assert resolved["mcp_servers"]["github"]["url"].startswith("https://egress.example/r/")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM egress_routes WHERE session_id = $1", session["id"]
        )
    assert count == 1

    await client.post(f"/v1/sessions/{session['id']}/terminate")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM egress_routes WHERE session_id = $1", session["id"]
        )
    assert count == 0
