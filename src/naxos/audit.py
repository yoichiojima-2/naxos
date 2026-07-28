import json
import logging
import os
import uuid
from datetime import UTC, datetime

from naxos.agent import AgentRun
from naxos.bq import BigQuery

logger = logging.getLogger(__name__)

TABLE = os.environ.get("AUDIT_TABLE", "audit.runs")


def log_run(
    prompt: str,
    run: AgentRun,
    started_at: datetime,
    role: str | None = None,
    principal: str | None = None,
    trigger: str | None = None,
) -> str:
    client = BigQuery().client
    run_id = str(uuid.uuid4())
    row = {
        "run_id": run_id,
        "session_id": run.session_id,
        "role": role,
        "principal": principal,
        "trigger_type": trigger,
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "prompt": prompt,
        "text": run.text,
        "tool_calls": json.dumps(run.tool_calls, default=str, ensure_ascii=False),
        "usage": json.dumps(run.usage, default=str),
        "num_turns": run.num_turns,
        "cost_usd": run.cost_usd,
        "is_error": run.is_error,
    }
    errors = client.insert_rows_json(TABLE, [row])
    if errors:
        logger.error(f"audit insert failed: {errors}")
    else:
        logger.info(f"audit: logged run {run_id}")
    return run_id


def recent_runs(limit: int) -> list[dict]:
    sql = (
        "SELECT run_id, session_id, role, principal, trigger_type, started_at, prompt, text, num_turns, cost_usd, is_error "
        f"FROM {TABLE} ORDER BY started_at DESC LIMIT {limit}"
    )
    return BigQuery().query(sql)
