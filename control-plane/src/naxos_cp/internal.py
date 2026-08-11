import asyncio
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from naxos_shared.events import (
    EventIn,
    EventType,
    PermissionMode,
    PermissionPolicy,
    SessionConfig,
    SessionStatus,
    StopReason,
)
from naxos_shared.ids import new_id
from naxos_shared.paths import unsafe_relpath
from pydantic import BaseModel, Field

from . import artifacts, audit, config, db, deployments, favorites, store, wake
from .auth import caller_service_account

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal")

QUEUE_POLL_SECONDS = 0.5


def create_app(manage_pool: bool = True) -> FastAPI:
    app = FastAPI(title="naxos-internal", lifespan=db.lifespan if manage_pool else None)
    app.include_router(router)
    return app


async def _authorize(conn, session_id: str, caller: str) -> Any:
    row = await conn.fetchrow(
        "SELECT s.*, e.service_account_email, e.session_bucket, a.disabled "
        "FROM sessions s JOIN environments e ON e.id = s.environment_id "
        "JOIN agents a ON a.id = s.agent_id "
        "WHERE s.id = $1",
        session_id,
    )
    if row is None:
        raise HTTPException(404, "session not found")
    if config.ENFORCE_CALLER_AUTH and row["service_account_email"] != caller:
        raise HTTPException(403, "caller is not the session's environment service account")
    return row


async def _agent_version(conn, row: Any) -> Any:
    return await conn.fetchrow(
        "SELECT instructions, model, tools, permission_policy, mcp_servers, max_turns, effort "
        "FROM agent_versions WHERE agent_id = $1 AND version = $2",
        row["agent_id"],
        row["agent_version"],
    )


@router.post("/sessions/{session_id}/claim")
async def claim(
    session_id: str, request: Request, caller: str = Depends(caller_service_account)
) -> dict:
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        if row["status"] == str(SessionStatus.TERMINATED):
            raise HTTPException(409, "session is terminated")
        lease_id = await store.acquire_lease(conn, session_id, row["service_account_email"])
        if lease_id is None:
            raise HTTPException(409, "session is already leased")
        await store.set_status(conn, session_id, SessionStatus.RUNNING)
    return {"lease_id": lease_id}


@router.post("/sessions/{session_id}/heartbeat")
async def heartbeat(
    session_id: str, body: dict[str, str], caller: str = Depends(caller_service_account)
) -> dict:
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        ok = await store.heartbeat(conn, session_id, body["lease_id"])
        if not ok:
            raise HTTPException(409, "lease lost")
    return {"ok": True, "disabled": bool(row["disabled"])}


async def _resolve_egress(conn, session_id: str, vault_ids: list[str], mcp_servers: dict) -> dict:
    """Rewrite MCP server URLs through the egress proxy so credentials never
    reach the sandbox. Servers without a matching credential pass through."""
    if not config.EGRESS_URL or not vault_ids:
        return mcp_servers
    credentials = await conn.fetch(
        "SELECT * FROM vault_credentials WHERE vault_id = ANY($1) AND type = 'header'",
        vault_ids,
    )
    rewritten: dict = {}
    for name, server in (mcp_servers or {}).items():
        server = dict(server)
        url = server.get("url", "")
        match = next(
            (c for c in credentials if (c["target"] or {}).get("mcp_server") == name), None
        )
        if match and url:
            token = secrets.token_urlsafe(24)
            await conn.execute(
                "INSERT INTO egress_routes (token, session_id, credential_id, target_url, "
                "  header, value_prefix) VALUES ($1, $2, $3, $4, $5, $6)",
                token,
                session_id,
                match["id"],
                url,
                (match["target"] or {}).get("header", "authorization"),
                (match["target"] or {}).get("prefix", "Bearer "),
            )
            server["url"] = f"{config.EGRESS_URL}/r/{token}/"
        rewritten[name] = server
    return rewritten


