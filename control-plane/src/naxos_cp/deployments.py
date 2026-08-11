import functools
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from naxos_shared.events import EventIn, StopReason
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db, sessions
from .auth import principal_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")

# A blocked run is not a finished one. Both of these wait on an operator —
# answering the confirmation, or raising the budget, which is itself the resume
# signal — and the session then carries on where it stopped, so the run stays
# open and keeps accumulating cost instead of freezing at a wrong outcome.
BLOCKING_STOP_REASONS = {StopReason.REQUIRES_ACTION, StopReason.BUDGET_REACHED}


@functools.cache
def _scheduler_client():
    from google.cloud import scheduler_v1

    return scheduler_v1.CloudSchedulerAsyncClient()


def _job_name(deployment_id: str) -> str:
    suffix = deployment_id.split("_", 1)[1][:16]
    return f"projects/{config.PROJECT_ID}/locations/{config.REGION}/jobs/naxos-deploy-{suffix}"


async def _create_scheduler_job(deployment_id: str, cron: str, timezone: str) -> str:
    if not config.INTERNAL_URL or not config.PROJECT_ID:
        if config.DEV_MODE:
            log.warning(
                "scheduler not configured; deployment %s fires manually only", deployment_id
            )
            return ""
        raise HTTPException(503, "scheduler is not configured (INTERNAL_URL / project missing)")
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import scheduler_v1

    name = _job_name(deployment_id)
    job = scheduler_v1.Job(
        name=name,
        schedule=cron,
        time_zone=timezone,
        http_target=scheduler_v1.HttpTarget(
            uri=f"{config.INTERNAL_URL}/internal/deployments/{deployment_id}/fire",
            http_method=scheduler_v1.HttpMethod.POST,
            oidc_token=scheduler_v1.OidcToken(
                service_account_email=config.SCHEDULER_SA,
                audience=config.INTERNAL_URL,
            ),
        ),
    )
    parent = f"projects/{config.PROJECT_ID}/locations/{config.REGION}"
    try:
        await _scheduler_client().create_job(parent=parent, job=job)
    except AlreadyExists:
        log.warning("scheduler job %s already exists; reusing it", name)
    return name


async def _set_scheduler_paused(job_name: str, paused: bool) -> None:
    if not job_name:
        return
    client = _scheduler_client()
    if paused:
        await client.pause_job(name=job_name)
    else:
        await client.resume_job(name=job_name)


async def _delete_scheduler_job(job_name: str) -> None:
    if not job_name:
        return
    try:
        await _scheduler_client().delete_job(name=job_name)
    except Exception:
        log.exception("failed to delete scheduler job %s", job_name)


class DeploymentIn(BaseModel):
    name: str
    agent_id: str
    agent_version: int | None = None
    cron: str
    timezone: str = "Asia/Tokyo"
    initial_events: list[EventIn] = Field(min_length=1)
    budget_usd: float | None = None


async def insert(
    conn,
    *,
    agent_id: str,
    agent_version: int | None,
    name: str,
    cron: str,
    timezone: str,
    initial_events: list[EventIn],
    budget_usd: float | None,
    created_by: str,
) -> dict[str, Any]:
    deployment_id = new_id("deployment")
    row = await conn.fetchrow(
        "INSERT INTO deployments (id, agent_id, agent_version, name, cron, timezone, "
        "  initial_events, budget_usd, scheduler_job_name, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *",
        deployment_id,
        agent_id,
        agent_version,
        name,
        cron,
        timezone,
        [e.model_dump(mode="json") for e in initial_events],
        budget_usd,
        "",
        created_by,
    )
    job_name = await _create_scheduler_job(deployment_id, cron, timezone)
    if job_name:
        row = await conn.fetchrow(
            "UPDATE deployments SET scheduler_job_name = $2 WHERE id = $1 RETURNING *",
            deployment_id,
            job_name,
        )
    return dict(row)


