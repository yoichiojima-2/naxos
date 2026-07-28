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
from src.agent import run_agent
from src.audit import log_run
from src.bq import BigQuery
from src.gcs import CloudStorage

ROOT = Path(__file__).parent.parent
WS = ROOT / "ws"
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


async def main() -> None:
    configure_logging()
    args = parse_args()
    cs = CloudStorage()

    if is_disabled(cs, args.role):
        logger.warning(f"role {args.role} is disabled (gs://{BUCKET}/disabled/{args.role} exists), aborting")
        return

    sync_skills(cs, ROLES[args.role]["skills"])
    started_at = datetime.now(UTC)
    run = await run_agent(args.prompt, build_options(args.role, cs, args.resume), echo=True)
    log_run(args.prompt, run, started_at)
    if ROLES[args.role].get("notify"):
        slack.notify(f"[{args.role}] {run.text}")


if __name__ == "__main__":
    asyncio.run(main())
