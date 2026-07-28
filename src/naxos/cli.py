import argparse
import asyncio
import logging
import os

from naxos import slack
from naxos.agent import AgentRun
from naxos.config import ROLES, configure_logging
from naxos.runner import RoleDisabled, execute

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--role", default=os.environ.get("ROLE", "ops"), choices=ROLES)
    parser.add_argument("--resume", help="session_id of a previous run to continue")
    return parser.parse_args()


def slack_message(role: str, run: AgentRun) -> str:
    text = run.text[:3000] + ("…" if len(run.text) > 3000 else "")
    return f"[{role}] {text}\n---\ncost ${run.cost_usd or 0:.4f} · session {run.session_id}"


async def main() -> None:
    configure_logging()
    args = parse_args()

    try:
        run = await execute(args.prompt, args.role, args.resume, trigger="job", echo=True)
    except RoleDisabled as e:
        logger.warning(f"{e}, aborting")
        return

    if ROLES[args.role].get("notify"):
        slack.notify(slack_message(args.role, run))


if __name__ == "__main__":
    asyncio.run(main())
