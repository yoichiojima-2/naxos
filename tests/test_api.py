import json
from unittest.mock import Mock

from fastapi.testclient import TestClient

from naxos import api
from naxos.agent import AgentRun
from naxos.runner import RoleDisabled

client = TestClient(api.app)


def test_roles():
    assert client.get("/api/roles").json() == sorted(api.ROLES)


def test_me():
    assert client.get("/api/me").json() == {"email": "local-dev"}


def test_me_requires_iap_when_audience_set(monkeypatch):
    monkeypatch.setattr(api, "IAP_AUDIENCE", "/projects/1/services/x")

    assert client.get("/api/me").status_code == 401


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


def test_run_stream_emits_events_then_result(monkeypatch):
    async def fake_execute(prompt, role, resume=None, principal=None, fresh_ws=False, on_event=None, **kwargs):
        on_event({"event": "thinking"})
        on_event({"event": "tool", "name": "query_bigquery", "input": {"sql": "select 1"}})
        on_event(
            {
                "event": "tool",
                "name": "mcp__schedules__propose_schedule",
                "input": {"role": "ops", "cron": "0 9 * * *", "prompt": "check"},
            }
        )
        return AgentRun(text="hi", session_id="s1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(api, "execute", fake_execute)

    response = client.post("/api/run/stream", json={"prompt": "hello", "role": "analyst"})

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[0] == {"event": "thinking"}
    assert lines[1] == {"event": "tool", "name": "query_bigquery"}
    assert lines[2] == {"event": "proposal", "kind": "schedule", "role": "ops", "cron": "0 9 * * *", "prompt": "check"}
    assert lines[3]["event"] == "result"
    assert lines[3]["text"] == "hi"
    assert lines[3]["session_id"] == "s1"


def test_run_stream_reports_disabled_as_error_event(monkeypatch):
    async def fake_execute(*args, **kwargs):
        raise RoleDisabled("role ops is disabled")

    monkeypatch.setattr(api, "execute", fake_execute)

    response = client.post("/api/run/stream", json={"prompt": "x", "role": "ops"})

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines == [{"event": "error", "detail": "role ops is disabled"}]


def test_run_stream_requires_iap_when_audience_set(monkeypatch):
    monkeypatch.setattr(api, "IAP_AUDIENCE", "/projects/1/services/x")

    assert client.post("/api/run/stream", json={"prompt": "x", "role": "ops"}).status_code == 401


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


def test_artifacts(monkeypatch):
    storage = Mock()
    storage.list_all.return_value = ["ops/2026-07-28-report/report.html"]
    monkeypatch.setattr(api, "get_storage", Mock(return_value=storage))

    response = client.get("/api/artifacts")

    assert response.status_code == 200
    assert [a["title"] for a in response.json()] == ["report"]
    assert storage.list_all.call_args.args[0].endswith("-artifacts")


def test_artifacts_require_iap_when_audience_set(monkeypatch):
    monkeypatch.setattr(api, "IAP_AUDIENCE", "/projects/1/services/x")

    assert client.get("/api/artifacts").status_code == 401


def test_artifact_file(monkeypatch):
    storage = Mock()
    storage.read_bytes.return_value = (b"<h1>hi</h1>", "text/html")
    monkeypatch.setattr(api, "get_storage", Mock(return_value=storage))

    response = client.get("/artifacts/ops/2026-07-28-report/report.html")

    assert response.status_code == 200
    assert response.content == b"<h1>hi</h1>"
    assert response.headers["content-type"].startswith("text/html")
    assert storage.read_bytes.call_args.args == ("test-bucket-artifacts", "ops/2026-07-28-report/report.html")


def test_artifact_file_missing_is_404(monkeypatch):
    storage = Mock()
    storage.read_bytes.side_effect = FileNotFoundError("not found")
    monkeypatch.setattr(api, "get_storage", Mock(return_value=storage))

    assert client.get("/artifacts/ops/nope.html").status_code == 404


def test_artifact_file_requires_iap_when_audience_set(monkeypatch):
    monkeypatch.setattr(api, "IAP_AUDIENCE", "/projects/1/services/x")

    assert client.get("/artifacts/ops/report.html").status_code == 401


def test_schedules_list(monkeypatch):
    schedules = Mock()
    schedules.list.return_value = [
        {"id": "naxos-schedule-a1", "name": "n", "role": "ops", "cron": "0 9 * * *", "prompt": "p", "paused": False}
    ]
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    assert client.get("/api/schedules").json() == schedules.list.return_value


def test_schedule_create(monkeypatch):
    schedules = Mock()
    schedules.create.return_value = "naxos-schedule-a1"
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    response = client.post(
        "/api/schedules", json={"role": "ops", "name": "daily", "cron": "0 9 * * *", "prompt": "p", "paused": True}
    )

    assert response.status_code == 200
    assert response.json()["id"] == "naxos-schedule-a1"
    assert schedules.create.call_args.args == ("ops", "daily", "0 9 * * *", "p", True)


def test_schedule_create_rejects_unknown_role():
    body = {"role": "nope", "name": "n", "cron": "0 9 * * *", "prompt": "p"}

    assert client.post("/api/schedules", json=body).status_code == 400


def test_schedule_update(monkeypatch):
    schedules = Mock()
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    response = client.put(
        "/api/schedules/naxos-schedule-a1", json={"name": "n", "cron": "*/30 * * * *", "prompt": "new", "paused": True}
    )

    assert response.status_code == 200
    assert schedules.update.call_args.args == ("naxos-schedule-a1", "n", "*/30 * * * *", "new", True)


def test_schedule_update_maps_bad_cron_to_400(monkeypatch):
    from google.api_core.exceptions import InvalidArgument

    schedules = Mock()
    schedules.update.side_effect = InvalidArgument("Invalid cron expression")
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    response = client.put("/api/schedules/naxos-schedule-a1", json={"name": "n", "cron": "not a cron", "prompt": "p"})

    assert response.status_code == 400
    assert "cron" in response.json()["detail"]


def test_schedule_update_rejects_foreign_job():
    body = {"name": "n", "cron": "0 9 * * *", "prompt": "p"}

    assert client.put("/api/schedules/some-other-job", json=body).status_code == 404


def test_schedule_run(monkeypatch):
    schedules = Mock()
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    response = client.post("/api/schedules/naxos-schedule-a1/run")

    assert response.status_code == 200
    assert schedules.run.call_args.args == ("naxos-schedule-a1",)


def test_schedule_run_rejects_foreign_job():
    assert client.post("/api/schedules/some-other-job/run").status_code == 404


def test_schedule_run_maps_paused_to_404(monkeypatch):
    from google.api_core.exceptions import NotFound

    schedules = Mock()
    schedules.run.side_effect = NotFound("Job not found.")
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    assert client.post("/api/schedules/naxos-schedule-a1/run").status_code == 404


def test_schedule_delete(monkeypatch):
    schedules = Mock()
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    response = client.delete("/api/schedules/naxos-schedule-a1")

    assert response.status_code == 200
    assert schedules.delete.call_args.args == ("naxos-schedule-a1",)


def test_schedule_delete_rejects_foreign_job():
    assert client.delete("/api/schedules/some-other-job").status_code == 404


def test_schedule_delete_maps_missing_to_404(monkeypatch):
    from google.api_core.exceptions import NotFound

    schedules = Mock()
    schedules.delete.side_effect = NotFound("gone")
    monkeypatch.setattr(api, "get_schedules", Mock(return_value=schedules))

    assert client.delete("/api/schedules/naxos-schedule-a1").status_code == 404
