from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db
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
        rows = await conn.fetch("SELECT * FROM memory_stores ORDER BY name")
    return {"data": [dict(r) for r in rows]}


class MemoryIn(BaseModel):
    path: str = Field(pattern=r"^[a-zA-Z0-9._/-]{1,200}$")
    content: str


@router.post("/memory_stores/{store_id}/memories", status_code=201)
async def put_memory(store_id: str, body: MemoryIn, principal: str = Depends(principal_of)) -> dict:
    if len(body.content.encode()) > config.MAX_MEMORY_BYTES:
        raise HTTPException(413, "memory content exceeds 64KB")
    segments = body.path.split("/")
    if ".." in segments or "" in segments:
        raise HTTPException(400, "path may not contain empty or .. segments")
    async with db.transaction() as conn:
        store = await conn.fetchval("SELECT 1 FROM memory_stores WHERE id = $1", store_id)
        if not store:
            raise HTTPException(404, "memory store not found")
        row = await conn.fetchrow(
            "INSERT INTO memories (id, store_id, path, content, updated_by) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (store_id, path) DO UPDATE SET content = EXCLUDED.content, "
            "  updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
            new_id("memory"),
            store_id,
            body.path,
            body.content,
            principal,
        )
    return dict(row)


@router.get("/memory_stores/{store_id}/memories")
async def list_memories(store_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT id, store_id, path, length(content) AS size, updated_by, updated_at "
            "FROM memories WHERE store_id = $1 ORDER BY path",
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
