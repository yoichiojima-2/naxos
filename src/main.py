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
    parser.add_argument("--role", default="ops", choices=ROLES)
    return parser.parse_args()


def sync_skills(skills: list[str]) -> None:
    dest = WS / ".claude" / "skills"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    cs = CloudStorage()
    count = sum(cs.download_prefix(BUCKET, f"skills/{name}/", dest / name) for name in skills)
    logger.info(f"synced {count} skill files from gs://{BUCKET}/skills for {skills}")


def build_options(role: str) -> ClaudeAgentOptions:
    config = ROLES[role]
    servers = {}
    if "bq" in config["servers"]:
        servers["bq"] = BigQuery().mcp()
    if "gcs" in config["servers"]:
        servers["gcs"] = CloudStorage().mcp()
    return ClaudeAgentOptions(
        cwd=str(WS),
        setting_sources=["project"],
        mcp_servers=servers,
        thinking={"type": "adaptive", "display": "summarized"},
        permission_mode="bypassPermissions",
        max_turns=20,
    )


async def main() -> None:
    configure_logging()
    args = parse_args()

    sync_skills(ROLES[args.role]["skills"])
    started_at = datetime.now(UTC)
    run = await run_agent(args.prompt, build_options(args.role), echo=True)
    log_run(args.prompt, run, started_at)


if __name__ == "__main__":
    asyncio.run(main())
