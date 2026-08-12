import asyncio
import json
import logging

import asyncpg

from . import config

log = logging.getLogger(__name__)

CHANNEL = "naxos_events"
# Transient partial-text frames ride their own channel: payloads are the frame
# itself (never stored), so a lost notification loses nothing durable — the
# persisted agent.message supersedes every delta.
STREAM_CHANNEL = "naxos_stream"
MAX_NOTIFY_BYTES = 7800

_conn: asyncpg.Connection | None = None
_lock = asyncio.Lock()
_waiters: dict[str, set[asyncio.Event]] = {}
_stream_waiters: dict[str, set[asyncio.Queue]] = {}


def _on_notify(connection, pid, channel, payload: str) -> None:
    for event in _waiters.get(payload, ()):
        event.set()


def _on_stream(connection, pid, channel, payload: str) -> None:
    try:
        data = json.loads(payload)
    except ValueError:
        return
    session_id = data.pop("sid", None)
    for queue in _stream_waiters.get(session_id, ()):
        queue.put_nowait(data)


async def _ensure_listener() -> None:
    global _conn
    if _conn is not None and not _conn.is_closed():
        return
    async with _lock:
        if _conn is not None and not _conn.is_closed():
            return
        conn = await asyncpg.connect(config.DATABASE_URL)
        await conn.add_listener(CHANNEL, _on_notify)
        await conn.add_listener(STREAM_CHANNEL, _on_stream)
        _conn = conn


async def close() -> None:
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        finally:
            _conn = None


async def publish_stream(conn: asyncpg.Connection, session_id: str, delta: dict) -> bool:
    """Fan a transient frame out to this session's SSE listeners. Oversized
    frames are dropped, not truncated: the persisted message carries the text."""
    payload = json.dumps({"sid": session_id, **delta}, ensure_ascii=False)
    if len(payload.encode()) > MAX_NOTIFY_BYTES:
        return False
    await conn.execute("SELECT pg_notify($1, $2)", STREAM_CHANNEL, payload)
    return True


def subscribe_stream(session_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _stream_waiters.setdefault(session_id, set()).add(queue)
    return queue


def unsubscribe_stream(session_id: str, queue: asyncio.Queue) -> None:
    waiters = _stream_waiters.get(session_id)
    if waiters is not None:
        waiters.discard(queue)
        if not waiters:
            _stream_waiters.pop(session_id, None)


async def wait(session_id: str, timeout: float) -> bool:
    """Wait for a new event on the session. True when notified, False on timeout.

    A lost listener connection degrades to timeout-paced polling, never to
    missed events: callers re-query after every return.
    """
    result = await _wait(session_id, None, timeout)
    return result is True


async def wait_or_delta(session_id: str, queue: asyncio.Queue, timeout: float) -> dict | bool:
    """Like wait(), but also returns the next transient delta frame if one
    lands first. dict = a delta, True = a persisted event, False = timeout."""
    return await _wait(session_id, queue, timeout)


async def _wait(session_id: str, queue: asyncio.Queue | None, timeout: float) -> dict | bool:
    try:
        await _ensure_listener()
    except Exception:
        log.exception("event listener unavailable; falling back to timeout polling")
        await asyncio.sleep(timeout)
        return False
    if queue is not None and not queue.empty():
        return queue.get_nowait()
    event = asyncio.Event()
    _waiters.setdefault(session_id, set()).add(event)
    tasks = {asyncio.ensure_future(event.wait())}
    delta_task = None
    if queue is not None:
        delta_task = asyncio.ensure_future(queue.get())
        tasks.add(delta_task)
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if delta_task is not None and delta_task in done:
            return delta_task.result()
        return bool(done)
    finally:
        waiters = _waiters.get(session_id)
        if waiters is not None:
            waiters.discard(event)
            if not waiters:
                _waiters.pop(session_id, None)
