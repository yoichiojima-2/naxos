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


def build_options(tenant: str) -> ClaudeAgentOptions:
    bq = BigQuery(tenant=tenant)
    cs = CloudStorage(tenant=tenant)
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
    parser.add_argument("--tenant", default="default")
    args = parser.parse_args()

    await run_agent(args.prompt, build_options(args.tenant), echo=True)


if __name__ == "__main__":
    asyncio.run(main())
