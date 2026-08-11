import asyncio
import logging

import asyncpg

from . import config

log = logging.getLogger(__name__)

CHANNEL = "naxos_events"

_conn: asyncpg.Connection | None = None
_lock = asyncio.Lock()
_waiters: dict[str, set[asyncio.Event]] = {}


def _on_notify(connection, pid, channel, payload: str) -> None:
    for event in _waiters.get(payload, ()):
        event.set()


async def _ensure_listener() -> None:
    global _conn
    if _conn is not None and not _conn.is_closed():
        return
    async with _lock:
        if _conn is not None and not _conn.is_closed():
            return
        conn = await asyncpg.connect(config.DATABASE_URL)
        await conn.add_listener(CHANNEL, _on_notify)
        _conn = conn


async def close() -> None:
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        finally:
            _conn = None


async def wait(session_id: str, timeout: float) -> bool:
    """Wait for a new event on the session. True when notified, False on timeout.

    A lost listener connection degrades to timeout-paced polling, never to
    missed events: callers re-query after every return.
    """
    try:
        await _ensure_listener()
    except Exception:
        log.exception("event listener unavailable; falling back to timeout polling")
        await asyncio.sleep(timeout)
        return False
    event = asyncio.Event()
    _waiters.setdefault(session_id, set()).add(event)
    try:
        async with asyncio.timeout(timeout):
            await event.wait()
        return True
    except TimeoutError:
        return False
    finally:
        waiters = _waiters.get(session_id)
        if waiters is not None:
            waiters.discard(event)
            if not waiters:
                _waiters.pop(session_id, None)
