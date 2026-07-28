import json
from datetime import UTC, datetime
from unittest.mock import Mock

from naxos.agent import AgentRun
from naxos.audit import log_run


def make_client(monkeypatch) -> Mock:
    bq_cls = Mock()
    monkeypatch.setattr("naxos.audit.BigQuery", bq_cls)
    client = bq_cls.return_value.client
    client.insert_rows_json.return_value = []
    return client


def test_log_run_inserts_row(monkeypatch):
    client = make_client(monkeypatch)
    run = AgentRun(
        text="answer",
        tool_calls=[{"name": "query_bigquery", "input": {"sql": "select 1"}}],
        session_id="s1",
        cost_usd=0.05,
        num_turns=2,
        usage={"input_tokens": 100},
    )
    started_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    run_id = log_run("prompt", run, started_at)

    table, rows = client.insert_rows_json.call_args.args
    assert table == "audit.runs"
    row = rows[0]
    assert row["run_id"] == run_id
    assert row["session_id"] == "s1"
    assert row["started_at"] == "2026-07-28T12:00:00+00:00"
    assert row["prompt"] == "prompt"
    assert row["text"] == "answer"
    assert json.loads(row["tool_calls"]) == run.tool_calls
    assert json.loads(row["usage"]) == run.usage
    assert row["cost_usd"] == 0.05
    assert row["is_error"] is False


def test_log_run_survives_insert_errors(monkeypatch):
    client = make_client(monkeypatch)
    client.insert_rows_json.return_value = [{"errors": ["boom"]}]

    run_id = log_run("prompt", AgentRun(), datetime.now(UTC))

    assert run_id
