from typing import Any

import asyncpg
from naxos_shared.events import EventIn
from naxos_shared.ids import new_id

from . import store, wake


async def resolve_agent(
    conn: asyncpg.Connection, agent_id: str, version: int | None
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT a.*, v.version, v.default_budget_usd, v.vault_ids, v.memory_store_ids "
        "FROM agents a JOIN agent_versions v ON v.agent_id = a.id "
        "  AND v.version = COALESCE($2, a.latest_version) "
        "WHERE a.id = $1 AND a.archived_at IS NULL",
        agent_id,
        version,
    )


async def create(
    conn: asyncpg.Connection,
    agent: asyncpg.Record,
    *,
    initial_events: list[EventIn],
    principal: str,
    title: str | None = None,
    budget_usd: float | None = None,
    vault_ids: list[str] | None = None,
    memory_store_ids: list[str] | None = None,
    resources: list[dict[str, Any]] | None = None,
) -> asyncpg.Record:
    session_id = new_id("session")
    await conn.execute(
        "INSERT INTO sessions (id, agent_id, agent_version, environment_id, title, "
        "  budget_usd, vault_ids, memory_store_ids, resources, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        session_id,
        agent["id"],
        agent["version"],
        agent["environment_id"],
        title,
        budget_usd if budget_usd is not None else agent["default_budget_usd"],
        vault_ids or list(agent["vault_ids"]),
        memory_store_ids or list(agent["memory_store_ids"]),
        resources or [],
        principal,
    )
    for event in initial_events:
        event.validate_for_send()
        await store.append_event(
            conn, session_id, event.type, event.model_dump(mode="json"), principal
        )
    if initial_events:
        await wake.wake(conn, session_id)
    return await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
