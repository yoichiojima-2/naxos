import asyncio
import contextlib

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from naxos_egress import main

# Every wait is bounded: a buffering proxy must fail these tests quickly rather
# than wedge the run with an in-flight request the server shutdown waits on.
TIMEOUT = 5.0


async def _serve(app) -> tuple[uvicorn.Server, asyncio.Task, int]:
    """A real server, not ASGITransport: that transport buffers whole responses,
    which is exactly what these tests must distinguish."""
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    return server, task, server.servers[0].sockets[0].getsockname()[1]


@pytest.fixture
async def upstream():
    """An SSE upstream that holds its second chunk until the test releases it."""
    state = {"release": asyncio.Event(), "seen": {}}

    async def events(request):
        state["seen"]["auth"] = request.headers.get("authorization")

        async def body():
            yield b"data: one\n\n"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(state["release"].wait(), timeout=TIMEOUT)
            yield b"data: two\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/mcp", events, methods=["GET", "POST"])])
    server, task, port = await _serve(app)
    state["port"] = port
    yield state
    state["release"].set()
    server.should_exit = True
    await asyncio.wait_for(task, timeout=TIMEOUT)


@pytest.fixture
async def proxy(upstream, monkeypatch):
    async def fake_route(token: str) -> dict:
        return {
            "target_url": f"http://127.0.0.1:{upstream['port']}",
            "header": "authorization",
            "value_prefix": "Bearer ",
            "secret_ref": "projects/p/secrets/vault-x",
        }

    monkeypatch.setattr(main, "_route", fake_route)
    monkeypatch.setattr(main, "_secret", lambda ref: "s3cret")
    server, task, port = await _serve(main.app)
    yield f"http://127.0.0.1:{port}"
    upstream["release"].set()
    server.should_exit = True
    await asyncio.wait_for(task, timeout=TIMEOUT)


async def test_forward_streams_sse_and_substitutes_the_credential(proxy, upstream):
    async with (
        httpx.AsyncClient(timeout=TIMEOUT) as client,
        client.stream(
            "GET", f"{proxy}/r/tok/mcp", headers={"authorization": "Bearer sandbox-oidc"}
        ) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = response.aiter_raw()
        # Arrives while the upstream still holds the second chunk: a
        # buffering proxy cannot get here before the timeout.
        first = await asyncio.wait_for(anext(chunks), timeout=TIMEOUT)
        assert b"data: one" in first
        upstream["release"].set()
        rest = b"".join([c async for c in chunks])
        assert b"data: two" in rest

    # The sandbox's own OIDC header is replaced, never forwarded upstream.
    assert upstream["seen"]["auth"] == "Bearer s3cret"
