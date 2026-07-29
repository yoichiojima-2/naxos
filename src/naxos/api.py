import asyncio
import json
import logging
import os
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import InvalidArgument, NotFound
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel

from naxos import audit
from naxos.agent import AgentRun
from naxos.artifacts import browse
from naxos.config import ARTIFACTS_BUCKET, ROLES, ROOT, configure_logging
from naxos.gcs import CloudStorage
from naxos.runner import RoleDisabled, execute
from naxos.schedules import PREFIX, Schedules
from naxos.skills import Skills

logger = logging.getLogger(__name__)

configure_logging()
app = FastAPI()

IAP_AUDIENCE = os.environ.get("IAP_AUDIENCE")
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
PING_INTERVAL = 15.0


def error_handler(status: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handle


for exc_type, status in {
    ValueError: 400,
    InvalidArgument: 400,
    FileNotFoundError: 404,
    NotFound: 404,
    RoleDisabled: 409,
}.items():
    app.add_exception_handler(exc_type, error_handler(status))


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


class SkillFile(BaseModel):
    content: str


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


Principal = Annotated[str, Depends(principal_of)]
authed = [Depends(principal_of)]


def check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {role}")


@app.get("/api/me")
def me(principal: Principal) -> dict:
    return {"email": principal}


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
async def run(body: RunRequest, principal: Principal) -> dict:
    check_role(body.role)
    result = await execute(body.prompt, body.role, body.resume, principal=principal, fresh_ws=True, keep_alive=True)
    return run_payload(result)


class EventStream:
    """Buffered event log for one run, so a dropped connection can re-attach
    and replay from where it left off. Instance-local, like the keep-alive
    clients — a lost instance means falling back to History."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.wakeup = asyncio.Event()

    def emit(self, event: dict) -> None:
        self.events.append(event)
        wake, self.wakeup = self.wakeup, asyncio.Event()
        wake.set()

    def finish(self, task: asyncio.Task) -> None:
        try:
            result = task.result()
        except (RoleDisabled, FileNotFoundError) as e:
            event = {"event": "error", "detail": str(e)}
        except Exception as e:
            logger.exception("run failed")
            event = {"event": "error", "detail": str(e)}
        else:
            event = {"event": "result", **run_payload(result)}
        self.done = True
        self.emit(event)


streams: dict[str, EventStream] = {}
MAX_STREAMS = 8


def evict_streams() -> None:
    finished = [stream_id for stream_id, stream in streams.items() if stream.done]
    while len(streams) > MAX_STREAMS and finished:
        del streams[finished.pop(0)]


async def follow(stream: EventStream, offset: int):
    while True:
        while offset < len(stream.events):
            yield json.dumps(stream.events[offset]) + "\n"
            offset += 1
        if stream.done:
            return
        wakeup = stream.wakeup
        if offset < len(stream.events):
            continue
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=PING_INTERVAL)
        except TimeoutError:
            # keep bytes flowing so idle mobile/NAT connections stay open
            yield json.dumps({"event": "ping"}) + "\n"


@app.post("/api/run/stream")
async def run_stream(body: RunRequest, principal: Principal) -> StreamingResponse:
    check_role(body.role)
    stream = EventStream()
    stream_id = uuid.uuid4().hex
    stream.emit({"event": "stream", "id": stream_id})
    task = asyncio.create_task(
        execute(
            body.prompt,
            body.role,
            body.resume,
            principal=principal,
            fresh_ws=True,
            on_event=lambda event: stream.emit(wire(event)),
            keep_alive=True,
        )
    )
    task.add_done_callback(stream.finish)
    streams[stream_id] = stream
    evict_streams()
    return StreamingResponse(follow(stream, 0), media_type="application/x-ndjson")


@app.get("/api/run/stream/{stream_id}", dependencies=authed)
def reattach_stream(stream_id: str, offset: int = Query(0, ge=0)) -> StreamingResponse:
    stream = streams.get(stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail=f"unknown stream: {stream_id}")
    return StreamingResponse(follow(stream, offset), media_type="application/x-ndjson")


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return audit.recent_runs(limit)


@lru_cache(maxsize=1)
def get_storage() -> CloudStorage:
    return CloudStorage()


@app.get("/api/artifacts", dependencies=authed)
def artifacts() -> list[dict]:
    return browse(get_storage(), ARTIFACTS_BUCKET)


@app.get("/artifacts/{path:path}", dependencies=authed)
def artifact_file(path: str) -> Response:
    data, content_type = get_storage().read_bytes(ARTIFACTS_BUCKET, path)
    # published artifacts are immutable, so long-lived caching is safe
    return Response(
        content=data,
        media_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


@lru_cache(maxsize=1)
def get_skills() -> Skills:
    return Skills(get_storage())


@app.get("/api/skills", dependencies=authed)
def skills() -> list[dict]:
    return get_skills().list()


@app.get("/api/skills/{name}/files/{path:path}", dependencies=authed)
def skill_file(name: str, path: str) -> dict:
    return {"content": get_skills().read(name, path)}


@app.put("/api/skills/{name}/files/{path:path}")
def save_skill_file(name: str, path: str, body: SkillFile, principal: Principal) -> dict:
    get_skills().write(name, path, body.content)
    logger.info(f"skill file {name}/{path} saved by {principal}")
    return {"name": name, "path": path}


@app.delete("/api/skills/{name}/files/{path:path}")
def delete_skill_file(name: str, path: str, principal: Principal) -> dict:
    get_skills().delete_file(name, path)
    logger.info(f"skill file {name}/{path} deleted by {principal}")
    return {"name": name, "path": path}


@app.delete("/api/skills/{name}")
def delete_skill(name: str, principal: Principal) -> dict:
    get_skills().delete(name)
    logger.info(f"skill {name} deleted by {principal}")
    return {"name": name}


@lru_cache(maxsize=1)
def get_schedules() -> Schedules:
    return Schedules()


@app.get("/api/schedules", dependencies=authed)
def schedules() -> list[dict]:
    return get_schedules().list()


@app.post("/api/schedules")
def create_schedule(body: ScheduleCreate, principal: Principal) -> dict:
    check_role(body.role)
    job_id = get_schedules().create(body.role, body.name, body.cron, body.prompt, body.paused)
    logger.info(f"schedule {job_id} created by {principal}")
    return {"id": job_id, **body.model_dump()}


def check_known_schedule(job_id: str) -> None:
    if not job_id.startswith(PREFIX):
        raise HTTPException(status_code=404, detail=f"unknown schedule: {job_id}")


@app.put("/api/schedules/{job_id}")
def update_schedule(job_id: str, body: ScheduleUpdate, principal: Principal) -> dict:
    check_known_schedule(job_id)
    get_schedules().update(job_id, body.name, body.cron, body.prompt, body.paused)
    logger.info(f"schedule {job_id} updated by {principal}")
    return {"id": job_id, **body.model_dump()}


@app.post("/api/schedules/{job_id}/run")
def run_schedule(job_id: str, principal: Principal) -> dict:
    check_known_schedule(job_id)
    get_schedules().run(job_id)
    logger.info(f"schedule {job_id} triggered by {principal}")
    return {"id": job_id}


@app.delete("/api/schedules/{job_id}")
def delete_schedule(job_id: str, principal: Principal) -> dict:
    check_known_schedule(job_id)
    get_schedules().delete(job_id)
    logger.info(f"schedule {job_id} deleted by {principal}")
    return {"id": job_id}


static = ROOT / "frontend" / "out"
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True))