@router.get("/sessions/{session_id}/config")
async def session_config(session_id: str, caller: str = Depends(caller_service_account)) -> dict:
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        version = await _agent_version(conn, row)
        await conn.execute("DELETE FROM egress_routes WHERE session_id = $1", session_id)
        mcp_servers = await _resolve_egress(
            conn, session_id, list(row["vault_ids"] or []), version["mcp_servers"] or {}
        )
        mountable = await _mountable_skills(conn, list(row["skill_ids"] or []))
    return SessionConfig(
        session_id=session_id,
        agent_id=row["agent_id"],
        agent_version=row["agent_version"],
        environment_id=row["environment_id"],
        instructions=version["instructions"],
        model=version["model"],
        tools=list(version["tools"] or []),
        permission_policy=PermissionPolicy.model_validate(version["permission_policy"] or {}),
        mcp_servers=mcp_servers,
        skill_names=[s["name"] for s in mountable],
        session_bucket=row["session_bucket"],
        sdk_session_id=row["sdk_session_id"],
        budget_usd=float(row["budget_usd"]) if row["budget_usd"] is not None else None,
        cost_usd=float(row["cost_usd"]),
        max_turns=version["max_turns"],
        effort=version["effort"],
        disabled=row["disabled"],
    ).model_dump(mode="json")


@router.get("/sessions/{session_id}/queue")
async def queue(
    session_id: str, wait: int = 25, caller: str = Depends(caller_service_account)
) -> dict:
    """Long-poll for queued client events and control signals."""
    deadline = asyncio.get_running_loop().time() + max(0, min(wait, 55))
    async with db.transaction() as conn:
        await _authorize(conn, session_id, caller)
    while True:
        async with db.transaction() as conn:
            state = await conn.fetchrow(
                "SELECT s.status, a.disabled FROM sessions s "
                "JOIN agents a ON a.id = s.agent_id WHERE s.id = $1",
                session_id,
            )
            if state is None:
                return {"control": "terminate", "events": []}
            if state["disabled"]:
                return {"control": "kill", "events": []}
            if state["status"] == str(SessionStatus.TERMINATED):
                return {"control": "terminate", "events": []}
            events = await store.claim_queued_events(conn, session_id)
        if events:
            return {
                "control": None,
                "events": [
                    {
                        "seq": e["seq"],
                        "type": e["type"],
                        "payload": e["payload"],
                        "principal": e["principal"],
                    }
                    for e in events
                ],
            }
        if asyncio.get_running_loop().time() >= deadline:
            return {"control": None, "events": []}
        await asyncio.sleep(QUEUE_POLL_SECONDS)


class SandboxEvent(BaseModel):
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


class SandboxEventsIn(BaseModel):
    events: list[SandboxEvent] = Field(default_factory=list)
    run_id: str | None = None


@router.post("/sessions/{session_id}/events")
async def sandbox_events(
    session_id: str, body: SandboxEventsIn, caller: str = Depends(caller_service_account)
) -> dict:
    audit_rows = []
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        for event in body.events:
            await store.append_event(conn, session_id, event.type, event.payload, processed=True)
            if event.type is EventType.AGENT_TOOL_USE:
                audit_rows.append(
                    audit.tool_call_row(
                        run_id=body.run_id or session_id,
                        session_id=session_id,
                        agent_id=row["agent_id"],
                        principal=row["created_by"],
                        tool_name=event.payload.get("tool_name", ""),
                        args_redacted=str(event.payload.get("input", ""))[:2000],
                        decision=event.payload.get("decision", "auto_allowed"),
                        tool_use_id=event.payload.get("tool_use_id"),
                    )
                )
    await audit.log_tool_calls(audit_rows)
    return {"ok": True, "count": len(body.events)}


class PermissionAsk(BaseModel):
    call_hash: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None


@router.post("/sessions/{session_id}/permission")
async def permission(
    session_id: str, body: PermissionAsk, caller: str = Depends(caller_service_account)
) -> dict:
    """Resolve one tool call against policy and any recorded human decision."""
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        if row["disabled"]:
            return {"decision": "deny", "reason": "agent disabled", "killed": True}
        version = await _agent_version(conn, row)
        policy = PermissionPolicy.model_validate(version["permission_policy"] or {})
        if policy.mode_for(body.tool_name) is PermissionMode.ALWAYS_ALLOW:
            return {"decision": "allow", "by": "policy"}
        existing = await store.get_confirmation(conn, session_id, body.call_hash)
        if existing and existing["status"] == "allowed":
            return {"decision": "allow", "by": "user"}
        if existing and existing["status"] == "denied":
            return {
                "decision": "deny",
                "by": "user",
                "reason": existing["deny_message"] or "denied by operator",
            }
        await store.upsert_confirmation(
            conn, session_id, body.call_hash, body.tool_name, body.input, body.tool_use_id
        )
    return {"decision": "pending"}


