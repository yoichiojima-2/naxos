import asyncio
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from google.cloud import storage

from . import db
from .auth import principal_of

router = APIRouter(prefix="/v1")

_client: storage.Client | None = None


def _storage() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


async def _session_bucket(session_id: str) -> str:
    async with db.transaction() as conn:
        bucket = await conn.fetchval(
            "SELECT e.session_bucket FROM sessions s JOIN environments e "
            "ON e.id = s.environment_id WHERE s.id = $1",
            session_id,
        )
    if bucket is None:
        raise HTTPException(404, "session not found")
    return bucket


@router.get("/sessions/{session_id}/workspace")
async def list_workspace(session_id: str, _: str = Depends(principal_of)) -> dict:
    bucket_name = await _session_bucket(session_id)
    prefix = f"sessions/{session_id}/ws/"

    def _list() -> list[dict]:
        blobs = _storage().bucket(bucket_name).list_blobs(prefix=prefix)
        return [
            {"path": blob.name[len(prefix) :], "size": blob.size}
            for blob in blobs
            if blob.name != prefix
        ]

    return {"data": await asyncio.to_thread(_list)}


@router.get("/sessions/{session_id}/workspace/{path:path}")
async def get_workspace_file(
    session_id: str, path: str, _: str = Depends(principal_of)
) -> Response:
    if ".." in path.split("/"):
        raise HTTPException(400, "path may not contain ..")
    bucket_name = await _session_bucket(session_id)
    blob_name = f"sessions/{session_id}/ws/{path}"

    def _download() -> bytes | None:
        blob = _storage().bucket(bucket_name).blob(blob_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    content = await asyncio.to_thread(_download)
    if content is None:
        raise HTTPException(404, "artifact not found")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type)
