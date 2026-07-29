import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parents[2]
WS = ROOT / "ws"
SESSION_DIR = Path.home() / ".claude" / "projects" / str(WS).replace("/", "-")
ROLES = json.loads((ROOT / "roles.json").read_text())
BUCKET = os.environ["BUCKET"]
ARTIFACTS_BUCKET = f"{BUCKET}-artifacts"


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