class Checkpoint(BaseModel):
    lease_id: str
    sdk_session_id: str | None = None
    cost_usd: float | None = None
    stop_reason: StopReason = StopReason.END_TURN
    terminated: bool = False
    run_id: str | None = None
    started_at: datetime | None = None
    num_turns: int = 0


@router.post("/sessions/{session_id}/checkpoint")
async def checkpoint(
    session_id: str, body: Checkpoint, caller: str = Depends(caller_service_account)
) -> dict:
    run_id = body.run_id or f"{session_id}-{int(datetime.now(UTC).timestamp())}"
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        version = await _agent_version(conn, row)
        previous_cost = float(row["cost_usd"])
        await conn.execute(
            "UPDATE sessions SET sdk_session_id = COALESCE($2, sdk_session_id), "
            "  cost_usd = COALESCE($3, cost_usd), updated_at = now() WHERE id = $1",
            session_id,
            body.sdk_session_id,
            body.cost_usd,
        )
        status = SessionStatus.TERMINATED if body.terminated else SessionStatus.IDLE
        await store.set_status(
            conn, session_id, status, None if body.terminated else body.stop_reason
        )
        if body.terminated:
            await conn.execute("DELETE FROM egress_routes WHERE session_id = $1", session_id)
        created_by = row["created_by"] or ""
        await conn.execute(
            "INSERT INTO session_runs (id, session_id, agent_id, environment_id, trigger_type, "
            "  principal, model, status, stop_reason, num_turns, cost_usd, started_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
            "ON CONFLICT (id) DO NOTHING",
            run_id,
            session_id,
            row["agent_id"],
            row["environment_id"],
            "deployment" if created_by.startswith("deployment:") else "interactive",
            created_by,
            version["model"],
            str(status),
            str(body.stop_reason),
            body.num_turns,
            (body.cost_usd or previous_cost) - previous_cost,
            body.started_at or row["updated_at"],
        )
        await store.release_lease(conn, session_id, body.lease_id)
        pending = await conn.fetchval(
            "SELECT count(*) FROM session_events WHERE session_id = $1 AND processed_at IS NULL",
            session_id,
        )
        if pending and not body.terminated:
            await wake.wake(conn, session_id)

    await audit.log_run(
        run_id=run_id,
        session_id=session_id,
        agent_id=row["agent_id"],
        environment_id=row["environment_id"],
        principal=created_by,
        trigger_type="deployment" if created_by.startswith("deployment:") else "interactive",
        started_at=body.started_at or row["updated_at"],
        status=str(status),
        stop_reason=str(body.stop_reason),
        num_turns=body.num_turns,
        cost_usd=(body.cost_usd or previous_cost) - previous_cost,
        model=version["model"],
    )
    return {"ok": True}


async def _mountable_skills(conn, skill_ids: list[str]) -> list[Any]:
    """(id, name) of the session's skills that are unarchived and have a SKILL.md."""
    if not skill_ids:
        return []
    return await conn.fetch(
        "SELECT s.id, s.name FROM skills s WHERE s.id = ANY($1) AND s.archived_at IS NULL "
        "  AND EXISTS (SELECT 1 FROM skill_files f "
        "    WHERE f.skill_id = s.id AND f.path = 'SKILL.md') "
        "ORDER BY s.name",
        skill_ids,
    )


@router.get("/sessions/{session_id}/skills")
async def session_skills(session_id: str, caller: str = Depends(caller_service_account)) -> dict:
    """Skill files to materialise in the sandbox, read-only for the agent:
    edits are discarded at the next wake, writeback exists only for memory."""
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        mountable = await _mountable_skills(conn, list(row["skill_ids"] or []))
        skills: dict[str, dict[str, Any]] = {
            s["id"]: {"name": s["name"], "files": {}} for s in mountable
        }
        files = await conn.fetch(
            "SELECT skill_id, path, content FROM skill_files WHERE skill_id = ANY($1)",
            list(skills),
        )
        for f in files:
            skills[f["skill_id"]]["files"][f["path"]] = f["content"]
    return {"skills": skills}


