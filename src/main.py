import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from claude_agent_sdk import ClaudeAgentOptions

from src.agent import run_agent
from src.bq import BigQuery
from src.gcs import CloudStorage

WS = Path(__file__).parent.parent / "ws"
BUCKET = os.environ["BUCKET"]

logger = logging.getLogger(__name__)


def sync_skills() -> None:
    count = CloudStorage().download_prefix(BUCKET, "skills/", WS / ".claude" / "skills")
    logger.info(f"synced {count} skill files from gs://{BUCKET}/skills")


def build_options() -> ClaudeAgentOptions:
    bq = BigQuery()
    cs = CloudStorage()
    return ClaudeAgentOptions(
        cwd=str(WS),
        setting_sources=["project"],
        mcp_servers={"bq": bq.mcp(), "gcs": cs.mcp()},
        thinking={"type": "adaptive", "display": "summarized"},
        permission_mode="bypassPermissions",
        max_turns=20,
    )


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    args = parser.parse_args()

    sync_skills()
    await run_agent(args.prompt, build_options(), echo=True)


if __name__ == "__main__":
    asyncio.run(main())
