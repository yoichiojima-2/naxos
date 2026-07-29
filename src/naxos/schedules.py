import json
import logging
import os
import uuid
from dataclasses import dataclass, field

import google.auth
from google.cloud import scheduler_v1

from naxos import mcp

logger = logging.getLogger(__name__)

REGION = "asia-northeast1"
PREFIX = "naxos-schedule-"
RUNNER = "naxos-runner-"


def run_body(prompt: str) -> bytes:
    return json.dumps({"overrides": {"containerOverrides": [{"args": [prompt]}]}}).encode()


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
            job_id = job.name.rsplit("/", 1)[-1]
            if not job_id.startswith(PREFIX):
                continue
            body = json.loads(job.http_target.body) if job.http_target.body else {}
            args = (body.get("overrides", {}).get("containerOverrides") or [{}])[0].get("args", [])
            role = job.http_target.uri.rsplit(f"/jobs/{RUNNER}", 1)[-1].removesuffix(":run")
            paused = job.state == scheduler_v1.Job.State.PAUSED
            out.append(
                {
                    "id": job_id,
                    "name": job.description or role,
                    "role": role,
                    "cron": job.schedule,
                    "prompt": args[-1] if args else "",
                    "paused": paused,
                    "next_run": job.schedule_time.isoformat() if not paused and job.schedule_time.timestamp() > 0 else None,
                }
            )
        return sorted(out, key=lambda s: (s["role"], s["name"]))

    def create(self, role: str, name: str, cron: str, prompt: str, paused: bool) -> str:
        job_id = f"{PREFIX}{uuid.uuid4().hex[:8]}"
        job = scheduler_v1.Job(
            name=f"{self.parent}/jobs/{job_id}",
            description=name,
            schedule=cron,
            time_zone="Asia/Tokyo",
            http_target=scheduler_v1.HttpTarget(
                http_method=scheduler_v1.HttpMethod.POST,
                uri=f"https://run.googleapis.com/v2/{self.parent}/jobs/{RUNNER}{role}:run",
                headers={"Content-Type": "application/json"},
                body=run_body(prompt),
                oauth_token=scheduler_v1.OAuthToken(
                    service_account_email=f"sa-scheduler@{self.project}.iam.gserviceaccount.com"
                ),
            ),
        )
        self.client.create_job(parent=self.parent, job=job)
        if paused:
            self.client.pause_job(name=job.name)
        logger.info(f"schedule created: {job_id} role={role} cron={cron!r} paused={paused}")
        return job_id

    def update(self, job_id: str, name: str, cron: str, prompt: str, paused: bool) -> None:
        job_name = f"{self.parent}/jobs/{job_id}"
        job = scheduler_v1.Job(
            name=job_name, description=name, schedule=cron, http_target=scheduler_v1.HttpTarget(body=run_body(prompt))
        )
        updated = self.client.update_job(job=job, update_mask={"paths": ["description", "schedule", "http_target.body"]})
        if paused != (updated.state == scheduler_v1.Job.State.PAUSED):
            self.client.pause_job(name=job_name) if paused else self.client.resume_job(name=job_name)
        logger.info(f"schedule updated: {job_id} cron={cron!r} paused={paused}")

    def run(self, job_id: str) -> None:
        self.client.run_job(name=f"{self.parent}/jobs/{job_id}")
        logger.info(f"schedule triggered: {job_id}")

    def delete(self, job_id: str) -> None:
        self.client.delete_job(name=f"{self.parent}/jobs/{job_id}")
        logger.info(f"schedule deleted: {job_id}")


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
        "form, and only the user can save it. name is a short human-readable "
        "title for the task. cron is a standard 5-field cron expression "
        "evaluated in Asia/Tokyo.",
        {"role": str, "name": str, "cron": str, "prompt": str},
    )
    async def propose_schedule(args):
        return mcp.result(f"Proposed to the user for review: role={args['role']} cron={args['cron']}. Not saved yet.")

    return mcp.server("schedules", [propose_schedule])
