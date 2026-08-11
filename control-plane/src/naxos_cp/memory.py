from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.ids import new_id
from naxos_shared.paths import unsafe_relpath
from pydantic import BaseModel, Field

from . import config, db, store
from .auth import principal_of

router = APIRouter(prefix="/v1")


class StoreIn(BaseModel):
    name: str


@router.post("/memory_stores", status_code=201)
async def create_store(body: StoreIn, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        existing = await conn.fetchval("SELECT 1 FROM memory_stores WHERE name = $1", body.name)
        if existing:
            raise HTTPException(409, "memory store name already exists")
        row = await conn.fetchrow(
            "INSERT INTO memory_stores (id, name) VALUES ($1, $2) RETURNING *",
            new_id("memory_store"),
            body.name,
        )
    return dict(row)


@router.get("/memory_stores")
async def list_stores(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT s.*, (SELECT count(*) FROM memories m WHERE m.store_id = s.id) AS file_count "
            "FROM memory_stores s ORDER BY s.name"
        )
        usage = await conn.fetch(
            "SELECT unnest(v.memory_store_ids) AS store_id, a.name FROM agents a "
            "JOIN agent_versions v ON v.agent_id = a.id AND v.version = a.latest_version "
            "WHERE a.archived_at IS NULL ORDER BY a.name"
        )
    used_by: dict[str, list[str]] = {}
    for u in usage:
        used_by.setdefault(u["store_id"], []).append(u["name"])
    return {"data": [dict(r) | {"used_by": used_by.get(r["id"], [])} for r in rows]}


@router.patch("/memory_stores/{store_id}")
async def rename_store(store_id: str, body: StoreIn, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        taken = await conn.fetchval(
            "SELECT 1 FROM memory_stores WHERE name = $1 AND id != $2", body.name, store_id
        )
        if taken:
            raise HTTPException(409, "memory store name already exists")
        row = await conn.fetchrow(
            "UPDATE memory_stores SET name = $2 WHERE id = $1 RETURNING *", store_id, body.name
        )
    if row is None:
        raise HTTPException(404, "memory store not found")
    return dict(row)


@router.delete("/memory_stores/{store_id}")
async def delete_store(store_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        agents = await conn.fetch(
            "SELECT DISTINCT a.name FROM agents a "
            "JOIN agent_versions v ON v.agent_id = a.id "
            "WHERE a.archived_at IS NULL AND $1 = ANY(v.memory_store_ids) ORDER BY a.name",
            store_id,
        )
        if agents:
            names = ", ".join(a["name"] for a in agents)
            raise HTTPException(409, f"memory store is attached to agents: {names}")
        active = await conn.fetchval(
            "SELECT count(*) FROM sessions WHERE status != 'terminated' "
            "AND $1 = ANY(memory_store_ids)",
            store_id,
        )
        if active:
            raise HTTPException(409, f"memory store is in use by {active} active session(s)")
        result = await conn.execute("DELETE FROM memory_stores WHERE id = $1", store_id)
    if db.rowcount(result) != 1:
        raise HTTPException(404, "memory store not found")
    return {"id": store_id, "deleted": True}


class MemoryIn(BaseModel):
    path: str = Field(pattern=r"^[a-zA-Z0-9._/-]{1,200}$")
    content: str


@router.post("/memory_stores/{store_id}/memories", status_code=201)
async def put_memory(store_id: str, body: MemoryIn, principal: str = Depends(principal_of)) -> dict:
    if len(body.content.encode()) > config.MAX_MEMORY_BYTES:
        raise HTTPException(413, "memory content exceeds 64KB")
    if unsafe_relpath(body.path):
        raise HTTPException(400, "path must be relative with no empty or .. segments")
    async with db.transaction() as conn:
        exists = await conn.fetchval("SELECT 1 FROM memory_stores WHERE id = $1", store_id)
        if not exists:
            raise HTTPException(404, "memory store not found")
        row = await store.upsert_memory(conn, store_id, body.path, body.content, principal)
    return dict(row)


@router.get("/memory_stores/{store_id}/memories")
async def list_memories(store_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT id, store_id, path, octet_length(content) AS size, updated_by, updated_at "
            'FROM memories WHERE store_id = $1 ORDER BY path COLLATE "C"',
            store_id,
        )
    return {"data": [dict(r) for r in rows]}


@router.get("/memory_stores/{store_id}/memories/{memory_id}")
async def get_memory(store_id: str, memory_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM memories WHERE id = $1 AND store_id = $2", memory_id, store_id
        )
    if row is None:
        raise HTTPException(404, "memory not found")
    return dict(row)


@router.delete("/memory_stores/{store_id}/memories/{memory_id}")
async def delete_memory(store_id: str, memory_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        result = await conn.execute(
            "DELETE FROM memories WHERE id = $1 AND store_id = $2", memory_id, store_id
        )
    if db.rowcount(result) != 1:
        raise HTTPException(404, "memory not found")
    return {"id": memory_id, "deleted": True}
