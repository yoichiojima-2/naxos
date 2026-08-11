import os

PROJECT_ID = os.environ.get("GCLOUD_PROJECT_ID", "")
REGION = os.environ.get("REGION", "asia-northeast1")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/naxos")

DEV_MODE = os.environ.get("NAXOS_DEV_MODE", "") == "1"
IAP_AUDIENCE = os.environ.get("IAP_AUDIENCE", "")
ENFORCE_CALLER_AUTH = os.environ.get("ENFORCE_CALLER_AUTH", "") == "1"
DEV_PRINCIPAL = os.environ.get("DEV_PRINCIPAL", "local-dev")

AUDIT_DATASET = os.environ.get("AUDIT_DATASET", "audit")
INTERNAL_URL = os.environ.get("INTERNAL_URL", "")
SCHEDULER_SA = os.environ.get(
    "SCHEDULER_SA", f"sa-scheduler@{PROJECT_ID}.iam.gserviceaccount.com" if PROJECT_ID else ""
)
EGRESS_SA = os.environ.get(
    "EGRESS_SA", f"sa-egress@{PROJECT_ID}.iam.gserviceaccount.com" if PROJECT_ID else ""
)
EGRESS_URL = os.environ.get("EGRESS_URL", "")

MAX_CONCURRENT_SANDBOXES = int(os.environ.get("MAX_CONCURRENT_SANDBOXES", "5"))
MAX_WAKE_RETRIES = int(os.environ.get("MAX_WAKE_RETRIES", "3"))
LEASE_TTL_SECONDS = int(os.environ.get("LEASE_TTL_SECONDS", "90"))
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_MEMORY_BYTES = 64 * 1024
SSE_POLL_SECONDS = 1.0
SSE_PING_SECONDS = 15.0
