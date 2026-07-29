import asyncio
import json
import logging
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import InvalidArgument, NotFound
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel

from naxos import audit
from naxos.agent import AgentRun
from naxos.artifacts import browse
from naxos.config import BUCKET, ROLES, ROOT, configure_logging
from naxos.gcs import CloudStorage
from naxos.runner import RoleDisabled, execute
from naxos.schedules import PREFIX, Schedules

logger = logging.getLogger(__name__)

configure_logging()
app = FastAPI()

IAP_AUDIENCE = os.environ.get("IAP_AUDIENCE")
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"


class RunRequest(BaseModel):
    prompt: str
    role: str
    resume: str | None = None


class ScheduleUpdate(BaseModel):
    name: str
    cron: str
    prompt: str
    paused: bool = False


class ScheduleCreate(ScheduleUpdate):
    role: str


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


@app.get("/api/me")
def me(request: Request) -> dict:
    return {"email": principal_of(request)}


@app.get("/api/roles")
def roles() -> list[str]:
    return sorted(ROLES)


def wire(event: dict) -> dict:
    """Shape an internal event for the browser: tool inputs stay server-side,
    except a schedule proposal, which becomes a first-class event."""
    if event.get("event") != "tool":
        return event
    if event["name"].endswith("propose_schedule"):
        return {"event": "proposal", "kind": "schedule", **event["input"]}
    return {"event": "tool", "name": event["name"]}


def run_payload(result: AgentRun) -> dict:
    return {
        "text": result.text,
        "session_id": result.session_id,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "is_error": result.is_error,
        "tool_calls": result.tool_calls,
    }


@app.post("/api/run")
async def run(body: RunRequest, request: Request) -> dict:
    principal = principal_of(request)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role}")
    try:
        result = await execute(body.prompt, body.role, body.resume, principal=principal, fresh_ws=True, keep_alive=True)
    except RoleDisabled as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return run_payload(result)


@app.post("/api/run/stream")
async def run_stream(body: RunRequest, request: Request) -> StreamingResponse:
    principal = principal_of(request)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role}")

    async def events():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        task = asyncio.create_task(
            execute(
                body.prompt,
                body.role,
                body.resume,
                principal=principal,
                fresh_ws=True,
                on_event=queue.put_nowait,
                keep_alive=True,
            )
        )
        task.add_done_callback(lambda _: queue.put_nowait(None))
        while (event := await queue.get()) is not None:
            yield json.dumps(wire(event)) + "\n"
        try:
            result = task.result()
        except (RoleDisabled, FileNotFoundError) as e:
            yield json.dumps({"event": "error", "detail": str(e)}) + "\n"
            return
        yield json.dumps({"event": "result", **run_payload(result)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return audit.recent_runs(limit)


@lru_cache(maxsize=1)
def get_storage() -> CloudStorage:
    return CloudStorage()


@app.get("/api/artifacts")
def artifacts(request: Request) -> list[dict]:
    principal_of(request)
    return browse(get_storage(), f"{BUCKET}-artifacts")


@lru_cache(maxsize=1)
def get_schedules() -> Schedules:
    return Schedules()


@app.get("/api/schedules")
def schedules(request: Request) -> list[dict]:
    principal_of(request)
    return get_schedules().list()


@app.post("/api/schedules")
def create_schedule(body: ScheduleCreate, request: Request) -> dict:
    principal = principal_of(request)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role}")
    try:
        job_id = get_schedules().create(body.role, body.name, body.cron, body.prompt, body.paused)
    except InvalidArgument as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    logger.info(f"schedule {job_id} created by {principal}")
    return {"id": job_id, **body.model_dump()}


def check_known_schedule(job_id: str) -> None:
    if not job_id.startswith(PREFIX):
        raise HTTPException(status_code=404, detail=f"unknown schedule: {job_id}")


@app.put("/api/schedules/{job_id}")
def update_schedule(job_id: str, body: ScheduleUpdate, request: Request) -> dict:
    principal = principal_of(request)
    check_known_schedule(job_id)
    try:
        get_schedules().update(job_id, body.name, body.cron, body.prompt, body.paused)
    except InvalidArgument as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    logger.info(f"schedule {job_id} updated by {principal}")
    return {"id": job_id, **body.model_dump()}


@app.post("/api/schedules/{job_id}/run")
def run_schedule(job_id: str, request: Request) -> dict:
    principal = principal_of(request)
    check_known_schedule(job_id)
    try:
        get_schedules().run(job_id)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    logger.info(f"schedule {job_id} triggered by {principal}")
    return {"id": job_id}


@app.delete("/api/schedules/{job_id}")
def delete_schedule(job_id: str, request: Request) -> dict:
    principal = principal_of(request)
    check_known_schedule(job_id)
    try:
        get_schedules().delete(job_id)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    logger.info(f"schedule {job_id} deleted by {principal}")
    return {"id": job_id}


static = ROOT / "frontend" / "out"
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True))