@router.post("/deployments", status_code=201)
async def create_deployment(body: DeploymentIn, principal: str = Depends(principal_of)) -> dict:
    for event in body.initial_events:
        event.validate_for_send()
    async with db.transaction() as conn:
        agent = await conn.fetchrow(
            "SELECT id FROM agents WHERE id = $1 AND archived_at IS NULL", body.agent_id
        )
        if agent is None:
            raise HTTPException(404, "agent not found or archived")
        row = await insert(
            conn,
            agent_id=body.agent_id,
            agent_version=body.agent_version,
            name=body.name,
            cron=body.cron,
            timezone=body.timezone,
            initial_events=body.initial_events,
            budget_usd=body.budget_usd,
            created_by=principal,
        )
    return row


@router.get("/deployments")
async def list_deployments(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM deployments WHERE archived_at IS NULL ORDER BY created_at DESC"
        )
    return {"data": [dict(r) for r in rows]}


RUN_COLUMNS = (
    "r.id, r.deployment_id, r.session_id, r.status, r.error_type, r.error_message, "
    "r.stop_reason, r.cost_usd, r.num_turns, r.fired_at, r.started_at, r.finished_at, "
    "extract(epoch FROM (r.finished_at - r.fired_at)) AS duration_seconds, "
    "extract(epoch FROM (COALESCE(r.started_at, r.finished_at) - r.fired_at)) AS queued_seconds"
)


def _serialize_run(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["cost_usd"] = float(out["cost_usd"])
    for key in ("duration_seconds", "queued_seconds"):
        out[key] = float(out[key]) if out[key] is not None else None
    return out


@router.get("/deployments/runs")
async def runs_overview(
    days: int = Query(30, ge=1, le=365),
    deployment_id: str | None = None,
    status: str | None = None,
    limit: int = Query(400, ge=1, le=2000),
    _: str = Depends(principal_of),
) -> dict:
    """Run history across deployments: the rows the runs view charts and tables.

    Runs of archived deployments stay visible — the history is the point.
    """
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(RUN_STATUSES)}")
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    async with db.transaction() as conn:
        runs = await conn.fetch(
            f"SELECT {RUN_COLUMNS}, d.name AS deployment_name, d.agent_id, s.status "
            "  AS session_status FROM deployment_runs r "
            "JOIN deployments d ON d.id = r.deployment_id "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "WHERE r.fired_at >= $1 AND ($2::text IS NULL OR r.deployment_id = $2) "
            "  AND ($3::text IS NULL OR r.status = $3) "
            "ORDER BY r.fired_at DESC LIMIT $4",
            since,
            deployment_id,
            status,
            limit,
        )
        totals = await conn.fetch(
            "SELECT d.id, d.name, d.cron, d.timezone, d.agent_id, d.paused, "
            "  d.archived_at IS NOT NULL AS archived, "
            "  count(r.id) AS runs, "
            "  count(r.id) FILTER (WHERE r.status = 'succeeded') AS succeeded, "
            "  count(r.id) FILTER (WHERE r.status = 'failed') AS failed, "
            "  count(r.id) FILTER (WHERE r.status = 'cancelled') AS cancelled, "
            "  count(r.id) FILTER (WHERE r.status IN ('queued', 'running')) AS active, "
            "  count(r.id) FILTER (WHERE r.finished_at IS NOT NULL) AS finished, "
            "  COALESCE(sum(r.cost_usd), 0) AS cost_usd, "
            "  COALESCE(sum(extract(epoch FROM (r.finished_at - r.fired_at))), 0) "
            "    AS duration_seconds, "
            "  max(r.fired_at) AS last_fired_at, "
            # The health strip reads this, not the run list: that one is capped and
            # narrowed by the status filter, which would paint every strip one colour.
            "  COALESCE((SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.fired_at) "
            "    FROM (SELECT r2.id, r2.status, r2.fired_at FROM deployment_runs r2 "
            "      WHERE r2.deployment_id = d.id AND r2.fired_at >= $1 "
            "      ORDER BY r2.fired_at DESC LIMIT 20) recent), '[]'::jsonb) AS recent "
            "FROM deployments d "
            "LEFT JOIN deployment_runs r ON r.deployment_id = d.id AND r.fired_at >= $1 "
            "WHERE (d.archived_at IS NULL OR r.id IS NOT NULL) "
            "  AND ($2::text IS NULL OR d.id = $2) "
            "GROUP BY d.id ORDER BY count(r.id) DESC, d.name",
            since,
            deployment_id,
        )
    return {
        "window_days": days,
        "now": now.isoformat(),
        "runs": [_serialize_run(r) for r in runs],
        "deployments": [
            dict(r)
            | {"cost_usd": float(r["cost_usd"]), "duration_seconds": float(r["duration_seconds"])}
            for r in totals
        ],
    }


