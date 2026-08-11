import asyncio
import contextlib

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from naxos_sbx import mcp_gateway
from naxos_sbx.mcp_gateway import McpGateway

# Every wait is bounded: a forwarder that buffers must fail quickly rather than
# wedge the run with an in-flight request the server shutdown waits on.
TIMEOUT = 5.0


@pytest.fixture
async def gateway(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "DEV_SA", "dev@example.iam")
    gw = McpGateway()
    yield gw
    await gw.aclose()


async def _serve(app) -> tuple[uvicorn.Server, asyncio.Task, int]:
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


async def test_rewrite_only_touches_run_app_urls(gateway):
    servers = {
        "github": {"type": "http", "url": "https://naxos-egress-123.a.run.app/r/tok"},
        "public": {"type": "http", "url": "https://mcp.example.com/mcp"},
    }
    rewritten = await gateway.rewrite(servers)
    assert rewritten["public"] == servers["public"]
    assert rewritten["github"]["url"] == f"http://127.0.0.1:{gateway.port}/github"


async def test_rewrite_without_run_app_urls_starts_nothing(gateway):
    rewritten = await gateway.rewrite({"public": {"url": "https://mcp.example.com"}})
    assert gateway.port is None
    assert rewritten == {"public": {"url": "https://mcp.example.com"}}


async def test_forwards_and_streams_sse(gateway, monkeypatch):
    release = asyncio.Event()
    seen: dict = {}

    async def events(request):
        seen["auth"] = request.headers.get("authorization")

        async def body():
            yield b"data: one\n\n"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(release.wait(), timeout=TIMEOUT)
            yield b"data: two\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/r/tok/{rest:path}", events, methods=["GET", "POST"])])
    upstream, task, port = await _serve(app)

    rewritten = await gateway.rewrite({"github": {"url": "https://x-123.a.run.app/r/tok"}})
    # Point the forwarder at the local upstream, keeping the Cloud Run audience
    # the rewrite captured.
    gateway._targets["github"] = (f"http://127.0.0.1:{port}/r/tok", gateway._targets["github"][1])

    monkeypatch.setattr(mcp_gateway, "DEV_SA", "")

    def fake_fetch(auth_request, audience):
        seen["audience"] = audience
        return "oidc-token"

    monkeypatch.setattr("google.oauth2.id_token.fetch_id_token", fake_fetch)

    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT) as client,
            client.stream(
                "GET",
                f"{rewritten['github']['url']}/sse",
                headers={"authorization": "Bearer agent"},
            ) as response,
        ):
            assert response.status_code == 200
            chunks = response.aiter_raw()
            # Arrives while the upstream still holds the second chunk: a
            # buffering forwarder cannot get here before the timeout.
            first = await asyncio.wait_for(anext(chunks), timeout=TIMEOUT)
            assert b"data: one" in first
            release.set()
            rest = b"".join([c async for c in chunks])
            assert b"data: two" in rest

        # The sandbox's inbound header is replaced by a freshly minted ID token
        # for the Cloud Run service the rewrite captured, not for the forwarder.
        assert seen["auth"] == "Bearer oidc-token"
        assert seen["audience"] == "https://x-123.a.run.app"
    finally:
        release.set()
        upstream.should_exit = True
        await asyncio.wait_for(task, timeout=TIMEOUT)


async def test_redirects_are_kept_on_the_gateway(gateway):
    """A Location the client followed itself would reach Cloud Run without an ID
    token, so in-path redirects are mapped back here."""
    await gateway.rewrite({"github": {"url": "https://egress-1.a.run.app/r/tok"}})
    base = f"http://127.0.0.1:{gateway.port}/github"
    target = "https://egress-1.a.run.app/r/tok"

    assert gateway._local_location(f"{target}/", "github", target) == f"{base}/"
    assert (
        gateway._local_location(f"{target}/messages?s=1", "github", target)
        == f"{base}/messages?s=1"
    )
    # Nothing to map: left alone rather than pointed somewhere wrong.
    for untouched in ("/relative", "https://elsewhere.example.com/x"):
        assert gateway._local_location(untouched, "github", target) == untouched


async def test_unknown_server_is_404(gateway):
    await gateway.rewrite({"github": {"url": "https://x-123.a.run.app/r/tok"}})
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{gateway.port}/nope")
    assert response.status_code == 404
