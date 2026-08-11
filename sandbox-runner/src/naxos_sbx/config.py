import os
from pathlib import Path

INTERNAL_URL = os.environ.get("INTERNAL_URL", "http://localhost:8001")
DEV_SA = os.environ.get("NAXOS_DEV_SA", "")
WORKDIR = Path(os.environ.get("NAXOS_WORKDIR", "/workspace"))
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/workspace/.claude"))
IDLE_LINGER_SECONDS = float(os.environ.get("IDLE_LINGER_SECONDS", "120"))
MAX_ARTIFACT_BYTES = int(os.environ.get("MAX_ARTIFACT_BYTES", str(10 * 1024 * 1024)))

GCLOUD_PROJECT_ID = os.environ.get("GCLOUD_PROJECT_ID", "")
# Set by Terraform from the environment's `bigquery_datasets` in
# environments.json — the same list that drives the IAM grants. Empty means the
# environment was never opted in, and the BigQuery tools are not registered.
BIGQUERY_DATASETS = [
    d.strip() for d in os.environ.get("BIGQUERY_DATASETS", "").split(",") if d.strip()
]
MAX_QUERY_BYTES_BILLED = int(os.environ.get("MAX_QUERY_BYTES_BILLED", str(1024**3)))
MAX_QUERY_ROWS = int(os.environ.get("MAX_QUERY_ROWS", "200"))
