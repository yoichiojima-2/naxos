import json
import logging
import uuid
from datetime import UTC, datetime

from src.agent import AgentRun
from src.bq import BigQuery

logger = logging.getLogger(__name__)

TABLE = "audit.runs"


def log_run(prompt: str, run: AgentRun, started_at: datetime) -> str:
    client = BigQuery().client
    run_id = str(uuid.uuid4())
    row = {
        "run_id": run_id,
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
