import asyncio
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from naxos_shared.events import (
    EventType,
    PermissionMode,
    PermissionPolicy,
    SessionConfig,
    SessionStatus,
    StopReason,
)
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import audit, config, db, deployments, store, wake
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
        "SELECT instructions, model, tools, permission_policy, mcp_servers, max_turns "
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
        await conn.execute("UPDATE sessions SET retry_count = 0 WHERE id = $1", session_id)
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
            server["url"] = f"{config.EGRESS_URL}/r/{token}"
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
        skill_names = await _mountable_skills(conn, list(row["skill_ids"] or []))
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
        skill_names=skill_names,
        session_bucket=row["session_bucket"],
        sdk_session_id=row["sdk_session_id"],
        budget_usd=float(row["budget_usd"]) if row["budget_usd"] is not None else None,
        cost_usd=float(row["cost_usd"]),
        max_turns=version["max_turns"],
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


@router.post("/sessions/{session_id}/events")
async def sandbox_events(
    session_id: str, body: dict[str, Any], caller: str = Depends(caller_service_account)
) -> dict:
    events = [SandboxEvent.model_validate(e) for e in body.get("events", [])]
    audit_rows = []
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        for event in events:
            await store.append_event(conn, session_id, event.type, event.payload, processed=True)
            if event.type is EventType.AGENT_TOOL_USE:
                audit_rows.append(
                    audit.tool_call_row(
                        run_id=body.get("run_id", session_id),
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
    return {"ok": True, "count": len(events)}


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
        await store.release_lease(conn, session_id, body.lease_id)
        pending = await conn.fetchval(
            "SELECT count(*) FROM session_events WHERE session_id = $1 AND processed_at IS NULL",
            session_id,
        )
        if pending and not body.terminated:
            await wake.wake(conn, session_id)

    created_by = row["created_by"] or ""
    await audit.log_run(
        run_id=body.run_id or f"{session_id}-{int(datetime.now(UTC).timestamp())}",
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


async def _mountable_skills(conn, skill_ids: list[str]) -> list[str]:
    """Names of the session's skills that are unarchived and have a SKILL.md."""
    if not skill_ids:
        return []
    rows = await conn.fetch(
        "SELECT s.name FROM skills s WHERE s.id = ANY($1) AND s.archived_at IS NULL "
        "  AND EXISTS (SELECT 1 FROM skill_files f "
        "    WHERE f.skill_id = s.id AND f.path = 'SKILL.md') "
        "ORDER BY s.name",
        skill_ids,
    )
    return [r["name"] for r in rows]


@router.get("/sessions/{session_id}/skills")
async def session_skills(session_id: str, caller: str = Depends(caller_service_account)) -> dict:
    """Skill files to materialise under the workspace, read-only for the agent:
    edits are discarded at the next wake, writeback exists only for memory."""
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        skills: dict[str, dict[str, Any]] = {}
        for skill_id in row["skill_ids"] or []:
            skill = await conn.fetchrow(
                "SELECT name FROM skills WHERE id = $1 AND archived_at IS NULL "
                "  AND EXISTS (SELECT 1 FROM skill_files f "
                "    WHERE f.skill_id = skills.id AND f.path = 'SKILL.md')",
                skill_id,
            )
            if skill is None:
                continue
            files = await conn.fetch(
                "SELECT path, content FROM skill_files WHERE skill_id = $1", skill_id
            )
            skills[skill_id] = {
                "name": skill["name"],
                "files": {f["path"]: f["content"] for f in files},
            }
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


@router.post("/sessions/{session_id}/memory")
async def session_memory_writeback(
    session_id: str, body: dict[str, Any], caller: str = Depends(caller_service_account)
) -> dict:
    """Persist memory files the agent changed during the burst. Last write wins."""
    written = 0
    async with db.transaction() as conn:
        row = await _authorize(conn, session_id, caller)
        allowed = set(row["memory_store_ids"] or [])
        for store_id, files in (body.get("stores") or {}).items():
            if store_id not in allowed:
                continue
            for path, content in files.items():
                if content is None:
                    await conn.execute(
                        "DELETE FROM memories WHERE store_id = $1 AND path = $2",
                        store_id,
                        path,
                    )
                    continue
                if len(str(content).encode()) > config.MAX_MEMORY_BYTES:
                    log.warning(
                        "memory writeback skipped, %s/%s exceeds size limit", store_id, path
                    )
                    continue
                await conn.execute(
                    "INSERT INTO memories (id, store_id, path, content, updated_by) "
                    "VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (store_id, path) DO UPDATE SET content = EXCLUDED.content, "
                    "  updated_by = EXCLUDED.updated_by, updated_at = now()",
                    new_id("memory"),
                    store_id,
                    path,
                    content,
                    f"agent:{session_id}",
                )
                written += 1
    return {"written": written}


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