@router.get("/deployments/{deployment_id}")
async def get_deployment(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow("SELECT * FROM deployments WHERE id = $1", deployment_id)
    if row is None:
        raise HTTPException(404, "deployment not found")
    return dict(row)


@router.post("/deployments/{deployment_id}/pause")
async def pause_deployment(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    return await _set_paused(deployment_id, True)


@router.post("/deployments/{deployment_id}/unpause")
async def unpause_deployment(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    return await _set_paused(deployment_id, False)


async def _set_paused(deployment_id: str, paused: bool) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "UPDATE deployments SET paused = $2 WHERE id = $1 AND archived_at IS NULL RETURNING *",
            deployment_id,
            paused,
        )
        if row is None:
            raise HTTPException(404, "deployment not found or archived")
    await _set_scheduler_paused(row["scheduler_job_name"], paused)
    return dict(row)


@router.post("/deployments/{deployment_id}/archive")
async def archive_deployment(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "UPDATE deployments SET archived_at = now() WHERE id = $1 AND archived_at IS NULL "
            "RETURNING *",
            deployment_id,
        )
        if row is None:
            raise HTTPException(404, "deployment not found or archived")
    await _delete_scheduler_job(row["scheduler_job_name"])
    return {"id": deployment_id, "archived": True}


@router.post("/deployments/{deployment_id}/run", status_code=201)
async def run_deployment(deployment_id: str, principal: str = Depends(principal_of)) -> dict:
    return await fire(deployment_id, trigger=f"manual:{principal}")


@router.get("/deployments/{deployment_id}/runs")
async def list_runs(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            f"SELECT {RUN_COLUMNS} FROM deployment_runs r "
            "WHERE r.deployment_id = $1 ORDER BY r.fired_at DESC LIMIT 100",
            deployment_id,
        )
    return {"data": [_serialize_run(r) for r in rows]}


async def record_burst(
    conn,
    session_id: str,
    *,
    stop_reason: StopReason,
    terminated: bool,
    errored: bool,
    cost_delta: float,
    num_turns: int,
    started_at: datetime,
) -> None:
    """Fold one wake-to-idle burst into the open deployment run of this session.

    A burst that ends blocked leaves the run open — it is still going, waiting on
    an operator. Anything else is the run's end. `errored` is load-bearing: a
    burst that died on an exception still reports `end_turn`, so without it a
    crashed run would be recorded as a success.
    """
    blocked = not errored and not terminated and stop_reason in BLOCKING_STOP_REASONS
    await conn.execute(
        "UPDATE deployment_runs SET "
        "  started_at = COALESCE(started_at, $2), "
        "  cost_usd = cost_usd + $3, num_turns = num_turns + $4, stop_reason = $5, "
        "  status = $6, error_type = COALESCE(error_type, $7), "
        "  error_message = COALESCE(error_message, $8), "
        "  finished_at = CASE WHEN $9 THEN finished_at ELSE now() END "
        "WHERE session_id = $1 AND finished_at IS NULL",
        session_id,
        started_at,
        cost_delta,
        num_turns,
        str(stop_reason),
        "failed" if errored else "running" if blocked else "succeeded",
        "session_error" if errored else None,
        "the sandbox stopped on an error" if errored else None,
        blocked,
    )


async def _close_open_runs(
    conn, session_id: str, status: str, error_type: str, reason: str
) -> None:
    await conn.execute(
        "UPDATE deployment_runs SET status = $2, finished_at = now(), "
        "  error_type = COALESCE(error_type, $3), error_message = COALESCE(error_message, $4) "
        "WHERE session_id = $1 AND finished_at IS NULL",
        session_id,
        status,
        error_type,
        reason,
    )


async def cancel_open_runs(conn, session_id: str, reason: str) -> None:
    """Close the run of a session an operator terminated or deleted, so it does
    not read as still running forever."""
    await _close_open_runs(conn, session_id, "cancelled", "cancelled", reason)


async def fail_open_runs(conn, session_id: str, error_type: str, reason: str) -> None:
    """Close the run of a session that cannot proceed at all — the sandbox never
    reaches a checkpoint in that case, so nothing else would ever close it."""
    await _close_open_runs(conn, session_id, "failed", error_type, reason)


async def cancel_agent_runs(conn, agent_id: str, reason: str) -> None:
    """Kill switch: the sandbox stops on the control signal but reports an
    ordinary end_turn, so a killed run would otherwise be recorded as a success."""
    await conn.execute(
        "UPDATE deployment_runs SET status = 'cancelled', finished_at = now(), "
        "  error_type = COALESCE(error_type, 'agent_disabled'), "
        "  error_message = COALESCE(error_message, $2) "
        "WHERE finished_at IS NULL "
        "  AND session_id IN (SELECT id FROM sessions WHERE agent_id = $1)",
        agent_id,
        reason,
    )


async def _record_failure(
    run_id: str, deployment_id: str, error_type: str, message: str
) -> dict[str, Any]:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "INSERT INTO deployment_runs (id, deployment_id, status, error_type, error_message, "
            "  finished_at) VALUES ($1, $2, 'failed', $3, $4, now()) RETURNING *",
            run_id,
            deployment_id,
            error_type,
            message,
        )
    return dict(row)


async def fire(deployment_id: str, trigger: str) -> dict[str, Any]:
    """Create one session from a deployment and record the attempt."""
    run_id = new_id("deployment_run")
    async with db.transaction() as conn:
        deployment = await conn.fetchrow(
            "SELECT d.*, a.disabled, a.archived_at AS agent_archived "
            "FROM deployments d JOIN agents a ON a.id = d.agent_id WHERE d.id = $1",
            deployment_id,
        )
    if deployment is None:
        raise HTTPException(404, "deployment not found")
    if deployment["archived_at"] is not None:
        return await _record_failure(
            run_id, deployment_id, "deployment_archived", "deployment is archived"
        )
    if deployment["agent_archived"] is not None:
        return await _record_failure(run_id, deployment_id, "agent_archived", "agent is archived")
    if deployment["disabled"]:
        return await _record_failure(
            run_id, deployment_id, "agent_disabled", "agent is disabled (kill switch)"
        )

    try:
        async with db.transaction() as conn:
            agent = await sessions.resolve_agent(
                conn, deployment["agent_id"], deployment["agent_version"]
            )
            if agent is None:
                raise LookupError("agent version not found")
            session = await sessions.create(
                conn,
                agent,
                initial_events=[
                    EventIn.model_validate(raw) for raw in deployment["initial_events"]
                ],
                principal=f"deployment:{deployment_id}",
                title=f"{deployment['name']} ({trigger})",
                budget_usd=deployment["budget_usd"],
            )
            row = await conn.fetchrow(
                "INSERT INTO deployment_runs (id, deployment_id, session_id, status) "
                "VALUES ($1, $2, $3, 'running') RETURNING *",
                run_id,
                deployment_id,
                session["id"],
            )
        return dict(row)
    except Exception as exc:
        log.exception("deployment fire failed: %s", deployment_id)
        return await _record_failure(run_id, deployment_id, "infra_error", str(exc)[:500])
