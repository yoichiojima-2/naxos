import os
from pathlib import Path

INTERNAL_URL = os.environ.get("INTERNAL_URL", "http://localhost:8001")
DEV_SA = os.environ.get("NAXOS_DEV_SA", "")
WORKDIR = Path(os.environ.get("NAXOS_WORKDIR", "/workspace"))
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/workspace/.claude"))
IDLE_LINGER_SECONDS = float(os.environ.get("IDLE_LINGER_SECONDS", "120"))
