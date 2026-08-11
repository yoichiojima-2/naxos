import json
from types import SimpleNamespace

import httpx
import pytest
from naxos_shared.events import SessionConfig

from naxos_sbx import bigquery
from naxos_sbx.bigquery import BigQueryError, BigQueryTools, read_only_violation


class _Tools(BigQueryTools):
    """BigQueryTools with the REST transport replaced by canned responses."""

    def __init__(self, responses=None, datasets=("analytics",), **kwargs):
        super().__init__("naxos-503510", list(datasets), **kwargs)
        self.responses = list(responses or [])
        self.calls = []
        # Pre-resolved so only the location test pays for the lookup.
        self._location = ""

    async def call(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if not self.responses:
            raise AssertionError(f"unexpected call {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _query_result(rows, fields, total=None, scanned="1024"):
    return {
        "jobComplete": True,
        "totalRows": str(total if total is not None else len(rows)),
        "totalBytesProcessed": scanned,
        "schema": {"fields": fields},
        "rows": rows,
    }


# --- read-only guard --------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  select id from t where d = @d ",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT 1;",
        "-- a comment\nSELECT 1",
        "SELECT 'a;b' AS s",
    ],
)
def test_read_only_accepts_selects(sql):
    assert read_only_violation(sql) is None


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("DELETE FROM t WHERE 1=1", "read-only"),
        ("CREATE TABLE t AS SELECT 1", "read-only"),
        ("SELECT 1; DROP TABLE t", "multi-statement"),
        ("/* SELECT 1 */ UPDATE t SET a = 1", "read-only"),
        ("   ", "empty"),
    ],
)
def test_read_only_refuses_writes_and_scripts(sql, expected):
    violation = read_only_violation(sql)
    assert violation is not None and expected in violation


# --- discovery --------------------------------------------------------------


async def test_list_datasets_names_the_opted_in_datasets():
    tools = _Tools(datasets=["analytics", "sales"])
    result = await tools.list_datasets({})
    assert "analytics, sales" in result["content"][0]["text"]


async def test_list_tables_rejects_a_dataset_outside_the_grant():
    tools = _Tools()
    with pytest.raises(BigQueryError) as exc:
        await tools.list_tables({"dataset": "secrets"})
    assert "not available to this environment" in str(exc.value)
    assert "analytics" in str(exc.value)
    assert tools.calls == []


async def test_list_tables_returns_table_ids():
    tools = _Tools([{"tables": [{"tableReference": {"tableId": "events"}, "type": "TABLE"}]}])
    result = await tools.list_tables({"dataset": "analytics"})
    assert json.loads(result["content"][0]["text"]) == [{"table": "events", "type": "TABLE"}]


async def test_describe_table_reports_partitioning_and_nested_schema():
    tools = _Tools(
        [
            {
                "type": "TABLE",
                "numRows": "42",
                "numBytes": "2048",
                "timePartitioning": {"field": "event_date"},
                "clustering": {"fields": ["user_id"]},
                "schema": {
                    "fields": [
                        {"name": "event_date", "type": "DATE", "mode": "REQUIRED"},
                        {
                            "name": "user",
                            "type": "RECORD",
                            "fields": [{"name": "id", "type": "STRING"}],
                        },
                    ]
                },
            }
        ]
    )
    body = (await tools.describe_table({"dataset": "analytics", "table": "events"}))["content"][0][
        "text"
    ]
    assert "partitioned on event_date" in body
    assert "clustered by user_id" in body
    assert "event_date: DATE REQUIRED" in body
    assert "user.id: STRING" in body


# --- query ------------------------------------------------------------------


async def test_query_dry_runs_before_executing_and_flattens_rows():
    tools = _Tools(
        [
            {"totalBytesProcessed": "2048"},
            _query_result(
                [{"f": [{"v": "a"}, {"v": [{"v": "x"}, {"v": "y"}]}]}],
                [
                    {"name": "id", "type": "STRING"},
                    {"name": "tags", "type": "STRING", "mode": "REPEATED"},
                ],
            ),
        ]
    )
    result = await tools.query({"query": "SELECT id, tags FROM analytics.events"})
    text = result["content"][0]["text"]
    assert not result.get("is_error")
    assert tools.calls[0][3]["dryRun"] is True
    assert tools.calls[1][3]["maximumBytesBilled"] == str(tools.max_bytes_billed)
    assert json.loads(text.split("\n\n", 1)[1]) == [{"id": "a", "tags": ["x", "y"]}]


async def test_query_refuses_when_the_estimate_exceeds_the_cap():
    tools = _Tools([{"totalBytesProcessed": str(4 * 1024**3)}])
    result = await tools.query({"query": "SELECT * FROM analytics.events"})
    assert result["is_error"]
    assert "over the" in result["content"][0]["text"]
    assert len(tools.calls) == 1


