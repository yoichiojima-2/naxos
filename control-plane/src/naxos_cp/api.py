import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from naxos_shared.events import (
    Decision,
    EventIn,
    EventType,
    PermissionPolicy,
    SessionStatus,
)
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db, gcs, notify, sessions, store, wake
from .auth import principal_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


def create_app(manage_pool: bool = True) -> FastAPI:
    from . import artifacts, deployments, memory, skills, vaults, workspace

    app = FastAPI(title="naxos", lifespan=db.lifespan if manage_pool else None)
    app.include_router(router)
    app.include_router(deployments.router)
    app.include_router(vaults.router)
    app.include_router(memory.router)
    app.include_router(skills.router)
    app.include_router(workspace.router)
    app.include_router(artifacts.router)
    ui_dir = Path(os.environ.get("UI_DIR", "/app/ui"))
    if ui_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dir, html=True))
    return app


# --- agents ----------------------------------------------------------------


class AgentIn(BaseModel):
    name: str
    environment_id: str
    model: str
    instructions: str | None = None
    tools: list[str] = Field(default_factory=list)
    permission_policy: PermissionPolicy = Field(default_factory=PermissionPolicy)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    vault_ids: list[str] = Field(default_factory=list)
    memory_store_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    default_budget_usd: float | None = None
    max_turns: int | None = None