@router.get("/sessions/{session_id}/memory")
async def session_memory(session_id: str, caller: str = Depends(caller_service_account)) -> dict:
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        stores: dict[str, dict[str, str]] = {}
        for store_id in row["memory_store_ids"] or []:
            name = await conn.fetchval("SELECT name FROM memory_stores WHERE id = $1", store_id)
            if name is None:
                continue
            memories = await conn.fetch(
                "SELECT path, content FROM memories WHERE store_id = $1", store_id
            )
            stores[store_id] = {
                "name": name,
                "files": {m["path"]: m["content"] for m in memories},
            }
    return {"stores": stores}


class MemoryWriteback(BaseModel):
    stores: dict[str, dict[str, str | None]] = Field(default_factory=dict)


@router.post("/sessions/{session_id}/memory")
async def session_memory_writeback(
    session_id: str, body: MemoryWriteback, caller: str = Depends(caller_service_account)
) -> dict:
    """Persist memory files the agent changed during the burst. Last write wins."""
    written = 0
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        allowed = set(row["memory_store_ids"] or [])
        for store_id, files in body.stores.items():
            if store_id not in allowed:
                continue
            for path, content in files.items():
                if unsafe_relpath(path):
                    log.warning("memory writeback skipped, unsafe path %s/%s", store_id, path)
                    continue
                if content is None:
                    await conn.execute(
                        "DELETE FROM memories WHERE store_id = $1 AND path = $2",
                        store_id,
                        path,
                    )
                    continue
                if len(content.encode()) > config.MAX_MEMORY_BYTES:
                    log.warning(
                        "memory writeback skipped, %s/%s exceeds size limit", store_id, path
                    )
                    continue
                await store.upsert_memory(conn, store_id, path, content, f"agent:{session_id}")
                written += 1
    return {"written": written}


class ArtifactIn(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9._/ -]{1,200}$")
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(0, ge=0)
    description: str | None = None


async def _emit_artifact_event(conn, session_id: str, action: str, row: Any) -> None:
    payload = {
        "artifact_id": row["id"],
        "name": row["name"],
        "action": action,
        "version": row["version"],
        "size_bytes": row["size_bytes"],
        "content_type": row["content_type"],
    }
    if row["share_token"]:
        payload["share_url"] = artifacts.share_url(row["share_token"])
    await store.append_event(
        conn,
        session_id,
        EventType.AGENT_ARTIFACT,
        payload,
        principal=f"agent:{session_id}",
        processed=True,
    )


@router.get("/sessions/{session_id}/artifacts")
async def session_artifacts(session_id: str, caller: str = Depends(caller_service_account)) -> dict:
    async with db.transaction() as conn:
        await _authorize(conn, session_id, caller)
        rows = await conn.fetch(
            "SELECT * FROM artifacts WHERE session_id = $1 ORDER BY name", session_id
        )
    return {"data": [artifacts.serialize(r) for r in rows]}


@router.post("/sessions/{session_id}/artifacts", status_code=201)
async def register_artifact(
    session_id: str, body: ArtifactIn, caller: str = Depends(caller_service_account)
) -> dict:
    """Record an artifact the sandbox uploaded to the session bucket.

    Upsert by (session_id, name): publishing an existing name bumps its version.
    """
    artifacts.validate_name(body.name)
    if body.size_bytes > config.MAX_ARTIFACT_BYTES:
        raise HTTPException(413, "artifact exceeds size limit")
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        record = await conn.fetchrow(
            "INSERT INTO artifacts (id, session_id, agent_id, environment_id, name, "
            "  description, content_type, size_bytes, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            "ON CONFLICT (session_id, name) DO UPDATE SET "
            "  content_type = EXCLUDED.content_type, size_bytes = EXCLUDED.size_bytes, "
            "  description = COALESCE(EXCLUDED.description, artifacts.description), "
            "  version = artifacts.version + 1, updated_at = now() "
            "RETURNING *, (xmax = 0) AS inserted",
            new_id("artifact"),
            session_id,
            row["agent_id"],
            row["environment_id"],
            body.name,
            body.description,
            body.content_type,
            body.size_bytes,
            f"agent:{session_id}",
        )
        action = "created" if record["inserted"] else "updated"
        await _emit_artifact_event(conn, session_id, action, record)
    out = artifacts.serialize(record)
    out.pop("inserted", None)
    return out


