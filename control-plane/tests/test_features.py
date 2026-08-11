import pytest

from naxos_cp import skills, vaults

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


async def _make_session(client, agent):
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    return session["id"]


async def test_agent_created_schedule_is_a_governed_deployment(client, internal_client, launched):
    _, agent = await make_agent(client)
    session_id = await _make_session(client, agent)

    created = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "daily-digest", "cron": "3 8 * * *", "prompt": "Compile the digest."},
    )
    assert created.status_code == 201
    deployment = created.json()
    assert deployment["created_by"] == f"agent:{session_id}"

    operator_view = (await client.get("/v1/deployments")).json()["data"]
    assert [d["id"] for d in operator_view] == [deployment["id"]]
    assert operator_view[0]["agent_version"] is None

    run = (await client.post(f"/v1/deployments/{deployment['id']}/run")).json()
    assert run["status"] == "running"
    fired = (await client.get(f"/v1/sessions/{run['session_id']}/events")).json()["data"]
    assert fired[0]["payload"]["content"][0]["text"] == "Compile the digest."

    agent_view = (await internal_client.get(f"/internal/sessions/{session_id}/deployments")).json()[
        "data"
    ]
    assert [d["id"] for d in agent_view] == [deployment["id"]]
    assert agent_view[0]["prompt"] == "Compile the digest."

    archived = await internal_client.delete(
        f"/internal/sessions/{session_id}/deployments/{deployment['id']}"
    )
    assert archived.json()["archived"] is True
    assert (await client.get("/v1/deployments")).json()["data"] == []


async def test_agent_cannot_archive_operator_deployments(client, internal_client, launched):
    env, agent = await make_agent(client)
    session_id = await _make_session(client, agent)
    operator_deployment = (
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

    agent_view = (await internal_client.get(f"/internal/sessions/{session_id}/deployments")).json()[
        "data"
    ]
    assert [d["id"] for d in agent_view] == [operator_deployment["id"]]

    refused = await internal_client.delete(
        f"/internal/sessions/{session_id}/deployments/{operator_deployment['id']}"
    )
    assert refused.status_code == 403

    other_agent = (
        await client.post(
            "/v1/agents",
            json={
                "environment_id": env["id"],
                "name": "other",
                "model": "claude-sonnet-5",
                "instructions": "You do something else.",
            },
        )
    ).json()
    other_session = await _make_session(client, other_agent)
    not_yours = await internal_client.delete(
        f"/internal/sessions/{other_session}/deployments/{operator_deployment['id']}"
    )
    assert not_yours.status_code == 404


async def test_agent_schedule_validation_and_cap(client, internal_client, launched, monkeypatch):
    from naxos_cp import config as cp_config

    _, agent = await make_agent(client)
    session_id = await _make_session(client, agent)

    bad_cron = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "x", "cron": "every day at 8", "prompt": "p"},
    )
    assert bad_cron.status_code == 422

    over_budget = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "x", "cron": "0 8 * * *", "prompt": "p", "budget_usd": 1e9},
    )
    assert over_budget.status_code == 422

    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": True})
    killed = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "x", "cron": "0 8 * * *", "prompt": "p"},
    )
    assert killed.status_code == 409
    await client.patch(f"/v1/agents/{agent['id']}", json={"disabled": False})

    monkeypatch.setattr(cp_config, "MAX_AGENT_DEPLOYMENTS", 1)
    first = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "a", "cron": "0 8 * * *", "prompt": "p"},
    )
    assert first.status_code == 201
    capped = await internal_client.post(
        f"/internal/sessions/{session_id}/deployments",
        json={"name": "b", "cron": "0 9 * * *", "prompt": "p"},
    )
    assert capped.status_code == 409


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

    for bad_path in ["/tmp/escape", "a/../b", "a//b", "trailing/"]:
        rejected = await client.post(
            f"/v1/memory_stores/{store['id']}/memories",
            json={"path": bad_path, "content": "x"},
        )
        assert rejected.status_code == 400, bad_path


