import functools
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.events import EventIn
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db, sessions
from .auth import principal_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")


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
        deployment_id = new_id("deployment")
        row = await conn.fetchrow(
            "INSERT INTO deployments (id, agent_id, agent_version, name, cron, timezone, "
            "  initial_events, budget_usd, scheduler_job_name, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *",
            deployment_id,
            body.agent_id,
            body.agent_version,
            body.name,
            body.cron,
            body.timezone,
            [e.model_dump(mode="json") for e in body.initial_events],
            body.budget_usd,
            "",
            principal,
        )
        job_name = await _create_scheduler_job(deployment_id, body.cron, body.timezone)
        if job_name:
            row = await conn.fetchrow(
                "UPDATE deployments SET scheduler_job_name = $2 WHERE id = $1 RETURNING *",
                deployment_id,
                job_name,
            )
    return dict(row)


@router.get("/deployments")
async def list_deployments(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM deployments WHERE archived_at IS NULL ORDER BY created_at DESC"
        )
    return {"data": [dict(r) for r in rows]}


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
            "SELECT * FROM deployment_runs WHERE deployment_id = $1 ORDER BY fired_at DESC "
            "LIMIT 100",
            deployment_id,
        )
    return {"data": [dict(r) for r in rows]}


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