@router.delete("/sessions/{session_id}/artifacts/{name:path}")
async def delete_session_artifact(
    session_id: str, name: str, caller: str = Depends(caller_service_account)
) -> dict:
    async with db.transaction() as conn:
        await _authorize(conn, session_id, caller)
        record = await conn.fetchrow(
            "DELETE FROM artifacts WHERE session_id = $1 AND name = $2 RETURNING *",
            session_id,
            name,
        )
        if record is None:
            raise HTTPException(404, "artifact not found")
        await favorites.clear_for_entities(conn, record["id"])
        await _emit_artifact_event(conn, session_id, "deleted", record)
    return {"id": record["id"], "deleted": True}


class ArtifactShare(BaseModel):
    name: str
    shared: bool


@router.post("/sessions/{session_id}/artifacts/share")
async def share_session_artifact(
    session_id: str, body: ArtifactShare, caller: str = Depends(caller_service_account)
) -> dict:
    async with db.transaction() as conn:
        await _authorize(conn, session_id, caller)
        artifact_id = await conn.fetchval(
            "SELECT id FROM artifacts WHERE session_id = $1 AND name = $2",
            session_id,
            body.name,
        )
        if artifact_id is None:
            raise HTTPException(404, "artifact not found")
        record = await artifacts.set_shared(conn, artifact_id, body.shared, f"agent:{session_id}")
        if record is None:
            raise HTTPException(404, "artifact not found")
        await _emit_artifact_event(
            conn, session_id, "shared" if body.shared else "unshared", record
        )
    return artifacts.serialize(record)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cron: str
    prompt: str = Field(min_length=1)
    timezone: str = "Asia/Tokyo"
    budget_usd: float | None = Field(None, ge=0)


def _validate_cron(cron: str) -> str:
    # Cloud Scheduler validates for real at job creation; this catches the
    # obvious mistakes in DEV_MODE, where no scheduler job is created.
    if len(cron.split()) != 5:
        raise HTTPException(422, "cron must have 5 fields (minute hour day month weekday)")
    return cron


def _serialize_deployment(row: Any) -> dict[str, Any]:
    prompt = "\n".join(
        block.get("text", "")
        for event in row["initial_events"]
        for block in event.get("content", [])
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "cron": row["cron"],
        "timezone": row["timezone"],
        "prompt": prompt,
        "paused": row["paused"],
        "budget_usd": float(row["budget_usd"]) if row["budget_usd"] is not None else None,
        "created_by": row["created_by"],
    }


@router.get("/sessions/{session_id}/deployments")
async def session_deployments(
    session_id: str, caller: str = Depends(caller_service_account)
) -> dict:
    """Every unarchived deployment of the session's agent, operator-created included,
    so the agent can answer "what is scheduled for you" truthfully."""
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        rows = await conn.fetch(
            "SELECT * FROM deployments WHERE agent_id = $1 AND archived_at IS NULL "
            "ORDER BY created_at",
            row["agent_id"],
        )
    return {"data": [_serialize_deployment(r) for r in rows]}


