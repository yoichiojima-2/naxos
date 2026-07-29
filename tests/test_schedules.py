import json
from datetime import UTC, datetime
from unittest.mock import Mock

from google.cloud import scheduler_v1

from naxos.schedules import Schedules


def scheduler_job(name, cron, prompt, role="ops", description="", paused=False, schedule_time=None):
    body = json.dumps({"overrides": {"containerOverrides": [{"args": [prompt]}]}}).encode()
    job = scheduler_v1.Job(
        name=f"projects/p/locations/asia-northeast1/jobs/{name}",
        description=description,
        schedule=cron,
        http_target=scheduler_v1.HttpTarget(
            uri=f"https://run.googleapis.com/v2/projects/p/locations/asia-northeast1/jobs/naxos-runner-{role}:run",
            body=body,
        ),
        state=scheduler_v1.Job.State.PAUSED if paused else scheduler_v1.Job.State.ENABLED,
    )
    if schedule_time:
        job.schedule_time = schedule_time
    return job


def test_list_parses_jobs_and_skips_foreign_ones():
    client = Mock()
    client.list_jobs.return_value = [
        scheduler_job(
            "naxos-schedule-abc123",
            "0 9 * * *",
            "check things",
            description="daily check",
            schedule_time=datetime(2026, 7, 30, 0, 0, tzinfo=UTC),
        ),
        scheduler_job("naxos-schedule-analyst", "0 8 * * 1-5", "report", role="analyst", paused=True),
        scheduler_job("unrelated-job", "* * * * *", "x"),
    ]

    schedules = Schedules(client=client, project="p").list()

    assert schedules == [
        {
            "id": "naxos-schedule-analyst",
            "name": "analyst",
            "role": "analyst",
            "cron": "0 8 * * 1-5",
            "prompt": "report",
            "paused": True,
            "next_run": None,
        },
        {
            "id": "naxos-schedule-abc123",
            "name": "daily check",
            "role": "ops",
            "cron": "0 9 * * *",
            "prompt": "check things",
            "paused": False,
            "next_run": "2026-07-30T00:00:00+00:00",
        },
    ]


def test_create_builds_runner_target():
    client = Mock()

    job_id = Schedules(client=client, project="p").create("ops", "daily check", "0 9 * * *", "check things", False)

    assert job_id.startswith("naxos-schedule-")
    job = client.create_job.call_args.kwargs["job"]
    assert job.name == f"projects/p/locations/asia-northeast1/jobs/{job_id}"
    assert job.description == "daily check"
    assert job.schedule == "0 9 * * *"
    assert job.time_zone == "Asia/Tokyo"
    assert (
        job.http_target.uri == "https://run.googleapis.com/v2/projects/p/locations/asia-northeast1/jobs/naxos-runner-ops:run"
    )
    assert json.loads(job.http_target.body) == {"overrides": {"containerOverrides": [{"args": ["check things"]}]}}
    assert job.http_target.oauth_token.service_account_email == "sa-scheduler@p.iam.gserviceaccount.com"
    client.pause_job.assert_not_called()


def test_create_paused_pauses_after_create():
    client = Mock()

    job_id = Schedules(client=client, project="p").create("ops", "n", "0 9 * * *", "p", True)

    client.pause_job.assert_called_once_with(name=f"projects/p/locations/asia-northeast1/jobs/{job_id}")


def test_update_patches_values_without_state_change():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-abc123", "*/30 * * * *", "new prompt")

    Schedules(client=client, project="p").update("naxos-schedule-abc123", "renamed", "*/30 * * * *", "new prompt", False)

    job = client.update_job.call_args.kwargs["job"]
    assert job.description == "renamed"
    assert job.schedule == "*/30 * * * *"
    assert json.loads(job.http_target.body) == {"overrides": {"containerOverrides": [{"args": ["new prompt"]}]}}
    assert client.update_job.call_args.kwargs["update_mask"] == {"paths": ["description", "schedule", "http_target.body"]}
    client.resume_job.assert_not_called()
    client.pause_job.assert_not_called()


def test_update_pauses_on_transition():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-abc123", "0 9 * * *", "p")

    Schedules(client=client, project="p").update("naxos-schedule-abc123", "n", "0 9 * * *", "p", True)

    client.pause_job.assert_called_once()
    client.resume_job.assert_not_called()


def test_update_resumes_on_transition():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-abc123", "0 9 * * *", "p", paused=True)

    Schedules(client=client, project="p").update("naxos-schedule-abc123", "n", "0 9 * * *", "p", False)

    client.resume_job.assert_called_once()
    client.pause_job.assert_not_called()


def test_run():
    client = Mock()

    Schedules(client=client, project="p").run("naxos-schedule-abc123")

    client.run_job.assert_called_once_with(name="projects/p/locations/asia-northeast1/jobs/naxos-schedule-abc123")


def test_delete():
    client = Mock()

    Schedules(client=client, project="p").delete("naxos-schedule-abc123")

    client.delete_job.assert_called_once_with(name="projects/p/locations/asia-northeast1/jobs/naxos-schedule-abc123")
