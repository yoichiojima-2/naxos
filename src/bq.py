import json
import logging
import os
from dataclasses import dataclass
from functools import cached_property

from google.cloud import bigquery
from google.cloud.bigquery import Client

logger = logging.getLogger(__name__)


@dataclass
class BigQuery:
    """BigQuery wrapper designed to be handed to an agent as tools.

    Guardrails live in code, not in the prompt: every query carries a
    scan-size cap (rejected by BigQuery before running), a timeout, and a
    row cap on what comes back. Write access is expected to be blocked by
    IAM (dataViewer + jobUser only), not by this class.
    """

    project: str | None = os.environ.get("GCLOUD_PROJECT_ID")
    max_bytes_billed: int = 10 * 1024**3  # 10 GB scan cap per query
    max_rows: int = 200
    timeout_seconds: float = 60.0

    @cached_property
    def client(self) -> Client:
        return bigquery.Client(project=self.project)

    def query(self, sql: str, max_rows: int | None = None) -> list[dict]:
        logger.info(f"query: {sql[:200]}")
        job_config = bigquery.QueryJobConfig(maximum_bytes_billed=self.max_bytes_billed)
        rows = self.client.query(sql, job_config=job_config).result(
            timeout=self.timeout_seconds,
            max_results=max_rows or self.max_rows,
        )
        return [dict(row) for row in rows]

    def dry_run(self, sql: str) -> dict:
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = self.client.query(sql, job_config=job_config)
        return {
            "valid": True,
            "estimated_scan_mb": round(job.total_bytes_processed / 1024**2, 1),
            "within_limit": job.total_bytes_processed <= self.max_bytes_billed,
        }

    def list_datasets(self) -> list[str]:
        return [d.dataset_id for d in self.client.list_datasets()]

    def list_tables(self, dataset: str) -> list[str]:
        return [t.table_id for t in self.client.list_tables(dataset)]

    def get_table_info(self, table: str) -> dict:
        t = self.client.get_table(table)
        return {
            "table": f"{t.project}.{t.dataset_id}.{t.table_id}",
            "description": t.description,
            "num_rows": t.num_rows,
            "size_mb": round(t.num_bytes / 1024**2, 1),
            "created": t.created.isoformat(),
            "modified": t.modified.isoformat(),
            "partitioning": t.time_partitioning.field if t.time_partitioning else None,
            "clustering": t.clustering_fields,
            "schema": [{"name": f.name, "type": f.field_type, "description": f.description} for f in t.schema],
        }

    def mcp(self):
        from claude_agent_sdk import create_sdk_mcp_server

        return create_sdk_mcp_server(name="bq", version="1.0.0", tools=self.tools())

    def tools(self) -> list:
        """Agent-facing tools for the Claude Agent SDK (in-process MCP).

        Errors come back as tool results instead of raising: BigQuery's
        error messages are specific enough that the model reads them and
        fixes the query on the next turn.
        """
        from claude_agent_sdk import tool

        def _result(text: str) -> dict:
            return {"content": [{"type": "text", "text": text}]}

        def _dumps(obj) -> str:
            return json.dumps(obj, default=str, ensure_ascii=False)

        @tool(
            "query_bigquery",
            "Run a read-only BigQuery standard SQL query and return rows as "
            "JSON. Scan size and result rows are capped: aggregate or LIMIT "
            "rather than selecting everything, and check the schema with "
            "get_table_info first when unsure of column names.",
            {"sql": str},
        )
        async def query_bigquery(args):
            try:
                return _result(_dumps(self.query(args["sql"])))
            except Exception as e:
                return _result(f"Query failed: {e}")

        @tool(
            "get_table_info",
            "Get a BigQuery table's schema, row count, size, and "
            "partitioning. Call this before writing SQL against an "
            "unfamiliar table. Table ID must be fully qualified, e.g. "
            "'project.dataset.table'.",
            {"table": str},
        )
        async def get_table_info(args):
            try:
                return _result(_dumps(self.get_table_info(args["table"])))
            except Exception as e:
                return _result(f"Failed: {e}")

        @tool(
            "list_tables",
            "List the tables in a BigQuery dataset of the current project. "
            "Dataset name goes without the project prefix, e.g. 'audit'.",
            {"dataset": str},
        )
        async def list_tables(args):
            try:
                return _result(_dumps(self.list_tables(args["dataset"])))
            except Exception as e:
                return _result(f"Failed: {e}")

        return [query_bigquery, get_table_info, list_tables]
