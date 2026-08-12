"""The approval inbox: every tool call waiting on a human, across all sessions.

Deciding one is still `POST /v1/sessions/{id}/events` with a `user.tool_confirmation`
— the rows here carry the `session_id` and `call_hash` that call needs. Keeping the
one decision path means the audit write, the wake, and the pending-row update cannot
drift from a second implementation.
"""

from fastapi import APIRouter, Depends, Query

from . import db
from .auth import principal_of

router = APIRouter(prefix="/v1")

# The turn's principal is the last user event that carried one — the human whose
# work the agent is doing, which is who a reviewer needs to see.
LIST_SQL = """
SELECT c.id, c.session_id, c.call_hash, c.tool_use_id, c.tool_name, c.input,
       c.status, c.deny_message, c.requested_at, c.expires_at, c.decided_by, c.decided_at,
       s.title AS session_title, s.status AS session_status, s.stop_reason,
       s.environment_id, s.agent_id, s.agent_version,
       a.name AS agent_name, a.disabled AS agent_disabled,
       COALESCE(s.turn_principal, s.created_by) AS requested_for
  FROM tool_confirmations c
  JOIN sessions s ON s.id = c.session_id
  JOIN agents a ON a.id = s.agent_id
 WHERE ($1::text IS NULL OR c.status = $1)
   AND ($2::text IS NULL OR s.agent_id = $2)
   AND ($3::text IS NULL OR s.environment_id = $3)
   AND ($4::text IS NULL OR c.session_id = $4)
 ORDER BY c.requested_at
 LIMIT $5
"""


@router.get("/tool_confirmations")
async def list_confirmations(
    status: str | None = "pending",
    agent_id: str | None = None,
    environment_id: str | None = None,
    session_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: str = Depends(principal_of),
) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(LIST_SQL, status, agent_id, environment_id, session_id, limit)
        pending = await conn.fetchval(
            "SELECT count(*) FROM tool_confirmations WHERE status = 'pending'"
        )
    return {"data": [dict(r) for r in rows], "pending": pending}
