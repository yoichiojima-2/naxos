import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import Mock

from naxos.bq import BigQuery


def make_bq() -> BigQuery:
    bq = BigQuery(project="test-project")
    bq.client = Mock()
    return bq


def test_query_applies_guardrails():
    bq = make_bq()
    bq.client.query.return_value.result.return_value = [{"n": 1}, {"n": 2}]

    rows = bq.query("select n from t")

    assert rows == [{"n": 1}, {"n": 2}]
    job_config = bq.client.query.call_args.kwargs["job_config"]
    assert job_config.maximum_bytes_billed == bq.max_bytes_billed
    result_kwargs = bq.client.query.return_value.result.call_args.kwargs
    assert result_kwargs["max_results"] == bq.max_rows
    assert result_kwargs["timeout"] == bq.timeout_seconds


def test_query_max_rows_override():
    bq = make_bq()
    bq.client.query.return_value.result.return_value = []

    bq.query("select 1", max_rows=5)

    assert bq.client.query.return_value.result.call_args.kwargs["max_results"] == 5


def test_dry_run_reports_scan_estimate():
    bq = make_bq()
    bq.client.query.return_value.total_bytes_processed = 5 * 1024**2

    assert bq.dry_run("select 1") == {"valid": True, "estimated_scan_mb": 5.0, "within_limit": True}


def test_dry_run_flags_over_limit():
    bq = make_bq()
    bq.client.query.return_value.total_bytes_processed = bq.max_bytes_billed + 1

    assert bq.dry_run("select *")["within_limit"] is False


def test_get_table_info():
    bq = make_bq()
    table = Mock(
        project="p",
        dataset_id="d",
        table_id="t",
        description="desc",
        num_rows=10,
        num_bytes=2 * 1024**2,
        created=datetime(2026, 1, 1, tzinfo=UTC),
        modified=datetime(2026, 7, 1, tzinfo=UTC),
        time_partitioning=Mock(field="started_at"),
        clustering_fields=None,
        schema=[Mock(field_type="STRING", description=None)],
    )
    table.schema[0].name = "run_id"
    bq.client.get_table.return_value = table

    info = bq.get_table_info("p.d.t")

    assert info["table"] == "p.d.t"
    assert info["size_mb"] == 2.0
    assert info["partitioning"] == "started_at"
    assert info["schema"] == [{"name": "run_id", "type": "STRING", "description": None}]


def test_tool_returns_rows_as_json():
    bq = make_bq()
    bq.client.query.return_value.result.return_value = [{"n": 1}]
    query_tool = next(t for t in bq.tools() if t.name == "query_bigquery")

    result = asyncio.run(query_tool.handler({"sql": "select 1"}))

    assert json.loads(result["content"][0]["text"]) == [{"n": 1}]


def test_tool_returns_error_as_result():
    bq = make_bq()
    bq.client.query.side_effect = Exception("Unrecognized name: colunm")
    query_tool = next(t for t in bq.tools() if t.name == "query_bigquery")

    result = asyncio.run(query_tool.handler({"sql": "select colunm"}))

    assert "Query failed: Unrecognized name: colunm" in result["content"][0]["text"]
