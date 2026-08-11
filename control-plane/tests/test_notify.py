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