@router.post("/sessions/{session_id}/deployments", status_code=201)
async def create_session_deployment(
    session_id: str, body: ScheduleIn, caller: str = Depends(caller_service_account)
) -> dict:
    """Create a durable deployment for the session's agent, on the agent's behalf.

    Unpinned (agent_version NULL = latest at fire time) and attributed to
    agent:{session_id}, so operators can see and govern what agents scheduled.
    """
    _validate_cron(body.cron)
    if body.budget_usd is not None and body.budget_usd > config.MAX_AGENT_DEPLOYMENT_BUDGET_USD:
        # Fired sessions take the deployment's budget as their hard cap, so an
        # agent-chosen value must not be able to disable budget governance.
        raise HTTPException(
            422,
            f"budget_usd may not exceed {config.MAX_AGENT_DEPLOYMENT_BUDGET_USD} for "
            "agent-created deployments; leave it unset to use the agent's default",
        )
    initial_events = [
        EventIn(type=EventType.USER_MESSAGE, content=[{"type": "text", "text": body.prompt}])
    ]
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        if row["disabled"]:
            raise HTTPException(409, "agent is disabled (kill switch)")
        # Lock the agent row so concurrent creates serialize and the cap holds.
        await conn.execute("SELECT 1 FROM agents WHERE id = $1 FOR UPDATE", row["agent_id"])
        existing = await conn.fetchval(
            "SELECT count(*) FROM deployments WHERE agent_id = $1 AND archived_at IS NULL "
            "AND created_by LIKE 'agent:%'",
            row["agent_id"],
        )
        if existing >= config.MAX_AGENT_DEPLOYMENTS:
            raise HTTPException(
                409,
                f"this agent already has {existing} agent-created deployments "
                f"(limit {config.MAX_AGENT_DEPLOYMENTS}); archive one first",
            )
        record = await deployments.insert(
            conn,
            agent_id=row["agent_id"],
            agent_version=None,
            name=body.name,
            cron=body.cron,
            timezone=body.timezone,
            initial_events=initial_events,
            budget_usd=body.budget_usd,
            created_by=f"agent:{session_id}",
        )
    return _serialize_deployment(record)


@router.delete("/sessions/{session_id}/deployments/{deployment_id}")
async def archive_session_deployment(
    session_id: str, deployment_id: str, caller: str = Depends(caller_service_account)
) -> dict:
    """Archive an agent-created deployment. Operator-created deployments are
    read-only to agents: visible in the list, not archivable from the sandbox."""
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        if row["disabled"]:
            raise HTTPException(409, "agent is disabled (kill switch)")
        record = await conn.fetchrow(
            "SELECT * FROM deployments WHERE id = $1 AND agent_id = $2 AND archived_at IS NULL",
            deployment_id,
            row["agent_id"],
        )
        if record is None:
            raise HTTPException(404, "deployment not found for this agent")
        if not (record["created_by"] or "").startswith("agent:"):
            raise HTTPException(
                403, "this deployment was created by an operator; only operators can archive it"
            )
        await conn.execute(
            "UPDATE deployments SET archived_at = now() WHERE id = $1", deployment_id
        )
    await deployments._delete_scheduler_job(record["scheduler_job_name"])
    return {"id": deployment_id, "archived": True}


@router.get("/egress/routes/{token}")
async def egress_route(token: str, caller: str = Depends(caller_service_account)) -> dict:
    """Resolve a route token for the egress proxy. Only the proxy may call."""
    if config.ENFORCE_CALLER_AUTH and caller != config.EGRESS_SA:
        raise HTTPException(403, "caller is not the egress proxy")
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT r.*, c.secret_ref, s.status FROM egress_routes r "
            "JOIN vault_credentials c ON c.id = r.credential_id "
            "JOIN sessions s ON s.id = r.session_id WHERE r.token = $1",
            token,
        )
    if row is None:
        raise HTTPException(404, "unknown route")
    if row["status"] == str(SessionStatus.TERMINATED):
        raise HTTPException(409, "session is terminated")
    return {
        "target_url": row["target_url"],
        "header": row["header"],
        "value_prefix": row["value_prefix"],
        "secret_ref": row["secret_ref"],
    }


@router.post("/deployments/{deployment_id}/fire")
async def fire_deployment(deployment_id: str, _: str = Depends(caller_service_account)) -> dict:
    return await deployments.fire(deployment_id, trigger="schedule")


@router.post("/reconcile")
async def reconcile(_: str = Depends(caller_service_account)) -> dict:
    woken = []
    async with db.transaction() as conn:
        for row in await store.stale_wakeable_sessions(conn):
            try:
                if await wake.wake(conn, row["id"]):
                    woken.append(row["id"])
            except Exception:
                log.exception("reconcile failed for %s", row["id"])
    return {"woken": woken}


app = create_app()