async def test_dry_run_reports_the_estimate_without_executing():
    tools = _Tools([{"totalBytesProcessed": str(3 * 1024**2)}])
    result = await tools.query({"query": "SELECT 1", "dry_run": True})
    assert "3.0 MiB" in result["content"][0]["text"]
    assert len(tools.calls) == 1


async def test_query_refuses_writes_without_touching_bigquery():
    tools = _Tools()
    result = await tools.query({"query": "DELETE FROM analytics.events"})
    assert result["is_error"]
    assert tools.calls == []


async def test_query_sends_named_parameters_rather_than_inlined_values():
    tools = _Tools([{"totalBytesProcessed": "1"}, _query_result([], [])])
    await tools.query(
        {"query": "SELECT 1 WHERE d = @d AND n = @n", "parameters": {"d": "2026-01-01", "n": 5}}
    )
    sent = tools.calls[0][3]["queryParameters"]
    assert sent[0] == {
        "name": "d",
        "parameterType": {"type": "STRING"},
        "parameterValue": {"value": "2026-01-01"},
    }
    assert sent[1]["parameterType"] == {"type": "INT64"}


async def test_query_caps_requested_rows_and_says_so():
    tools = _Tools(
        [
            {"totalBytesProcessed": "1"},
            _query_result([{"f": [{"v": "1"}]}], [{"name": "n", "type": "INT64"}], total=900),
        ],
        max_rows=10,
    )
    result = await tools.query({"query": "SELECT n FROM analytics.events", "max_rows": 5000})
    assert tools.calls[1][3]["maxResults"] == 10
    assert "Only the first 1 are shown" in result["content"][0]["text"]


async def test_query_polls_until_the_job_completes():
    tools = _Tools(
        [
            {"totalBytesProcessed": "1"},
            {
                "jobComplete": False,
                "jobReference": {"jobId": "job_1", "location": "asia-northeast1"},
            },
            _query_result([], [{"name": "n", "type": "INT64"}]),
        ]
    )
    result = await tools.query({"query": "SELECT n FROM analytics.events"})
    assert not result.get("is_error")
    assert tools.calls[2][0] == "GET"
    assert tools.calls[2][2]["location"] == "asia-northeast1"


async def test_location_resolves_from_the_readable_datasets_and_is_cached():
    tools = _Tools(
        [
            {"location": "asia-northeast1"},
            {"totalBytesProcessed": "1"},
            _query_result([], []),
            {"totalBytesProcessed": "1"},
            _query_result([], []),
        ]
    )
    tools._location = None
    await tools.query({"query": "SELECT 1"})
    await tools.query({"query": "SELECT 1"})
    assert tools.calls[1][3]["location"] == "asia-northeast1"
    assert [call[0] for call in tools.calls].count("GET") == 1


# --- errors -----------------------------------------------------------------


def test_denied_access_tells_the_agent_to_stop_rather_than_retry():
    tools = _Tools()
    message = tools._api_message(
        httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "accessDenied"}], "message": "no access"}},
        )
    )
    assert "accessDenied" in message
    assert "analytics" in message
    assert "retrying will not help" in message


# --- registration -----------------------------------------------------------


def test_no_server_without_an_opt_in():
    assert bigquery.build_server(project="p", datasets=[]) is None
    assert bigquery.build_server(project="", datasets=["analytics"]) is None


def _config(**overrides) -> SessionConfig:
    base = {
        "session_id": "session_x",
        "agent_id": "agent_x",
        "agent_version": 1,
        "environment_id": "env_x",
        "model": "claude-sonnet-5",
        "session_bucket": "bucket-x",
    }
    return SessionConfig.model_validate({**base, **overrides})


def test_harness_omits_bigquery_when_the_environment_is_not_opted_in(monkeypatch):
    from naxos_sbx.harness import Harness

    monkeypatch.setattr(bigquery, "BIGQUERY_DATASETS", [])
    options = Harness(SimpleNamespace(session_id="session_x"), _config(), "/tmp").options()
    assert "bigquery" not in options.mcp_servers


def test_harness_exposes_bigquery_when_datasets_are_granted(monkeypatch):
    from naxos_sbx.harness import Harness

    monkeypatch.setattr(bigquery, "BIGQUERY_DATASETS", ["analytics"])
    monkeypatch.setattr(bigquery, "GCLOUD_PROJECT_ID", "naxos-503510")
    options = Harness(SimpleNamespace(session_id="session_x"), _config(), "/tmp").options()
    assert "bigquery" in options.mcp_servers
