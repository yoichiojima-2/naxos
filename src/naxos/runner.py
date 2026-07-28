import asyncio
import logging
import shutil
from datetime import UTC, datetime

from claude_agent_sdk import ClaudeAgentOptions

from naxos.agent import AgentRun, run_agent
from naxos.artifacts import Artifacts
from naxos.audit import log_run
from naxos.bq import BigQuery
from naxos.config import BUCKET, ROLES, SESSION_DIR, WS
from naxos.gcs import CloudStorage

logger = logging.getLogger(__name__)


class RoleDisabled(Exception):
    pass


def is_disabled(cs: CloudStorage, role: str) -> bool:
    return cs.exists(BUCKET, f"disabled/{role}")


def clear_ws() -> None:
    WS.mkdir(exist_ok=True)
    for path in WS.iterdir():
        if path.name == ".claude":
            continue
        shutil.rmtree(path) if path.is_dir() else path.unlink()


def sync_skills(cs: CloudStorage, skills: list[str]) -> None:
    dest = WS / ".claude" / "skills"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    count = sum(cs.download_prefix(BUCKET, f"skills/{name}/", dest / name) for name in skills)
    logger.info(f"synced {count} skill files from gs://{BUCKET}/skills for {skills}")


def restore_session(cs: CloudStorage, role: str, session_id: str) -> None:
    bucket = f"{BUCKET}-sessions-{role}"
    target = SESSION_DIR / f"{session_id}.jsonl"
    if not target.exists():
        cs.download_file(bucket, f"{session_id}/transcript.jsonl", target)
        logger.info(f"session restored: gs://{bucket}/{session_id}/transcript.jsonl")
    count = cs.download_prefix(bucket, f"{session_id}/ws/", WS)
    if count:
        logger.info(f"restored {count} workspace files")


def save_session(cs: CloudStorage, role: str, session_id: str) -> None:
    bucket = f"{BUCKET}-sessions-{role}"
    source = SESSION_DIR / f"{session_id}.jsonl"
    if source.exists():
        cs.upload_file(bucket, f"{session_id}/transcript.jsonl", source)
    else:
        logger.error(f"session file not found, transcript lost: {source}")
    count = 0
    for file in WS.rglob("*"):
        if file.is_file() and ".claude" not in file.parts:
            cs.upload_file(bucket, f"{session_id}/ws/{file.relative_to(WS)}", file)
            count += 1
    logger.info(f"session saved: gs://{bucket}/{session_id}/ ({count} workspace files)")


def build_options(role: str, cs: CloudStorage, resume: str | None = None) -> ClaudeAgentOptions:
    config = ROLES[role]
    servers = {}
    if "bq" in config["servers"]:
        servers["bq"] = BigQuery().mcp()
    if "gcs" in config["servers"]:
        servers["gcs"] = cs.mcp()
    if "artifacts" in config["servers"]:
        servers["artifacts"] = Artifacts(role, f"{BUCKET}-artifacts", WS, cs).mcp()
    return ClaudeAgentOptions(
        cwd=str(WS),
        setting_sources=["project"],
        mcp_servers=servers,
        thinking={"type": "adaptive", "display": "summarized"},
        permission_mode=config["permission_mode"],
        max_turns=config["max_turns"],
        resume=resume,
    )


async def execute(
    prompt: str,
    role: str,
    resume: str | None = None,
    principal: str | None = None,
    trigger: str = "interactive",
    echo: bool = False,
    fresh_ws: bool = False,
) -> AgentRun:
    cs = CloudStorage()

    def prepare() -> None:
        if is_disabled(cs, role):
            raise RoleDisabled(f"role {role} is disabled (gs://{BUCKET}/disabled/{role} exists)")
        if fresh_ws:
            clear_ws()
        sync_skills(cs, ROLES[role]["skills"])
        if resume:
            restore_session(cs, role, resume)

    await asyncio.to_thread(prepare)
    started_at = datetime.now(UTC)
    run = await run_agent(prompt, build_options(role, cs, resume), echo=echo)
    if resume and run.session_id != resume:
        logger.error(f"resume failed: expected session {resume}, got {run.session_id}")

    def persist() -> None:
        log_run(prompt, run, started_at, role, principal, trigger)
        if run.session_id:
            save_session(cs, role, run.session_id)

    await asyncio.to_thread(persist)
    return run
