import json
import logging
import os
from dataclasses import dataclass, field

import google.auth
from google.cloud import scheduler_v1

from naxos import mcp

logger = logging.getLogger(__name__)

REGION = "asia-northeast1"
PREFIX = "naxos-schedule-"


@dataclass
class Schedules:
    client: scheduler_v1.CloudSchedulerClient = field(default_factory=scheduler_v1.CloudSchedulerClient)
    project: str | None = os.environ.get("GCLOUD_PROJECT_ID")

    def __post_init__(self):
        if self.project is None:
            _, self.project = google.auth.default()

    @property
    def parent(self) -> str:
        return f"projects/{self.project}/locations/{REGION}"

    def list(self) -> list[dict]:
        out = []
        for job in self.client.list_jobs(parent=self.parent):
            name = job.name.rsplit("/", 1)[-1]
            if not name.startswith(PREFIX):
                continue
            body = json.loads(job.http_target.body) if job.http_target.body else {}
            args = (body.get("overrides", {}).get("containerOverrides") or [{}])[0].get("args", [])
            out.append(
                {
                    "role": name.removeprefix(PREFIX),
                    "cron": job.schedule,
                    "prompt": args[-1] if args else "",
                    "paused": job.state == scheduler_v1.Job.State.PAUSED,
                }
            )
        return sorted(out, key=lambda s: s["role"])

    def update(self, role: str, cron: str, prompt: str, paused: bool) -> None:
        name = f"{self.parent}/jobs/{PREFIX}{role}"
        body = json.dumps({"overrides": {"containerOverrides": [{"args": [prompt]}]}}).encode()
        job = scheduler_v1.Job(name=name, schedule=cron, http_target=scheduler_v1.HttpTarget(body=body))
        updated = self.client.update_job(job=job, update_mask={"paths": ["schedule", "http_target.body"]})
        if paused != (updated.state == scheduler_v1.Job.State.PAUSED):
            self.client.pause_job(name=name) if paused else self.client.resume_job(name=name)
        logger.info(f"schedule updated: {role} cron={cron!r} paused={paused}")


def proposals_mcp():
    """Agent-facing schedule proposal tool: writes nothing.

    The tool call itself is the product — the UI catches it in the event
    stream and opens a prefilled form; only the user can save.
    """
    from claude_agent_sdk import tool

    @tool(
        "propose_schedule",
        "Propose a scheduled task for the user to review and save. This does "
        "not write anything: the proposal appears to the user as a prefilled "
        "form, and only the user can save it. cron is a standard 5-field cron "
        "expression evaluated in Asia/Tokyo.",
        {"role": str, "cron": str, "prompt": str},
    )
    async def propose_schedule(args):
        return mcp.result(f"Proposed to the user for review: role={args['role']} cron={args['cron']}. Not saved yet.")

    return mcp.server("schedules", [propose_schedule])
