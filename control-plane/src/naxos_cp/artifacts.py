"""Artifacts: named outputs an agent publishes from its session workspace.

Metadata lives in Postgres; content lives in the environment's session bucket
at sessions/{session_id}/artifacts/{name}. Sharing mints a stable token URL —
still behind IAP, so an artifact never leaves the org boundary.
"""

import secrets

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import config, db, gcs
from .auth import principal_of

router = APIRouter(prefix="/v1")


def blob_path(session_id: str, name: str) -> str:
    return f"sessions/{session_id}/artifacts/{name}"


def share_url(token: str) -> str:
    # The in-app viewer route: share_url is handed to humans (agent chat
    # messages, the UI); raw content stays at /v1/artifacts/shared/{token}/content.
    path = f"/#artifacts/shared/{token}"
    return f"{config.PUBLIC_URL.rstrip('/')}{path}" if config.PUBLIC_URL else path


def validate_name(name: str) -> None:
    if ".." in name.split("/") or name.startswith("/"):
        raise HTTPException(400, "artifact name may not contain .. or start with /")


def serialize(row: asyncpg.Record | dict) -> dict:
    out = dict(row)
    out.pop("session_bucket", None)
    if out.get("share_token"):
        out["share_url"] = share_url(out["share_token"])
    return out


async def set_shared(
    conn: asyncpg.Connection, artifact_id: str, shared: bool, shared_by: str = ""
) -> asyncpg.Record | None:
    """Mint or revoke an artifact's share token. Sharing is idempotent: an
    existing token is kept so the URL stays stable. shared_by is recorded
    only when sharing; revoking clears it."""
    if shared:
        return await conn.fetchrow(
            "UPDATE artifacts SET share_token = COALESCE(share_token, $2), "
            "  shared_at = COALESCE(shared_at, now()), shared_by = COALESCE(shared_by, $3), "
            "  updated_at = now() WHERE id = $1 RETURNING *",
            artifact_id,
            secrets.token_urlsafe(24),
            shared_by,
        )
    return await conn.fetchrow(
        "UPDATE artifacts SET share_token = NULL, shared_at = NULL, shared_by = NULL, "
        "  updated_at = now() WHERE id = $1 RETURNING *",
        artifact_id,
    )


async def _fetch_with_bucket(conn: asyncpg.Connection, artifact_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT a.*, e.session_bucket FROM artifacts a "
        "JOIN environments e ON e.id = a.environment_id WHERE a.id = $1",
        artifact_id,
    )
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


def _content_response(row: asyncpg.Record, content: bytes | None) -> Response:
    if content is None:
        raise HTTPException(404, "artifact content not found")
    # attachment + nosniff: agent-authored content must never render on the
    # API origin, where it would run with the viewer's IAP session.
    return Response(
        content=content,
        media_type=row["content_type"],
        headers={
            "content-disposition": f'attachment; filename="{row["name"].split("/")[-1]}"',
            "x-content-type-options": "nosniff",
        },
    )


# --- shared (token) access; declared before /{artifact_id} routes -----------


@router.get("/artifacts/shared/{token}")
async def get_shared(token: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow("SELECT * FROM artifacts WHERE share_token = $1", token)
    if row is None:
        raise HTTPException(404, "shared artifact not found")
    return serialize(row)


@router.get("/artifacts/shared/{token}/content")
async def get_shared_content(token: str, _: str = Depends(principal_of)) -> Response:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT a.*, e.session_bucket FROM artifacts a "
            "JOIN environments e ON e.id = a.environment_id WHERE a.share_token = $1",
            token,
        )
    if row is None:
        raise HTTPException(404, "shared artifact not found")
    content = await gcs.download(row["session_bucket"], blob_path(row["session_id"], row["name"]))
    return _content_response(row, content)


# --- artifact management ----------------------------------------------------


@router.get("/artifacts")
async def list_artifacts(
    session_id: str | None = None,
    agent_id: str | None = None,
    _: str = Depends(principal_of),
) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT * FROM artifacts WHERE ($1::text IS NULL OR session_id = $1) "
            "  AND ($2::text IS NULL OR agent_id = $2) ORDER BY updated_at DESC LIMIT 500",
            session_id,
            agent_id,
        )
    return {"data": [serialize(r) for r in rows]}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow("SELECT * FROM artifacts WHERE id = $1", artifact_id)
    if row is None:
        raise HTTPException(404, "artifact not found")
    return serialize(row)


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(artifact_id: str, _: str = Depends(principal_of)) -> Response:
    async with db.transaction() as conn:
        row = await _fetch_with_bucket(conn, artifact_id)
    content = await gcs.download(row["session_bucket"], blob_path(row["session_id"], row["name"]))
    return _content_response(row, content)


class ArtifactPatch(BaseModel):
    description: str | None = None


@router.patch("/artifacts/{artifact_id}")
async def patch_artifact(
    artifact_id: str, body: ArtifactPatch, _: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        if "description" in body.model_fields_set:
            row = await conn.fetchrow(
                "UPDATE artifacts SET description = $2, updated_at = now() "
                "WHERE id = $1 RETURNING *",
                artifact_id,
                body.description,
            )
        else:
            row = await conn.fetchrow("SELECT * FROM artifacts WHERE id = $1", artifact_id)
    if row is None:
        raise HTTPException(404, "artifact not found")
    return serialize(row)


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await _fetch_with_bucket(conn, artifact_id)
        await conn.execute("DELETE FROM artifacts WHERE id = $1", artifact_id)
    await gcs.delete(row["session_bucket"], blob_path(row["session_id"], row["name"]))
    return {"id": artifact_id, "deleted": True}


@router.post("/artifacts/{artifact_id}/share")
async def share_artifact(artifact_id: str, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await set_shared(conn, artifact_id, True, principal)
    if row is None:
        raise HTTPException(404, "artifact not found")
    return serialize(row)


@router.delete("/artifacts/{artifact_id}/share")
async def unshare_artifact(artifact_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await set_shared(conn, artifact_id, False)
    if row is None:
        raise HTTPException(404, "artifact not found")
    return serialize(row)
