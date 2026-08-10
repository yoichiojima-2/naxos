import logging

import asyncpg
from naxos_shared.events import EventType, SessionStatus, StopReason

from . import config, sandbox, store

log = logging.getLogger(__name__)


async def wake(conn: asyncpg.Connection, session_id: str) -> bool:
    """Start a sandbox for a session that has queued work.

    No-op when a sandbox already holds a live lease — it will pick the events up
    from its own queue poll.
    """
    row = await conn.fetchrow(
        "SELECT s.status, s.lease_expires_at, s.retry_count, a.disabled, e.sandbox_job_name "
        "FROM sessions s JOIN agents a ON a.id = s.agent_id "
        "JOIN environments e ON e.id = s.environment_id WHERE s.id = $1",
        session_id,
    )
    if row is None:
        raise LookupError(session_id)
    if row["status"] == str(SessionStatus.TERMINATED) or row["disabled"]:
        return False
    if row["lease_expires_at"] is not None and await store.has_live_lease(conn, session_id):
        return False
    if row["retry_count"] >= config.MAX_WAKE_RETRIES:
        await store.append_event(
            conn,
            session_id,
            EventType.SESSION_ERROR,
            {"error": "wake retries exhausted"},
            processed=True,
        )
        await store.set_status(conn, session_id, SessionStatus.IDLE, StopReason.RETRIES_EXHAUSTED)
        return False
    if await store.running_sandbox_count(conn) >= config.MAX_CONCURRENT_SANDBOXES:
        log.warning("sandbox concurrency cap reached; %s stays queued", session_id)
        return False

    await conn.execute(
        "UPDATE sessions SET status = 'rescheduling', retry_count = retry_count + 1, "
        "updated_at = now() WHERE id = $1",
        session_id,
    )
    execution = await sandbox.launch(row["sandbox_job_name"], session_id)
    await conn.execute(
        "UPDATE sessions SET execution_name = $2 WHERE id = $1", session_id, execution
    )
    return True
