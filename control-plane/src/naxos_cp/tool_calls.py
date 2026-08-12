"""The execution record: what every agent tried to do, and what happened.

Reads Postgres only. The audit dataset in BigQuery is the archive; `naxos-api`
holds no BigQuery access, so this surface never needs it.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from . import db
from .auth import principal_of

router = APIRouter(prefix="/v1")

FILTERS = (
    "($1::bigint IS NULL OR id < $1) "
    "AND ($2::text IS NULL OR session_id = $2) "
    "AND ($3::text IS NULL OR agent_id = $3) "
    "AND ($4::text IS NULL OR environment_id = $4) "
    "AND ($5::text IS NULL OR tool_name = $5) "
    "AND ($6::text IS NULL OR principal = $6) "
    "AND ($7::text IS NULL OR decision = $7) "
    "AND ($8::text IS NULL OR result_status = $8) "
    "AND ($9::timestamptz IS NULL OR decided_at >= $9) "
    "AND ($10::timestamptz IS NULL OR decided_at < $10)"
)

EXPORT_MAX_ROWS = 50_000


def _serialise(row) -> dict:
    # exported_at is the BigQuery watermark, not part of the record.
    record = {k: v for k, v in dict(row).items() if k != "exported_at"}
    record["id"] = str(record["id"])
    for key in ("decided_at", "resulted_at"):
        record[key] = record[key].isoformat() if record[key] else None
    return record


async def _fetch(conn, args: list, limit: int) -> list:
    return await conn.fetch(
        f"SELECT * FROM tool_calls WHERE {FILTERS} ORDER BY id DESC LIMIT ${len(args) + 1}",
        *args,
        limit,
    )


def _args(
    cursor: str | None,
    session_id: str | None,
    agent_id: str | None,
    environment_id: str | None,
    tool_name: str | None,
    principal: str | None,
    decision: str | None,
    result_status: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list:
    return [
        int(cursor) if cursor and cursor.isdigit() else None,
        session_id,
        agent_id,
        environment_id,
        tool_name,
        principal,
        decision,
        result_status,
        since,
        until,
    ]


@router.get("/tool_calls")
async def list_tool_calls(
    session_id: str | None = None,
    agent_id: str | None = None,
    environment_id: str | None = None,
    tool_name: str | None = None,
    principal: str | None = None,
    decision: str | None = None,
    result_status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: str = Depends(principal_of),
) -> dict:
    args = _args(
        cursor,
        session_id,
        agent_id,
        environment_id,
        tool_name,
        principal,
        decision,
        result_status,
        since,
        until,
    )
    async with db.transaction() as conn:
        rows = await _fetch(conn, args, limit)
    return {
        "data": [_serialise(r) for r in rows],
        "next_cursor": str(rows[-1]["id"]) if len(rows) == limit else None,
    }


@router.get("/tool_calls/export")
async def export_tool_calls(
    session_id: str | None = None,
    agent_id: str | None = None,
    environment_id: str | None = None,
    tool_name: str | None = None,
    principal: str | None = None,
    decision: str | None = None,
    result_status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    _: str = Depends(principal_of),
) -> StreamingResponse:
    """The whole filtered record as newline-delimited JSON — the hand-it-over surface."""
    args = _args(
        None,
        session_id,
        agent_id,
        environment_id,
        tool_name,
        principal,
        decision,
        result_status,
        since,
        until,
    )

    async def lines():
        remaining = EXPORT_MAX_ROWS
        async with db.transaction() as conn:
            while remaining > 0:
                rows = await _fetch(conn, args, min(1000, remaining))
                if not rows:
                    return
                for row in rows:
                    yield json.dumps(_serialise(row)) + "\n"
                remaining -= len(rows)
                args[0] = rows[-1]["id"]

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"content-disposition": 'attachment; filename="tool_calls.ndjson"'},
    )
