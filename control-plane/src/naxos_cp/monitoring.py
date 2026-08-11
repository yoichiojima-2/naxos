from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from . import db
from .auth import principal_of

router = APIRouter(prefix="/v1")


@router.get("/monitoring/summary")
async def summary(days: int = Query(30, ge=1, le=365), _: str = Depends(principal_of)) -> dict:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=days - 1)
    async with db.transaction() as conn:
        totals = await conn.fetchrow(
            "SELECT COALESCE(sum(cost_usd), 0) AS cost_usd, count(*) AS runs, "
            "  COALESCE(sum(num_turns), 0) AS num_turns "
            "FROM session_runs WHERE ended_at >= $1",
            since,
        )
        tool_calls = await conn.fetchval(
            "SELECT count(*) FROM session_events "
            "WHERE type = 'agent.tool_use' AND created_at >= $1 "
            "  AND COALESCE(payload->>'decision', '') != 'awaiting_confirmation'",
            since,
        )
        all_time = await conn.fetchrow(
            "SELECT COALESCE(sum(cost_usd), 0) AS cost_usd, count(*) AS sessions FROM sessions"
        )
        cost_by_day = await conn.fetch(
            "SELECT (ended_at AT TIME ZONE 'UTC')::date AS day, "
            "  sum(cost_usd) AS cost_usd, count(*) AS runs "
            "FROM session_runs WHERE ended_at >= $1 GROUP BY 1 ORDER BY 1",
            since,
        )
        cost_by_agent = await conn.fetch(
            "SELECT r.agent_id, a.name, sum(r.cost_usd) AS cost_usd, count(*) AS runs, "
            "  count(DISTINCT r.session_id) AS sessions "
            "FROM session_runs r JOIN agents a ON a.id = r.agent_id "
            "WHERE r.ended_at >= $1 GROUP BY 1, 2 ORDER BY 3 DESC, 2",
            since,
        )
        cost_by_model = await conn.fetch(
            "SELECT COALESCE(model, 'unknown') AS model, sum(cost_usd) AS cost_usd, "
            "  count(*) AS runs "
            "FROM session_runs WHERE ended_at >= $1 GROUP BY 1 ORDER BY 2 DESC, 1",
            since,
        )
        sessions_by_status = await conn.fetch(
            "SELECT status, count(*) AS count FROM sessions GROUP BY 1 ORDER BY 1"
        )
        tool_usage = await conn.fetch(
            "SELECT payload->>'tool_name' AS tool_name, count(*) AS calls, "
            "  count(*) FILTER (WHERE payload->>'decision' IN ('user_denied', 'not_allowed')) "
            "  AS denied "
            "FROM session_events "
            "WHERE type = 'agent.tool_use' AND created_at >= $1 "
            "  AND payload->>'tool_name' IS NOT NULL "
            "  AND COALESCE(payload->>'decision', '') != 'awaiting_confirmation' "
            "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12",
            since,
        )
        deployment_runs = await conn.fetch(
            "SELECT status, count(*) AS count FROM deployment_runs "
            "WHERE fired_at >= $1 GROUP BY 1 ORDER BY 1",
            since,
        )
    return {
        "window_days": days,
        "totals": {
            "cost_usd": float(totals["cost_usd"]),
            "runs": totals["runs"],
            "num_turns": totals["num_turns"],
            "tool_calls": tool_calls,
        },
        "all_time": {
            "cost_usd": float(all_time["cost_usd"]),
            "sessions": all_time["sessions"],
        },
        "cost_by_day": [
            {"day": r["day"].isoformat(), "cost_usd": float(r["cost_usd"]), "runs": r["runs"]}
            for r in cost_by_day
        ],
        "cost_by_agent": [dict(r) | {"cost_usd": float(r["cost_usd"])} for r in cost_by_agent],
        "cost_by_model": [dict(r) | {"cost_usd": float(r["cost_usd"])} for r in cost_by_model],
        "sessions_by_status": [dict(r) for r in sessions_by_status],
        "tool_usage": [dict(r) for r in tool_usage],
        "deployment_runs": [dict(r) for r in deployment_runs],
    }
