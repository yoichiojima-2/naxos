import logging
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.events import EventIn
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db, store, wake
from .auth import principal_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

_scheduler = None


def _scheduler_client():
    global _scheduler
    if _scheduler is None:
        from google.cloud import scheduler_v1

        _scheduler = scheduler_v1.CloudSchedulerAsyncClient()
    return _scheduler


def _job_name(deployment_id: str) -> str:
    suffix = deployment_id.split("_", 1)[1][:16]
    return f"projects/{config.PROJECT_ID}/locations/{config.REGION}/jobs/naxos-deploy-{suffix}"


async def _create_scheduler_job(deployment_id: str, cron: str, timezone: str) -> str:
    if not config.INTERNAL_URL or not config.PROJECT_ID:
        log.warning("scheduler not configured; deployment %s fires manually only", deployment_id)
        return ""
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
    await _scheduler_client().create_job(parent=parent, job=job)
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
        job_name = await _create_scheduler_job(deployment_id, body.cron, body.timezone)
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
            job_name,
            principal,
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
    async with db.transaction() as conn:
        run = await fire(conn, deployment_id, trigger=f"manual:{principal}")
    return run


@router.get("/deployments/{deployment_id}/runs")
async def list_runs(deployment_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM deployment_runs WHERE deployment_id = $1 ORDER BY fired_at DESC "
            "LIMIT 100",
            deployment_id,
        )
    return {"data": [dict(r) for r in rows]}


async def fire(conn: asyncpg.Connection, deployment_id: str, trigger: str) -> dict[str, Any]:
    """Create one session from a deployment and record the attempt."""
    run_id = new_id("deployment_run")
    deployment = await conn.fetchrow(
        "SELECT d.*, a.disabled, a.archived_at AS agent_archived, a.environment_id, "
        "  a.latest_version "
        "FROM deployments d JOIN agents a ON a.id = d.agent_id WHERE d.id = $1",
        deployment_id,
    )
    if deployment is None:
        raise HTTPException(404, "deployment not found")

    async def failed(error_type: str, message: str) -> dict[str, Any]:
        row = await conn.fetchrow(
            "INSERT INTO deployment_runs (id, deployment_id, status, error_type, error_message, "
            "  finished_at) VALUES ($1, $2, 'failed', $3, $4, now()) RETURNING *",
            run_id,
            deployment_id,
            error_type,
            message,
        )
        return dict(row)

    if deployment["archived_at"] is not None:
        return await failed("deployment_archived", "deployment is archived")
    if deployment["agent_archived"] is not None:
        return await failed("agent_archived", "agent is archived")
    if deployment["disabled"]:
        return await failed("agent_disabled", "agent is disabled (kill switch)")

    version = deployment["agent_version"] or deployment["latest_version"]
    session_id = new_id("session")
    try:
        await conn.execute(
            "INSERT INTO sessions (id, agent_id, agent_version, environment_id, title, "
            "  budget_usd, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            session_id,
            deployment["agent_id"],
            version,
            deployment["environment_id"],
            f"{deployment['name']} ({trigger})",
            deployment["budget_usd"],
            f"deployment:{deployment_id}",
        )
        for raw in deployment["initial_events"]:
            event = EventIn.model_validate(raw)
            await store.append_event(
                conn,
                session_id,
                event.type,
                event.model_dump(mode="json"),
                f"deployment:{deployment_id}",
            )
        await wake.wake(conn, session_id)
        row = await conn.fetchrow(
            "INSERT INTO deployment_runs (id, deployment_id, session_id, status) "
            "VALUES ($1, $2, $3, 'running') RETURNING *",
            run_id,
            deployment_id,
            session_id,
        )
        return dict(row)
    except Exception as exc:
        log.exception("deployment fire failed: %s", deployment_id)
        return await failed("infra_error", str(exc)[:500])
