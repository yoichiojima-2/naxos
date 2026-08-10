import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

log = logging.getLogger(__name__)

INTERNAL_URL = os.environ.get("INTERNAL_URL", "").rstrip("/")
ROUTE_CACHE_SECONDS = 60
SECRET_CACHE_SECONDS = 300
HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length", "authorization"}

app = FastAPI(title="naxos-egress")
_routes: dict[str, tuple[float, dict]] = {}
_secrets: dict[str, tuple[float, str]] = {}
_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=False)


def _identity_headers() -> dict[str, str]:
    if os.environ.get("NAXOS_DEV_SA"):
        return {"x-naxos-dev-sa": os.environ["NAXOS_DEV_SA"]}
    from google.auth.transport.requests import Request as AuthRequest
    from google.oauth2 import id_token

    token = id_token.fetch_id_token(AuthRequest(), INTERNAL_URL)
    return {"authorization": f"Bearer {token}"}


async def _route(token: str) -> dict:
    cached = _routes.get(token)
    if cached and time.monotonic() - cached[0] < ROUTE_CACHE_SECONDS:
        return cached[1]
    response = await _client.get(
        f"{INTERNAL_URL}/internal/egress/routes/{token}", headers=_identity_headers()
    )
    if response.status_code == 404:
        raise HTTPException(404, "unknown route")
    response.raise_for_status()
    route = response.json()
    _routes[token] = (time.monotonic(), route)
    return route


def _secret(secret_ref: str) -> str:
    cached = _secrets.get(secret_ref)
    if cached and time.monotonic() - cached[0] < SECRET_CACHE_SECONDS:
        return cached[1]
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    value = client.access_secret_version(name=f"{secret_ref}/versions/latest").payload.data.decode()
    _secrets[secret_ref] = (time.monotonic(), value)
    return value


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.api_route(
    "/r/{token}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def forward(token: str, path: str, request: Request) -> Response:
    route = await _route(token)
    secret = _secret(route["secret_ref"])

    target = route["target_url"].rstrip("/")
    url = f"{target}/{path}" if path else target
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers[route["header"]] = f"{route['value_prefix']}{secret}"

    upstream = await _client.request(
        request.method, url, headers=headers, content=await request.body()
    )
    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() not in excluded},
        media_type=upstream.headers.get("content-type"),
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
