import asyncio
import functools
import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
from google.cloud import bigquery

from . import config, db, store

log = logging.getLogger(__name__)

JPY_PER_USD = 155.0


@functools.cache
def _bq() -> bigquery.Client:
    return bigquery.Client(project=config.PROJECT_ID)


async def _insert(table: str, rows: list[dict[str, Any]], row_ids: list[str] | None = None) -> bool:
    """True when the rows are durable in BigQuery (or auditing is switched off)."""
    if not rows:
        return True
    if not config.PROJECT_ID:
        log.debug("audit skipped (no project): %s %s", table, rows)
        return True
    target = f"{config.PROJECT_ID}.{config.AUDIT_DATASET}.{table}"
    # row_ids are BigQuery insertIds: a retried export of an already-accepted row
    # is de-duplicated rather than double-counted.
    kwargs = {"row_ids": row_ids} if row_ids else {}
    try:
        errors = await asyncio.to_thread(_bq().insert_rows_json, target, rows, **kwargs)
    except Exception:
        log.exception("audit insert raised table=%s", table)
        return False
    if errors:
        log.error("audit insert failed table=%s errors=%s", table, errors)
        return False
    return True


async def log_run(
    run_id: str,
    session_id: str,
    agent_id: str,
    environment_id: str,
    principal: str | None,
    trigger_type: str,
    started_at: datetime,
    status: str,
    stop_reason: str | None = None,
    num_turns: int = 0,
    cost_usd: float = 0.0,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    deployment_run_id: str | None = None,
) -> None:
    await _insert(
        "runs",
        [
            {
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "environment_id": environment_id,
                "deployment_run_id": deployment_run_id,
                "trigger_type": trigger_type,
                "principal": principal,
                "started_at": started_at.isoformat(),
                "ended_at": datetime.now(UTC).isoformat(),
                "status": status,
                "stop_reason": stop_reason,
                "num_turns": num_turns,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "approx_cost_jpy": round(cost_usd * JPY_PER_USD, 2),
                "model": model,
            }
        ],
    )


def tool_call_row(record: asyncpg.Record) -> dict[str, Any]:
    return {
        "tool_call_id": str(record["id"]),
        "run_id": record["run_id"],
        "session_id": record["session_id"],
        "agent_id": record["agent_id"],
        "agent_version": record["agent_version"],
        "environment_id": record["environment_id"],
        "principal": record["principal"],
        "approved_by": record["approved_by"],
        "ts": record["decided_at"].isoformat(),
        "tool_use_id": record["tool_use_id"],
        "tool_name": record["tool_name"],
        "call_hash": record["call_hash"],
        "args_json": record["args_json"],
        "args_truncated": record["args_truncated"],
        "decision": record["decision"],
        "result_status": record["result_status"],
        "latency_ms": record["latency_ms"],
        "error": record["error"],
    }


async def export_tool_calls(session_id: str) -> int:
    """Stream this session's completed tool-call records to BigQuery.

    Postgres is the system of record; BigQuery is an append-only export written
    once per row, at the burst boundary, with results already attached — a
    streamed row cannot be updated afterwards, so it must not be written before
    the call has finished.
    """
    async with db.transaction() as conn:
        records = await store.unexported_tool_calls(conn, session_id)
    if not records:
        return 0
    ok = await _insert(
        "tool_calls",
        [tool_call_row(r) for r in records],
        row_ids=[str(r["id"]) for r in records],
    )
    if not ok:
        return 0
    async with db.transaction() as conn:
        await store.mark_tool_calls_exported(conn, [r["id"] for r in records])
    return len(records)
