"""BigQuery tools exposed to the agent as an in-process MCP server.

The sandbox image carries no bq or gcloud CLI, so this is the only BigQuery
path. Queries run as the environment's service account, which Terraform grants
read access to exactly the datasets listed under `bigquery_datasets` in
environments.json; the same list reaches the sandbox as BIGQUERY_DATASETS and
bounds discovery here. An environment without that opt-in gets no server at
all, so the tools do not exist rather than failing at call time. Every tool
call passes the PreToolUse permission gate like any other tool, so queries are
audited and subject to policy and the kill switch.
"""

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from .config import BIGQUERY_DATASETS, GCLOUD_PROJECT_ID, MAX_QUERY_BYTES_BILLED, MAX_QUERY_ROWS
from .mcp_result import error, guarded, text

log = logging.getLogger(__name__)

API_ROOT = "https://bigquery.googleapis.com/bigquery/v2"
SCOPE = "https://www.googleapis.com/auth/bigquery"
REQUEST_TIMEOUT = 120.0
QUERY_TIMEOUT_MS = 60_000
POLL_TIMEOUT_MS = 10_000
MAX_POLLS = 30
MAX_RESULT_CHARS = 100_000

# Comments and string literals are blanked before the leading keyword and the
# statement separator are read, so neither a comment nor a literal can hide a
# second statement behind an opening SELECT.
COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
STRING_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", re.DOTALL)

_credentials: Any = None


class BigQueryError(Exception):
    """A refusal or API error meant to be read by the agent."""


def _access_token() -> str:
    global _credentials
    import google.auth
    from google.auth.transport.requests import Request

    if _credentials is None:
        _credentials, _ = google.auth.default(scopes=[SCOPE])
    if not _credentials.valid:
        _credentials.refresh(Request())
    return _credentials.token


def read_only_violation(sql: str) -> str | None:
    body = COMMENT_RE.sub(" ", STRING_RE.sub("''", sql)).strip().rstrip(";").strip()
    if not body:
        return "the query is empty"
    if ";" in body:
        return "multi-statement scripts are not allowed — send one statement at a time"
    keyword = body.lstrip("( \t\r\n").split(None, 1)[0].upper()
    if keyword not in ("SELECT", "WITH"):
        return (
            f"only read-only SELECT queries are allowed, but this one starts with "
            f"'{keyword}'. This environment has read access only; no DML or DDL will run"
        )
    return None


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


