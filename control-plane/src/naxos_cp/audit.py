import asyncio
import functools
import logging
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from . import config

log = logging.getLogger(__name__)

JPY_PER_USD = 155.0


@functools.cache
def _bq() -> bigquery.Client:
    return bigquery.Client(project=config.PROJECT_ID)


async def _insert(table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    if not config.PROJECT_ID:
        log.debug("audit skipped (no project): %s %s", table, rows)
        return
    target = f"{config.PROJECT_ID}.{config.AUDIT_DATASET}.{table}"
    errors = await asyncio.to_thread(_bq().insert_rows_json, target, rows)
    if errors:
        log.error("audit insert failed table=%s errors=%s", table, errors)


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
) -> None:
    await _insert(
        "runs",
        [
            {
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "environment_id": environment_id,
                "deployment_run_id": None,
                "trigger_type": trigger_type,
                "principal": principal,
                "started_at": started_at.isoformat(),
                "ended_at": datetime.now(UTC).isoformat(),
                "status": status,
                "stop_reason": stop_reason,
                "num_turns": num_turns,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": cost_usd,
                "approx_cost_jpy": round(cost_usd * JPY_PER_USD, 2),
                "model": model,
            }
        ],
    )


def tool_call_row(
    run_id: str,
    session_id: str,
    agent_id: str,
    principal: str | None,
    tool_name: str,
    args_redacted: str,
    decision: str,
    tool_use_id: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "principal": principal,
        "ts": datetime.now(UTC).isoformat(),
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "args_redacted": args_redacted,
        "decision": decision,
        "result_status": None,
        "latency_ms": None,
        "error": None,
    }


async def log_tool_calls(rows: list[dict[str, Any]]) -> None:
    await _insert("tool_calls", rows)