@router.post("/agents", status_code=201)
async def create_agent(body: AgentIn, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        env = await conn.fetchrow(
            "SELECT id FROM environments WHERE id = $1 AND archived_at IS NULL",
            body.environment_id,
        )
        if env is None:
            raise HTTPException(409, "environment is not registered or is archived")
        agent_id = new_id("agent")
        await conn.execute(
            "INSERT INTO agents (id, name, environment_id, created_by) VALUES ($1, $2, $3, $4)",
            agent_id,
            body.name,
            body.environment_id,
            principal,
        )
        await _insert_version(conn, agent_id, 1, body, principal)
        return await _agent_with_version(conn, agent_id, 1)


@router.post("/agents/{agent_id}/versions", status_code=201)
async def create_agent_version(
    agent_id: str, body: AgentIn, principal: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        version = await conn.fetchval(
            "UPDATE agents SET latest_version = latest_version + 1, updated_at = now() "
            "WHERE id = $1 AND archived_at IS NULL RETURNING latest_version",
            agent_id,
        )
        if version is None:
            raise HTTPException(404, "agent not found or archived")
        await _insert_version(conn, agent_id, version, body, principal)
        return await _agent_with_version(conn, agent_id, version)


async def _insert_version(conn, agent_id: str, version: int, body: AgentIn, principal: str):
    await conn.execute(
        "INSERT INTO agent_versions (agent_id, version, instructions, model, tools, "
        "  permission_policy, mcp_servers, vault_ids, memory_store_ids, skill_ids, "
        "  default_budget_usd, max_turns, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)",
        agent_id,
        version,
        body.instructions,
        body.model,
        list(body.tools),
        body.permission_policy.model_dump(mode="json"),
        body.mcp_servers,
        body.vault_ids,
        body.memory_store_ids,
        body.skill_ids,
        body.default_budget_usd,
        body.max_turns,
        principal,
    )


async def _agent_with_version(conn, agent_id: str, version: int) -> dict:
    row = await conn.fetchrow(
        "SELECT a.*, v.instructions, v.model, v.tools, v.permission_policy, v.mcp_servers, "
        "  v.vault_ids, v.memory_store_ids, v.skill_ids, v.default_budget_usd, v.max_turns "
        "FROM agents a JOIN agent_versions v ON v.agent_id = a.id AND v.version = $2 "
        "WHERE a.id = $1",
        agent_id,
        version,
    )
    if row is None:
        raise HTTPException(404, "agent version not found")
    out = dict(row)
    out["version"] = version
    return out


@router.get("/agents")
async def list_agents(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM agents WHERE archived_at IS NULL ORDER BY created_at DESC"
        )
    return {"data": [dict(r) for r in rows]}


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str, version: int | None = None, _: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        latest = await conn.fetchval("SELECT latest_version FROM agents WHERE id = $1", agent_id)
        if latest is None:
            raise HTTPException(404, "agent not found")
        return await _agent_with_version(conn, agent_id, version or latest)


class AgentPatch(BaseModel):
    disabled: bool


@router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, body: AgentPatch, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        result = await conn.execute(
            "UPDATE agents SET disabled = $2, updated_at = now() WHERE id = $1",
            agent_id,
            body.disabled,
        )
        if db.rowcount(result) != 1:
            raise HTTPException(404, "agent not found")
    return {"id": agent_id, "disabled": body.disabled}


@router.post("/agents/{agent_id}/archive")
async def archive_agent(agent_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE agents SET archived_at = now() WHERE id = $1 AND archived_at IS NULL",
            agent_id,
        )
    return {"id": agent_id, "archived": True}


# --- environments ----------------------------------------------------------


class EnvironmentIn(BaseModel):
    name: str
    service_account_email: str
    sandbox_job_name: str
    session_bucket: str
    cpu: str = "1"
    memory: str = "2Gi"


@router.post("/environments", status_code=201)
async def create_environment(body: EnvironmentIn, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        existing = await conn.fetchrow("SELECT id FROM environments WHERE name = $1", body.name)
        if existing:
            raise HTTPException(409, "environment already registered")
        env_id = new_id("environment")
        row = await conn.fetchrow(
            "INSERT INTO environments (id, name, service_account_email, sandbox_job_name, "
            "  session_bucket, cpu, memory) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
            env_id,
            body.name,
            body.service_account_email,
            body.sandbox_job_name,
            body.session_bucket,
            body.cpu,
            body.memory,
        )
    return dict(row)


@router.get("/environments")
async def list_environments(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch("SELECT * FROM environments ORDER BY created_at")
    return {"data": [dict(r) for r in rows]}


# --- sessions --------------------------------------------------------------


class AgentRef(BaseModel):
    id: str
    version: int | None = None


class SessionIn(BaseModel):
    agent: AgentRef
    title: str | None = None
    budget_usd: float | None = None
    initial_events: list[EventIn] = Field(default_factory=list)
    vault_ids: list[str] = Field(default_factory=list)
    memory_store_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        agent = await sessions.resolve_agent(conn, body.agent.id, body.agent.version)
        if agent is None:
            raise HTTPException(404, "agent or version not found")
        if agent["disabled"]:
            raise HTTPException(409, "agent is disabled")
        row = await sessions.create(
            conn,
            agent,
            initial_events=body.initial_events,
            principal=principal,
            title=body.title,
            budget_usd=body.budget_usd,
            vault_ids=body.vault_ids or None,
            memory_store_ids=body.memory_store_ids or None,
            skill_ids=body.skill_ids or None,
            resources=body.resources or None,
        )
    return dict(row)


@router.get("/sessions")
async def list_sessions(
    agent_id: str | None = None,
    status: SessionStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: str = Depends(principal_of),
) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sessions WHERE ($1::text IS NULL OR agent_id = $1) "
            "  AND ($2::text IS NULL OR status = $2) ORDER BY created_at DESC LIMIT $3",
            agent_id,
            str(status) if status else None,
            limit,
        )
    return {"data": [dict(r) for r in rows]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return dict(row)


class SessionPatch(BaseModel):
    budget_usd: float | None = None
    title: str | None = None


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str, body: SessionPatch, _: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "UPDATE sessions SET budget_usd = COALESCE($2, budget_usd), "
            "  title = COALESCE($3, title), updated_at = now() WHERE id = $1 RETURNING *",
            session_id,
            body.budget_usd,
            body.title,
        )
        if row is None:
            raise HTTPException(404, "session not found")
        if body.budget_usd is not None and row["stop_reason"] == "budget_reached":
            await wake.wake(conn, session_id)
    return dict(row)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT e.session_bucket FROM sessions s "
            "JOIN environments e ON e.id = s.environment_id WHERE s.id = $1",
            session_id,
        )
        if row is None:
            raise HTTPException(404, "session not found")
        # Conditional DELETE so the liveness check and the delete are one atomic
        # statement — a claim landing in between makes the condition fail, not race.
        result = await conn.execute(
            "DELETE FROM sessions WHERE id = $1 AND status <> 'rescheduling' "
            "  AND (lease_expires_at IS NULL OR lease_expires_at <= now())",
            session_id,
        )
        if db.rowcount(result) != 1:
            raise HTTPException(409, "session has a live sandbox — terminate it and retry")
    log.info("session %s deleted by %s", session_id, principal)
    try:
        await gcs.delete_prefix(row["session_bucket"], f"sessions/{session_id}/")
    except Exception:
        log.exception("blob wipe failed for deleted session %s", session_id)
    return {"id": session_id, "deleted": True}


@router.post("/sessions/{session_id}/terminate")
async def terminate_session(session_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        exists = await conn.fetchval("SELECT 1 FROM sessions WHERE id = $1", session_id)
        if not exists:
            raise HTTPException(404, "session not found")
        await store.set_status(conn, session_id, SessionStatus.TERMINATED)
    return {"id": session_id, "status": "terminated"}


# --- events ----------------------------------------------------------------


class EventsIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list)


@router.post("/sessions/{session_id}/events", status_code=202)
async def send_events(
    session_id: str, body: EventsIn, principal: str = Depends(principal_of)
) -> dict:
    if not body.events:
        raise HTTPException(400, "events must not be empty")
    async with db.transaction() as conn:
        session = await conn.fetchrow(
            "SELECT s.status, a.disabled FROM sessions s JOIN agents a ON a.id = s.agent_id "
            "WHERE s.id = $1",
            session_id,
        )
        if session is None:
            raise HTTPException(404, "session not found")
        if session["status"] == str(SessionStatus.TERMINATED):
            raise HTTPException(409, "session is terminated")
        if session["disabled"]:
            raise HTTPException(409, "agent is disabled")

        created = []
        for event in body.events:
            event.validate_for_send()
            if event.type is EventType.USER_TOOL_CONFIRMATION:
                decided = await store.decide_confirmation(
                    conn,
                    session_id,
                    event.call_hash or "",
                    "allowed" if event.result is Decision.ALLOW else "denied",
                    principal,
                    event.deny_message,
                )
                if not decided:
                    raise HTTPException(409, "no pending confirmation for that call")
            record = await store.append_event(
                conn, session_id, event.type, event.model_dump(mode="json"), principal
            )
            created.append({"id": record["id"], "seq": record["seq"], "type": record["type"]})
        await wake.wake(conn, session_id)
    return {"data": created}


@router.get("/sessions/{session_id}/events")
async def get_events(
    request: Request,
    session_id: str,
    after: int = 0,
    limit: int = Query(200, ge=1, le=1000),
    stream: str | None = None,
    _: str = Depends(principal_of),
):
    if stream == "sse":
        last_event_id = request.headers.get("last-event-id")
        start = int(last_event_id) if last_event_id else after
        return StreamingResponse(
            _sse(session_id, start),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )
    async with db.transaction() as conn:
        rows = await store.list_events(conn, session_id, after, limit)
    return {"data": [dict(r) for r in rows]}


async def _sse(session_id: str, after: int):
    """Replay from `after`, then tail on LISTEN/NOTIFY. Frame ids are the seq."""
    cursor = after
    while True:
        async with db.pool().acquire() as conn:
            rows = await store.list_events(conn, session_id, cursor, 200)
        for row in rows:
            cursor = row["seq"]
            payload = json.dumps(
                {
                    "type": row["type"],
                    "seq": row["seq"],
                    "payload": row["payload"],
                    "principal": row["principal"],
                    "created_at": row["created_at"].isoformat(),
                },
                default=str,
            )
            yield f"id: {row['seq']}\nevent: {row['type']}\ndata: {payload}\n\n"
            if row["type"] == str(EventType.SESSION_STATUS_TERMINATED):
                return
        if rows:
            continue
        async with db.pool().acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM sessions WHERE id = $1", session_id)
        if not exists:
            return
        if not await notify.wait(session_id, config.SSE_PING_SECONDS):
            yield ": ping\n\n"


app = create_app()
