import logging
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from . import config

log = logging.getLogger(__name__)
_client: bigquery.Client | None = None

JPY_PER_USD = 155.0


def _bq() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=config.PROJECT_ID)
    return _client


def _insert(table: str, row: dict[str, Any]) -> None:
    if not config.PROJECT_ID:
        log.debug("audit skipped (no project): %s %s", table, row)
        return
    target = f"{config.PROJECT_ID}.{config.AUDIT_DATASET}.{table}"
    errors = _bq().insert_rows_json(target, [row])
    if errors:
        log.error("audit insert failed table=%s errors=%s", table, errors)


def log_run(
    run_id: str,
    session_id: str,
    agent_id: str,
    environment_id: str,
    principal: str | None,
    trigger_type: str,
    started_at: datetime,
    status: str,
    stop_reason: str | None = None,
    deployment_run_id: str | None = None,
    num_turns: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    model: str | None = None,
) -> None:
    _insert(
        "runs",
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
        },
    )


def log_tool_call(
    run_id: str,
    session_id: str,
    agent_id: str,
    principal: str | None,
    tool_name: str,
    args_redacted: str,
    decision: str,
    result_status: str | None = None,
    tool_use_id: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    _insert(
        "tool_calls",
        {
            "run_id": run_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "principal": principal,
            "ts": datetime.now(UTC).isoformat(),
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "args_redacted": args_redacted,
            "decision": decision,
            "result_status": result_status,
            "latency_ms": latency_ms,
            "error": error,
        },
    )
