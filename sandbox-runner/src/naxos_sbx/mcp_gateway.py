import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from .config import DEV_SA

log = logging.getLogger(__name__)

HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length", "authorization"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
TOKEN_CACHE_SECONDS = 300


class McpGateway:
    """Localhost forwarder that authenticates the SDK's MCP traffic to Cloud Run.

    The SDK's MCP client cannot mint Google OIDC ID tokens, so calls to the
    egress proxy or a self-hosted connector service would fail Cloud Run IAM.
    A token attached once at wake would also expire mid-burst; this forwarder
    mints per request (cached briefly) and streams both directions.
    """

    def __init__(self) -> None:
        self.port: int | None = None
        self._targets: dict[str, tuple[str, str]] = {}
        self._tokens: dict[str, tuple[float, str]] = {}
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=None), follow_redirects=False
        )

    @staticmethod
    def _proxied(server: Any) -> str | None:
        if not isinstance(server, dict):
            return None
        url = server.get("url") or ""
        host = httpx.URL(url).host if url else ""
        return url if host.endswith(".run.app") else None

    async def rewrite(self, mcp_servers: dict[str, Any]) -> dict[str, Any]:
        """Point every *.run.app MCP server at this forwarder, starting it on
        first use. Other servers pass through untouched."""
        rewritten: dict[str, Any] = {}
        for name, server in (mcp_servers or {}).items():
            url = self._proxied(server)
            if url:
                await self._start()
                parts = httpx.URL(url)
                self._targets[name] = (url.rstrip("/"), f"{parts.scheme}://{parts.host}")
                rewritten[name] = {**server, "url": f"http://127.0.0.1:{self.port}/{name}"}
            else:
                rewritten[name] = server
        return rewritten

    async def _start(self) -> None:
        if self._server is not None:
            return
        app = Starlette(
            routes=[
                Route("/{name}", self._forward, methods=METHODS),
                Route("/{name}/{rest:path}", self._forward, methods=METHODS),
            ]
        )
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        )
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._task.done():
                raise RuntimeError("MCP gateway failed to start") from self._task.exception()
            await asyncio.sleep(0.02)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=5)
        await self._client.aclose()

    async def _token(self, audience: str) -> str | None:
        if DEV_SA:
            return None
        cached = self._tokens.get(audience)
        if cached and time.monotonic() - cached[0] < TOKEN_CACHE_SECONDS:
            return cached[1]
        from google.auth.transport.requests import Request as AuthRequest
        from google.oauth2 import id_token

        token = await asyncio.to_thread(id_token.fetch_id_token, AuthRequest(), audience)
        self._tokens[audience] = (time.monotonic(), token)
        return token

    async def _forward(self, request: Request) -> Response:
        name = request.path_params["name"]
        entry = self._targets.get(name)
        if entry is None:
            return Response("unknown MCP server", status_code=404)
        target, audience = entry
        rest = request.path_params.get("rest", "")
        url = f"{target}/{rest}" if rest else target
        if request.url.query:
            url = f"{url}?{request.url.query}"

        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        token = await self._token(audience)
        if token:
            headers["authorization"] = f"Bearer {token}"

        # The request body is buffered (MCP client messages are small) but the
        # response is streamed: passing a stream here would force chunked
        # encoding even on bodyless GETs, which some upstreams reject.
        upstream = await self._client.send(
            self._client.build_request(
                request.method, url, headers=headers, content=await request.body()
            ),
            stream=True,
        )
        excluded = {"transfer-encoding", "connection"}
        out = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded}
        # A redirect the client followed on its own would leave the gateway and
        # so arrive at Cloud Run with no ID token; keep it pointed back here.
        if "location" in out:
            out["location"] = self._local_location(out["location"], name, target)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=out,
            background=BackgroundTask(upstream.aclose),
        )

    def _local_location(self, location: str, name: str, target: str) -> str:
        base = f"http://127.0.0.1:{self.port}/{name}"
        if location.startswith(target):
            return base + location[len(target) :]
        origin = str(httpx.URL(target).copy_with(raw_path=b"/")).rstrip("/")
        if location.startswith(origin):
            log.warning("MCP redirect outside the mapped path: %s", location)
        return location
