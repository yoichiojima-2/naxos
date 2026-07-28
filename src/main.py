import argparse
import asyncio
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from claude_agent_sdk import ClaudeAgentOptions

from src import slack
from src.agent import AgentRun, run_agent
from src.artifacts import Artifacts
from src.audit import log_run
from src.bq import BigQuery
from src.gcs import CloudStorage

ROOT = Path(__file__).parent.parent
WS = ROOT / "ws"
SESSION_DIR = Path.home() / ".claude" / "projects" / str(WS).replace("/", "-")
ROLES = json.loads((ROOT / "roles.json").read_text())
BUCKET = os.environ["BUCKET"]

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--role", default=os.environ.get("ROLE", "ops"), choices=ROLES)
    parser.add_argument("--resume", help="session_id of a previous run to continue")
    return parser.parse_args()


def sync_skills(cs: CloudStorage, skills: list[str]) -> None:
    dest = WS / ".claude" / "skills"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    count = sum(cs.download_prefix(BUCKET, f"skills/{name}/", dest / name) for name in skills)
    logger.info(f"synced {count} skill files from gs://{BUCKET}/skills for {skills}")


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


def is_disabled(cs: CloudStorage, role: str) -> bool:
    return cs.exists(BUCKET, f"disabled/{role}")


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


def slack_message(role: str, run: AgentRun) -> str:
    text = run.text[:3000] + ("…" if len(run.text) > 3000 else "")
    return f"[{role}] {text}\n---\ncost ${run.cost_usd or 0:.4f} · session {run.session_id}"


async def main() -> None:
    configure_logging()
    args = parse_args()
    cs = CloudStorage()

    if is_disabled(cs, args.role):
        logger.warning(f"role {args.role} is disabled (gs://{BUCKET}/disabled/{args.role} exists), aborting")
        return

    sync_skills(cs, ROLES[args.role]["skills"])
    if args.resume:
        restore_session(cs, args.role, args.resume)
    started_at = datetime.now(UTC)
    run = await run_agent(args.prompt, build_options(args.role, cs, args.resume), echo=True)
    log_run(args.prompt, run, started_at)
    if args.resume and run.session_id != args.resume:
        logger.error(f"resume failed: expected session {args.resume}, got {run.session_id}")
    if run.session_id:
        save_session(cs, args.role, run.session_id)
    if ROLES[args.role].get("notify"):
        slack.notify(slack_message(args.role, run))


if __name__ == "__main__":
    asyncio.run(main())
