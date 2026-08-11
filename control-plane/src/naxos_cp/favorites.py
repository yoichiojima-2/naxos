from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.ids import new_id
from pydantic import BaseModel

from . import db
from .auth import principal_of

router = APIRouter(prefix="/v1")

EntityType = Literal["agent", "session", "artifact", "skill"]
ENTITY_TABLES: dict[str, str] = {
    "agent": "agents",
    "session": "sessions",
    "artifact": "artifacts",
    "skill": "skills",
}


class FavoriteIn(BaseModel):
    entity_type: EntityType
    entity_id: str


@router.get("/favorites")
async def list_favorites(principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT id, entity_type, entity_id, created_at FROM favorites "
            "WHERE principal = $1 ORDER BY created_at",
            principal,
        )
    return {"data": [dict(r) for r in rows]}


@router.post("/favorites", status_code=201)
async def create_favorite(body: FavoriteIn, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        # FOR SHARE holds the entity row until commit, so a concurrent delete of it
        # cannot slip past this check and strand the favorite with nothing to clean it up.
        exists = await conn.fetchval(
            f"SELECT 1 FROM {ENTITY_TABLES[body.entity_type]} WHERE id = $1 FOR SHARE",
            body.entity_id,
        )
        if not exists:
            raise HTTPException(404, f"{body.entity_type} not found")
        # DO UPDATE (not DO NOTHING) so a duplicate still returns the existing row.
        row = await conn.fetchrow(
            "INSERT INTO favorites (id, principal, entity_type, entity_id) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (principal, entity_type, entity_id) "
            "  DO UPDATE SET created_at = favorites.created_at RETURNING *",
            new_id("favorite"),
            principal,
            body.entity_type,
            body.entity_id,
        )
    return dict(row)


@router.delete("/favorites/{entity_type}/{entity_id}")
async def delete_favorite(
    entity_type: EntityType, entity_id: str, principal: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        await conn.execute(
            "DELETE FROM favorites WHERE principal = $1 AND entity_type = $2 AND entity_id = $3",
            principal,
            entity_type,
            entity_id,
        )
    return {"entity_type": entity_type, "entity_id": entity_id, "deleted": True}


async def clear_for_entities(conn: asyncpg.Connection, *entity_ids: str) -> None:
    """Entity ids are globally unique across types (prefixed), so type isn't needed."""
    await conn.execute("DELETE FROM favorites WHERE entity_id = ANY($1)", list(entity_ids))
