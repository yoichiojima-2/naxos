import logging
import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel

from naxos import audit
from naxos.config import ROLES, ROOT, configure_logging
from naxos.runner import RoleDisabled, execute

logger = logging.getLogger(__name__)

configure_logging()
app = FastAPI()

IAP_AUDIENCE = os.environ.get("IAP_AUDIENCE")
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"


class RunRequest(BaseModel):
    prompt: str
    role: str
    resume: str | None = None


def principal_of(request: Request) -> str:
    if not IAP_AUDIENCE:
        return os.environ.get("DEV_PRINCIPAL", "local-dev")
    token = request.headers.get("x-goog-iap-jwt-assertion")
    if not token:
        raise HTTPException(status_code=401, detail="missing IAP assertion")
    try:
        claims = id_token.verify_token(token, google_requests.Request(), audience=IAP_AUDIENCE, certs_url=IAP_CERTS_URL)
    except Exception as e:
        logger.error(f"IAP token verification failed: {e}")
        raise HTTPException(status_code=401, detail="invalid IAP assertion") from None
    return claims["email"]


@app.get("/api/roles")
def roles() -> list[str]:
    return sorted(ROLES)


@app.post("/api/run")
async def run(body: RunRequest, request: Request) -> dict:
    principal = principal_of(request)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role}")
    try:
        result = await execute(body.prompt, body.role, body.resume, principal=principal, fresh_ws=True)
    except RoleDisabled as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {
        "text": result.text,
        "session_id": result.session_id,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "is_error": result.is_error,
        "tool_calls": result.tool_calls,
    }


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return audit.recent_runs(limit)


static = ROOT / "frontend" / "out"
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True))