def _query_parameters(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    typed = []
    for name, value in parameters.items():
        if isinstance(value, bool):
            type_, rendered = "BOOL", str(value).lower()
        elif isinstance(value, int):
            type_, rendered = "INT64", str(value)
        elif isinstance(value, float):
            type_, rendered = "FLOAT64", str(value)
        else:
            type_, rendered = "STRING", str(value)
        typed.append(
            {
                "name": name,
                "parameterType": {"type": type_},
                "parameterValue": {"value": rendered},
            }
        )
    return typed


def _cell(field: dict[str, Any], value: Any) -> Any:
    if field.get("mode") == "REPEATED":
        return [_scalar(field, item.get("v")) for item in (value or [])]
    return _scalar(field, value)


def _scalar(field: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    if field.get("type") in ("RECORD", "STRUCT"):
        return _row(field.get("fields") or [], value)
    return value


def _row(fields: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    cells = row.get("f") or []
    return {
        field.get("name") or f"f{index}": _cell(
            field, cells[index].get("v") if index < len(cells) else None
        )
        for index, field in enumerate(fields)
    }


def _flatten_schema(fields: list[dict[str, Any]], prefix: str = "") -> list[str]:
    lines = []
    for field in fields:
        name = f"{prefix}{field.get('name', '')}"
        mode = field.get("mode") or "NULLABLE"
        suffix = "" if mode == "NULLABLE" else f" {mode}"
        if field.get("description"):
            suffix += f" — {field['description']}"
        lines.append(f"{name}: {field.get('type', '')}{suffix}")
        if field.get("fields"):
            lines.extend(_flatten_schema(field["fields"], f"{name}."))
    return lines


def _bq_tool(handler):
    """Surface refusals and API errors verbatim; guarded() catches the rest."""

    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await handler(args)
        except BigQueryError as exc:
            return error(str(exc))

    return guarded(wrapped, "bigquery")


class BigQueryTools:
    def __init__(
        self,
        project: str,
        datasets: list[str],
        max_bytes_billed: int = MAX_QUERY_BYTES_BILLED,
        max_rows: int = MAX_QUERY_ROWS,
    ) -> None:
        self.project = project
        self.datasets = datasets
        self.max_bytes_billed = max_bytes_billed
        self.max_rows = max_rows
        self._locations: dict[str, str] = {}

    # --- transport --------------------------------------------------------

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await asyncio.to_thread(_access_token)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{API_ROOT}{path}",
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            raise BigQueryError(self._api_message(response))
        return response.json()

    def _api_message(self, response: httpx.Response) -> str:
        try:
            detail = response.json().get("error", {})
        except ValueError:
            detail = {}
        reason = (detail.get("errors") or [{}])[0].get("reason") or str(response.status_code)
        message = detail.get("message") or response.text[:500]
        if response.status_code in (401, 403):
            return (
                f"BigQuery denied the request ({reason}): {message}. This environment reads "
                f"only the datasets an operator opted it into ({self._available()}). Report "
                "which dataset you need and stop — retrying will not help."
            )
        return f"BigQuery rejected the request ({reason}): {message}"

    def _available(self) -> str:
        return ", ".join(self.datasets) if self.datasets else "none"

    def _check_dataset(self, dataset: str) -> None:
        if dataset not in self.datasets:
            raise BigQueryError(
                f"dataset '{dataset}' is not available to this environment. "
                f"Available datasets: {self._available()}."
            )

    async def location(self) -> str | None:
        """Job location, resolved from the datasets this environment can read.

        Jobs against a regional dataset fail with a confusing 'not found in
        location US' unless the location travels with the request, and it has
        to be known before the dry run, so it cannot be read off the query.

        Cached per dataset, not as a single answer: a dataset that failed to
        resolve is retried on the next call rather than leaving every later
        query without a location.
        """
        for dataset in self.datasets:
            if dataset in self._locations:
                continue
            try:
                meta = await self.call("GET", f"/projects/{self.project}/datasets/{dataset}")
            except BigQueryError:
                continue
            self._locations[dataset] = meta.get("location", "")
        found = {location for location in self._locations.values() if location}
        return found.pop() if len(found) == 1 else None

    # --- tools ------------------------------------------------------------

    async def list_datasets(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.datasets:
            return text("This environment has no BigQuery datasets.")
        return text(
            f"Readable datasets in project {self.project}: {self._available()}. "
            "Access is read-only and an operator must widen it in environments.json."
        )

    async def list_tables(self, args: dict[str, Any]) -> dict[str, Any]:
        dataset = args["dataset"]
        self._check_dataset(dataset)
        payload = await self.call(
            "GET",
            f"/projects/{self.project}/datasets/{dataset}/tables",
            params={"maxResults": 1000},
        )
        tables = payload.get("tables") or []
        if not tables:
            return text(f"Dataset {dataset} has no tables.")
        rows = [
            {
                "table": entry.get("tableReference", {}).get("tableId", ""),
                "type": entry.get("type", "TABLE"),
            }
            for entry in tables
        ]
        return text(json.dumps(rows, indent=2))

    async def describe_table(self, args: dict[str, Any]) -> dict[str, Any]:
        dataset, table = args["dataset"], args["table"]
        self._check_dataset(dataset)
        meta = await self.call("GET", f"/projects/{self.project}/datasets/{dataset}/tables/{table}")
        lines = [f"{self.project}.{dataset}.{table} ({meta.get('type', 'TABLE')})"]
        if meta.get("description"):
            lines.append(meta["description"])
        lines.append(
            f"rows: {meta.get('numRows', 'unknown')}, "
            f"size: {human_bytes(int(meta.get('numBytes') or 0))}"
        )
        partitioning = meta.get("timePartitioning") or meta.get("rangePartitioning")
        if partitioning:
            column = partitioning.get("field") or "_PARTITIONTIME"
            lines.append(f"partitioned on {column} — always filter on it to limit the scan")
        clustering = (meta.get("clustering") or {}).get("fields")
        if clustering:
            lines.append(f"clustered by {', '.join(clustering)}")
        lines.append("")
        lines.extend(_flatten_schema((meta.get("schema") or {}).get("fields") or []))
        return text("\n".join(lines))

    async def query(self, args: dict[str, Any]) -> dict[str, Any]:
        sql = args["query"]
        violation = read_only_violation(sql)
        if violation:
            return error(f"Refused: {violation}.")

        location = args.get("location") or await self.location()
        parameters = _query_parameters(args.get("parameters") or {})
        base: dict[str, Any] = {"query": sql, "useLegacySql": False}
        if location:
            base["location"] = location
        if parameters:
            base["parameterMode"] = "NAMED"
            base["queryParameters"] = parameters

        estimate = await self.call(
            "POST", f"/projects/{self.project}/queries", body={**base, "dryRun": True}
        )
        scanned = int(estimate.get("totalBytesProcessed") or 0)
        if args.get("dry_run"):
            return text(
                f"Dry run only — nothing was executed. This query would scan "
                f"{human_bytes(scanned)}; the per-query cap is "
                f"{human_bytes(self.max_bytes_billed)}."
            )
        if scanned > self.max_bytes_billed:
            return error(
                f"Refused: the query would scan {human_bytes(scanned)}, over the "
                f"{human_bytes(self.max_bytes_billed)} per-query cap. Narrow it — select "
                "fewer columns, filter the partitioning column, or aggregate server-side."
            )

        max_rows = max(1, min(int(args.get("max_rows") or self.max_rows), self.max_rows))
        result = await self.call(
            "POST",
            f"/projects/{self.project}/queries",
            body={
                **base,
                "maximumBytesBilled": str(self.max_bytes_billed),
                "maxResults": max_rows,
                "timeoutMs": QUERY_TIMEOUT_MS,
            },
        )
        result = await self._await_completion(result, max_rows)
        if result is None:
            return error(
                "The query did not finish in time. Narrow it or aggregate more before retrying."
            )
        return text(self._format(result, scanned, max_rows))

    async def _await_completion(
        self, result: dict[str, Any], max_rows: int
    ) -> dict[str, Any] | None:
        for _ in range(MAX_POLLS):
            if result.get("jobComplete"):
                return result
            reference = result.get("jobReference") or {}
            job_id = reference.get("jobId")
            if not job_id:
                return None
            params: dict[str, Any] = {"maxResults": max_rows, "timeoutMs": POLL_TIMEOUT_MS}
            if reference.get("location"):
                params["location"] = reference["location"]
            result = await self.call(
                "GET", f"/projects/{self.project}/queries/{job_id}", params=params
            )
        return result if result.get("jobComplete") else None

    def _format(self, result: dict[str, Any], scanned: int, max_rows: int) -> str:
        fields = (result.get("schema") or {}).get("fields") or []
        rows = [_row(fields, row) for row in (result.get("rows") or [])]
        total = int(result.get("totalRows") or len(rows))
        payload = json.dumps(rows, indent=2, default=str)
        truncated = len(payload) > MAX_RESULT_CHARS
        if truncated:
            payload = payload[:MAX_RESULT_CHARS]
        header = f"{total} row(s) matched, {len(rows)} returned; scanned {human_bytes(scanned)}."
        if total > len(rows):
            header += (
                f" Only the first {len(rows)} are shown (max_rows={max_rows}) — aggregate or "
                "paginate with LIMIT/OFFSET in SQL rather than asking for more rows."
            )
        if truncated:
            header += " The result body was truncated; select fewer columns."
        return f"{header}\n\n{payload}"


def build_server(project: str | None = None, datasets: list[str] | None = None) -> Any | None:
    """The bigquery MCP server, or None when the environment has no opt-in."""
    project = GCLOUD_PROJECT_ID if project is None else project
    datasets = BIGQUERY_DATASETS if datasets is None else datasets
    if not project or not datasets:
        return None
    tools_ = BigQueryTools(project, datasets)
    available = ", ".join(datasets)

    list_datasets = tool(
        "bigquery_list_datasets",
        "List the BigQuery datasets this environment can read. Read-only access is "
        "granted per environment by an operator; anything not listed is unreachable.",
        {"type": "object", "properties": {}},
    )(_bq_tool(tools_.list_datasets))

    list_tables = tool(
        "bigquery_list_tables",
        f"List the tables and views in a readable dataset ({available}).",
        {
            "type": "object",
            "properties": {"dataset": {"type": "string", "description": "dataset id"}},
            "required": ["dataset"],
        },
    )(_bq_tool(tools_.list_tables))

    describe_table = tool(
        "bigquery_describe_table",
        "Show a table's schema, row count, size, partitioning, and clustering. Read this "
        "before writing a query so the column names and the partitioning filter are right.",
        {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": "dataset id"},
                "table": {"type": "string", "description": "table or view id"},
            },
            "required": ["dataset", "table"],
        },
    )(_bq_tool(tools_.describe_table))

    query = tool(
        "bigquery_query",
        "Run a read-only GoogleSQL SELECT against BigQuery and return the rows. The "
        f"readable datasets are {available}. Every call is size-checked first and refused "
        "if it would scan more than the per-query cap, so filter the partitioning column "
        "and name the columns you need instead of SELECT *. Pass values through "
        "`parameters` as @name placeholders rather than string-formatting them into the "
        "SQL. Use dry_run to size an exploratory query without executing it.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "a single GoogleSQL SELECT (or WITH) statement",
                },
                "parameters": {
                    "type": "object",
                    "description": "named query parameters, referenced as @name in the SQL",
                },
                "max_rows": {
                    "type": "integer",
                    "description": f"rows to return, capped at {MAX_QUERY_ROWS}",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "estimate the scan size without running the query",
                },
                "location": {
                    "type": "string",
                    "description": "BigQuery location, only if the default resolution fails",
                },
            },
            "required": ["query"],
        },
    )(_bq_tool(tools_.query))

    return create_sdk_mcp_server(
        name="bigquery",
        version="1.0.0",
        tools=[list_datasets, list_tables, describe_table, query],
    )
