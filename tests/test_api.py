from unittest.mock import Mock

from fastapi.testclient import TestClient

from naxos import api
from naxos.agent import AgentRun
from naxos.runner import RoleDisabled

client = TestClient(api.app)


def test_roles():
    assert client.get("/api/roles").json() == sorted(api.ROLES)


def test_run_returns_result(monkeypatch):
    captured = {}

    async def fake_execute(prompt, role, resume=None, principal=None, fresh_ws=False, **kwargs):
        captured.update(prompt=prompt, role=role, resume=resume, principal=principal, fresh_ws=fresh_ws)
        return AgentRun(text="hi", session_id="s1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(api, "execute", fake_execute)

    response = client.post("/api/run", json={"prompt": "hello", "role": "analyst", "resume": "s0"})

    assert response.status_code == 200
    assert response.json()["text"] == "hi"
    assert response.json()["session_id"] == "s1"
    assert captured == {"prompt": "hello", "role": "analyst", "resume": "s0", "principal": "local-dev", "fresh_ws": True}


def test_run_requires_iap_when_audience_set(monkeypatch):
    monkeypatch.setattr(api, "IAP_AUDIENCE", "/projects/1/services/x")

    response = client.post("/api/run", json={"prompt": "x", "role": "ops"})

    assert response.status_code == 401


def test_run_rejects_unknown_role():
    assert client.post("/api/run", json={"prompt": "x", "role": "nope"}).status_code == 400


def test_run_conflict_when_disabled(monkeypatch):
    async def fake_execute(*args, **kwargs):
        raise RoleDisabled("role ops is disabled")

    monkeypatch.setattr(api, "execute", fake_execute)

    assert client.post("/api/run", json={"prompt": "x", "role": "ops"}).status_code == 409


def test_run_404_when_session_missing(monkeypatch):
    async def fake_execute(*args, **kwargs):
        raise FileNotFoundError("gs://b/s9/transcript.jsonl not found")

    monkeypatch.setattr(api, "execute", fake_execute)

    assert client.post("/api/run", json={"prompt": "x", "role": "ops", "resume": "s9"}).status_code == 404


def test_runs_history(monkeypatch):
    recent = Mock(return_value=[{"run_id": "r1"}])
    monkeypatch.setattr(api.audit, "recent_runs", recent)

    assert client.get("/api/runs").json() == [{"run_id": "r1"}]
    assert recent.call_args.args == (50,)


def test_runs_limit_validated():
    assert client.get("/api/runs?limit=9999").status_code == 422
