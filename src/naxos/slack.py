import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)


def notify(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.info("slack: SLACK_WEBHOOK_URL not set, skipping")
        return
    request = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            logger.info(f"slack: notified ({response.status})")
    except Exception as e:
        logger.error(f"slack: notification failed: {e}")
