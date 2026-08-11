import json
import uuid
from typing import Any

import asyncpg
from naxos_shared.events import EventType, SessionStatus, StopReason
from naxos_shared.ids import new_id

from . import config, notify
from .db import rowcount


async def append_event(
    conn: asyncpg.Connection,
    session_id: str,
    type_: EventType,
    payload: dict[str, Any] | None = None,
    principal: str | None = None,
    processed: bool = False,
) -> asyncpg.Record:
    """Append one event. seq is allocated under a row lock on the session."""
    payload = payload or {}
    encoded = json.dumps(payload, default=str)
    if len(encoded) > config.MAX_EVENT_PAYLOAD_BYTES:
        payload = {
            "truncated": True,
            "type": payload.get("type"),
            "preview": encoded[: config.MAX_EVENT_PAYLOAD_BYTES // 2],
        }
    seq = await conn.fetchval(
        "UPDATE sessions SET last_event_seq = last_event_seq + 1, updated_at = now() "
        "WHERE id = $1 RETURNING last_event_seq",
        session_id,
    )
    if seq is None:
        raise LookupError(session_id)
    record = await conn.fetchrow(
        "INSERT INTO session_events (session_id, seq, type, payload, principal, processed_at) "
        "VALUES ($1, $2, $3, $4, $5, CASE WHEN $6 THEN now() ELSE NULL END) RETURNING *",
        session_id,
        seq,
        str(type_),
        payload,
        principal,
        processed,
    )
    await conn.execute("SELECT pg_notify($1, $2)", notify.CHANNEL, session_id)
    return record


async def list_events(
    conn: asyncpg.Connection, session_id: str, after: int = 0, limit: int = 200
) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM session_events WHERE session_id = $1 AND seq > $2 ORDER BY seq LIMIT $3",
        session_id,
        after,
        limit,
    )


async def claim_queued_events(
    conn: asyncpg.Connection, session_id: str, limit: int = 50
) -> list[asyncpg.Record]:
    return await conn.fetch(
        "UPDATE session_events SET processed_at = now() WHERE id IN ("
        "  SELECT id FROM session_events WHERE session_id = $1 AND processed_at IS NULL"
        "  ORDER BY seq LIMIT $2 FOR UPDATE SKIP LOCKED"
        ") RETURNING *",
        session_id,
        limit,
    )


async def set_status(
    conn: asyncpg.Connection,
    session_id: str,
    status: SessionStatus,
    stop_reason: StopReason | None = None,
) -> None:
    await conn.execute(
        "UPDATE sessions SET status = $2, stop_reason = $3, updated_at = now(), "
        "terminated_at = CASE WHEN $2 = 'terminated' THEN now() ELSE terminated_at END "
        "WHERE id = $1",
        session_id,
        str(status),
        str(stop_reason) if stop_reason else None,
    )
    event_type = {
        SessionStatus.RUNNING: EventType.SESSION_STATUS_RUNNING,
        SessionStatus.IDLE: EventType.SESSION_STATUS_IDLE,
        SessionStatus.TERMINATED: EventType.SESSION_STATUS_TERMINATED,
    }.get(status)
    if event_type is not None:
        payload = {"stop_reason": str(stop_reason)} if stop_reason else {}
        await append_event(conn, session_id, event_type, payload, processed=True)


async def has_live_lease(conn: asyncpg.Connection, session_id: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT lease_expires_at > now() FROM sessions WHERE id = $1", session_id
        )
    )


async def acquire_lease(
    conn: asyncpg.Connection, session_id: str, service_account: str
) -> str | None:
    row = await conn.fetchrow(
        "UPDATE sessions s SET lease_id = gen_random_uuid(), "
        "  lease_expires_at = now() + make_interval(secs => $2), retry_count = 0 "
        "FROM environments e WHERE s.id = $1 AND s.environment_id = e.id "
        "  AND e.service_account_email = $3 "
        "  AND (s.lease_expires_at IS NULL OR s.lease_expires_at <= now()) "
        "  AND s.status <> 'terminated' "
        "RETURNING s.lease_id",
        session_id,
        config.LEASE_TTL_SECONDS,
        service_account,
    )
    return str(row["lease_id"]) if row else None


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def heartbeat(conn: asyncpg.Connection, session_id: str, lease_id: str) -> bool:
    if _uuid(lease_id) is None:
        return False
    result = await conn.execute(
        "UPDATE sessions SET lease_expires_at = now() + make_interval(secs => $3) "
        "WHERE id = $1 AND lease_id = $2::uuid",
        session_id,
        lease_id,
        config.LEASE_TTL_SECONDS,
    )
    return rowcount(result) == 1


async def release_lease(conn: asyncpg.Connection, session_id: str, lease_id: str) -> None:
    if _uuid(lease_id) is None:
        return
    await conn.execute(
        "UPDATE sessions SET lease_id = NULL, lease_expires_at = NULL "
        "WHERE id = $1 AND lease_id = $2::uuid",
        session_id,
        lease_id,
    )


async def stale_wakeable_sessions(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Sessions with queued work whose sandbox is gone."""
    return await conn.fetch(
        "SELECT s.* FROM sessions s WHERE s.status <> 'terminated' "
        "  AND (s.lease_expires_at IS NULL OR s.lease_expires_at <= now()) "
        "  AND EXISTS (SELECT 1 FROM session_events e "
        "              WHERE e.session_id = s.id AND e.processed_at IS NULL) "
        "ORDER BY s.updated_at LIMIT 20"
    )


async def running_sandbox_count(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM sessions WHERE lease_expires_at > now()")


async def upsert_confirmation(
    conn: asyncpg.Connection,
    session_id: str,
    call_hash: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_use_id: str | None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        "INSERT INTO tool_confirmations (id, session_id, call_hash, tool_use_id, tool_name, input) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "ON CONFLICT (session_id, call_hash) DO UPDATE SET tool_use_id = EXCLUDED.tool_use_id "
        "RETURNING *",
        new_id("confirmation"),
        session_id,
        call_hash,
        tool_use_id,
        tool_name,
        tool_input,
    )


async def get_confirmation(
    conn: asyncpg.Connection, session_id: str, call_hash: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM tool_confirmations WHERE session_id = $1 AND call_hash = $2",
        session_id,
        call_hash,
    )


async def decide_confirmation(
    conn: asyncpg.Connection,
    session_id: str,
    call_hash: str,
    status: str,
    principal: str,
    deny_message: str | None = None,
) -> bool:
    result = await conn.execute(
        "UPDATE tool_confirmations SET status = $3, decided_by = $4, decided_at = now(), "
        "  deny_message = $5 "
        "WHERE session_id = $1 AND call_hash = $2 AND status = 'pending'",
        session_id,
        call_hash,
        status,
        principal,
        deny_message,
    )
    return rowcount(result) == 1