async def test_memory_store_rename_and_delete(client):
    store = (await client.post("/v1/memory_stores", json={"name": "runbooks"})).json()
    other = (await client.post("/v1/memory_stores", json={"name": "notes"})).json()

    renamed = await client.patch(f"/v1/memory_stores/{store['id']}", json={"name": "playbooks"})
    assert renamed.json()["name"] == "playbooks"
    dup = await client.patch(f"/v1/memory_stores/{store['id']}", json={"name": "notes"})
    assert dup.status_code == 409
    missing = await client.patch("/v1/memory_stores/nope", json={"name": "x"})
    assert missing.status_code == 404

    await client.post(
        f"/v1/memory_stores/{store['id']}/memories",
        json={"path": "notes.md", "content": "seed"},
    )
    _, agent = await make_agent(client, memory_store_ids=[store["id"]])
    listed = (await client.get("/v1/memory_stores")).json()["data"]
    by_id = {s["id"]: s for s in listed}
    assert by_id[store["id"]]["file_count"] == 1
    assert by_id[store["id"]]["used_by"] == [agent["name"]]
    assert by_id[other["id"]]["used_by"] == []

    attached = await client.delete(f"/v1/memory_stores/{store['id']}")
    assert attached.status_code == 409
    assert agent["name"] in attached.json()["detail"]

    detached = await client.post(
        f"/v1/agents/{agent['id']}/versions",
        json={
            "environment_id": agent["environment_id"],
            "name": agent["name"],
            "model": agent["model"],
            "instructions": agent["instructions"],
        },
    )
    assert detached.json()["memory_store_ids"] == []
    pinnable = await client.delete(f"/v1/memory_stores/{store['id']}")
    assert pinnable.status_code == 409

    await client.post(f"/v1/agents/{agent['id']}/archive")
    freed = await client.delete(f"/v1/memory_stores/{store['id']}")
    assert freed.json()["deleted"] is True

    deleted = await client.delete(f"/v1/memory_stores/{other['id']}")
    assert deleted.json() == {"id": other["id"], "deleted": True}
    assert (await client.delete(f"/v1/memory_stores/{other['id']}")).status_code == 404


async def test_memory_store_delete_blocked_by_active_session(client, launched):
    store = (await client.post("/v1/memory_stores", json={"name": "runbooks"})).json()
    _, agent = await make_agent(client)
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "memory_store_ids": [store["id"]]},
        )
    ).json()

    blocked = await client.delete(f"/v1/memory_stores/{store['id']}")
    assert blocked.status_code == 409
    assert "active session" in blocked.json()["detail"]

    await client.post(f"/v1/sessions/{session['id']}/terminate", json={})
    deleted = await client.delete(f"/v1/memory_stores/{store['id']}")
    assert deleted.json()["deleted"] is True


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


async def test_skill_round_trip_and_validation(client):
    bad_name = await client.post("/v1/skills", json={"name": "Deploy Helper"})
    assert bad_name.status_code == 422

    skill = (
        await client.post(
            "/v1/skills", json={"name": "deploy-helper", "description": "How we deploy"}
        )
    ).json()
    duplicate = await client.post("/v1/skills", json={"name": "deploy-helper"})
    assert duplicate.status_code == 409

    listed = (await client.get("/v1/skills")).json()["data"]
    assert [s["name"] for s in listed] == ["deploy-helper"]
    assert listed[0]["ready"] is False

    put = (
        await client.post(
            f"/v1/skills/{skill['id']}/files",
            json={"path": "SKILL.md", "content": "---\nname: deploy-helper\n---\nSteps."},
        )
    ).json()
    got = (await client.get(f"/v1/skills/{skill['id']}/files/{put['id']}")).json()
    assert got["content"].endswith("Steps.")
    assert (await client.get("/v1/skills")).json()["data"][0]["ready"] is True
    assert (await client.get(f"/v1/skills/{skill['id']}")).json()["ready"] is True

    too_big = await client.post(
        f"/v1/skills/{skill['id']}/files",
        json={"path": "big.md", "content": "x" * (256 * 1024 + 1)},
    )
    assert too_big.status_code == 413

    for path in ("/etc/evil", "a//b.md", "a/../b.md"):
        escaped = await client.post(
            f"/v1/skills/{skill['id']}/files", json={"path": path, "content": "x"}
        )
        assert escaped.status_code in (400, 422), path

    await client.post(f"/v1/skills/{skill['id']}/archive")
    assert (await client.get("/v1/skills")).json()["data"] == []
    rejected = await client.post(
        f"/v1/skills/{skill['id']}/files", json={"path": "more.md", "content": "no"}
    )
    assert rejected.status_code == 404

    reused = await client.post("/v1/skills", json={"name": "deploy-helper"})
    assert reused.status_code == 201


