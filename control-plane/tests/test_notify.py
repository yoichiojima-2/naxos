import asyncio

from naxos_shared.events import EventType

from naxos_cp import db, notify, store

from .test_session_flow import make_agent


async def test_append_event_wakes_sse_waiters(client):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()

    waiter = asyncio.create_task(notify.wait(session["id"], timeout=10))
    await asyncio.sleep(0.5)
    async with db.transaction() as conn:
        await store.append_event(
            conn, session["id"], EventType.AGENT_MESSAGE, {"text": "hello"}, processed=True
        )
    try:
        assert await waiter is True
    finally:
        await notify.close()


async def test_wait_times_out_without_events(client):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    try:
        assert await notify.wait(session["id"], timeout=0.2) is False
    finally:
        await notify.close()


async def test_stream_deltas_reach_subscribers_without_touching_the_log(client, internal_client):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    sid = session["id"]

    queue = notify.subscribe_stream(sid)
    try:
        waiter = asyncio.create_task(notify.wait_or_delta(sid, queue, timeout=10))
        await asyncio.sleep(0.5)
        response = await internal_client.post(
            f"/internal/sessions/{sid}/stream", json={"stream": "run:1", "text": "hel"}
        )
        assert response.json() == {"ok": True}
        assert await waiter == {"stream": "run:1", "text": "hel"}
    finally:
        notify.unsubscribe_stream(sid, queue)
        await notify.close()

    events = (await client.get(f"/v1/sessions/{sid}/events")).json()["data"]
    assert all(e["type"] != "agent.message_delta" for e in events)


async def test_oversized_stream_frames_are_dropped_not_truncated(client, internal_client):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    response = await internal_client.post(
        f"/internal/sessions/{session['id']}/stream",
        json={"stream": "run:1", "text": "x" * 9000},
    )
    assert response.json() == {"ok": False}


async def test_wait_or_delta_still_reports_persisted_events(client):
    _, agent = await make_agent(client)
    session = (await client.post("/v1/sessions", json={"agent": {"id": agent["id"]}})).json()
    sid = session["id"]

    queue = notify.subscribe_stream(sid)
    try:
        waiter = asyncio.create_task(notify.wait_or_delta(sid, queue, timeout=10))
        await asyncio.sleep(0.5)
        async with db.transaction() as conn:
            await store.append_event(
                conn, sid, EventType.AGENT_MESSAGE, {"text": "hello"}, processed=True
            )
        assert await waiter is True
    finally:
        notify.unsubscribe_stream(sid, queue)
        await notify.close()