async def test_seed_samples_creates_once_and_never_overrides(pool, client, tmp_path):
    folder = tmp_path / "bigquery"
    (folder / "reference").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        '---\nname: bigquery\ndescription: "Query BigQuery."\n---\ndescription: not this one'
    )
    (folder / "reference" / "queries.md").write_text("recipes")
    (folder / ".hidden").write_text("ignored")
    (folder / "font.ttf").write_bytes(b"\x00\x01\xfe\xff")
    (folder / "huge.md").write_text("x" * (256 * 1024 + 1))
    (tmp_path / "no-entry").mkdir()

    async with pool.acquire() as conn:
        assert await skills.seed_samples(conn, tmp_path) == ["bigquery"]

    listed = (await client.get("/v1/skills")).json()["data"]
    assert [(s["name"], s["description"], s["ready"]) for s in listed] == [
        ("bigquery", "Query BigQuery.", True)
    ]
    skill = listed[0]
    files = (await client.get(f"/v1/skills/{skill['id']}/files")).json()["data"]
    assert [f["path"] for f in files] == ["SKILL.md", "reference/queries.md"]

    entry = next(f for f in files if f["path"] == "SKILL.md")
    await client.post(
        f"/v1/skills/{skill['id']}/files", json={"path": "SKILL.md", "content": "edited"}
    )
    async with pool.acquire() as conn:
        assert await skills.seed_samples(conn, tmp_path) == []
    kept = (await client.get(f"/v1/skills/{skill['id']}/files/{entry['id']}")).json()
    assert kept["content"] == "edited"

    await client.post(f"/v1/skills/{skill['id']}/archive")
    async with pool.acquire() as conn:
        assert await skills.seed_samples(conn, tmp_path) == []
    assert (await client.get("/v1/skills")).json()["data"] == []


def test_frontmatter_description_parsing():
    assert skills._frontmatter_description("no frontmatter\ndescription: nope") is None
    assert skills._frontmatter_description('---\ndescription: "quoted"\n---\n') == "quoted"
    assert (
        skills._frontmatter_description("---\ndescription: |-\n  two\n  lines\nlicense: x\n---\n")
        == "two lines"
    )
    assert skills._frontmatter_description("---\nname: x\n---\ndescription: body") is None


async def test_bundled_sample_skills_seed_from_the_repo(pool, client):
    async with pool.acquire() as conn:
        seeded = await skills.seed_samples(conn)
    assert "bigquery" in seeded
    listed = (await client.get("/v1/skills")).json()["data"]
    bigquery = next(s for s in listed if s["name"] == "bigquery")
    assert bigquery["ready"] is True
    assert bigquery["created_by"] == "system:seed"
    assert bigquery["description"].startswith("Query BigQuery")


async def _make_skill(client, name: str, ready: bool = True) -> dict:
    skill = (await client.post("/v1/skills", json={"name": name})).json()
    if ready:
        await client.post(
            f"/v1/skills/{skill['id']}/files",
            json={"path": "SKILL.md", "content": f"---\nname: {name}\n---\nUse me."},
        )
    return skill


async def test_session_inherits_agent_skills_and_mounts_them(client, internal_client, launched):
    skill = await _make_skill(client, "deploy-helper")
    await client.post(
        f"/v1/skills/{skill['id']}/files",
        json={"path": "scripts/run.sh", "content": "echo deploy"},
    )
    draft = await _make_skill(client, "draft-skill", ready=False)
    _, agent = await make_agent(client, skill_ids=[skill["id"], draft["id"]])
    assert agent["skill_ids"] == [skill["id"], draft["id"]]

    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    assert session["skill_ids"] == [skill["id"], draft["id"]]
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    config = (await internal_client.get(f"/internal/sessions/{session['id']}/config")).json()
    assert config["skill_names"] == ["deploy-helper"]

    mounted = (await internal_client.get(f"/internal/sessions/{session['id']}/skills")).json()
    assert set(mounted["skills"]) == {skill["id"]}
    assert mounted["skills"][skill["id"]]["name"] == "deploy-helper"
    assert mounted["skills"][skill["id"]]["files"]["scripts/run.sh"] == "echo deploy"


async def test_archived_skill_is_not_mounted(client, internal_client, launched):
    skill = await _make_skill(client, "deploy-helper")
    _, agent = await make_agent(client, skill_ids=[skill["id"]])
    session = (
        await client.post(
            "/v1/sessions",
            json={"agent": {"id": agent["id"]}, "initial_events": [MESSAGE]},
        )
    ).json()
    await client.post(f"/v1/skills/{skill['id']}/archive")
    await internal_client.post(f"/internal/sessions/{session['id']}/claim")

    config = (await internal_client.get(f"/internal/sessions/{session['id']}/config")).json()
    assert config["skill_names"] == []
    mounted = (await internal_client.get(f"/internal/sessions/{session['id']}/skills")).json()
    assert mounted["skills"] == {}


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
