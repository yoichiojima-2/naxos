import json
from unittest.mock import Mock

from google.cloud import scheduler_v1

from naxos.schedules import Schedules


def scheduler_job(name, cron, prompt, paused=False):
    body = json.dumps({"overrides": {"containerOverrides": [{"args": [prompt]}]}}).encode()
    return scheduler_v1.Job(
        name=f"projects/p/locations/asia-northeast1/jobs/{name}",
        schedule=cron,
        http_target=scheduler_v1.HttpTarget(body=body),
        state=scheduler_v1.Job.State.PAUSED if paused else scheduler_v1.Job.State.ENABLED,
    )


def test_list_parses_jobs_and_skips_foreign_ones():
    client = Mock()
    client.list_jobs.return_value = [
        scheduler_job("naxos-schedule-ops", "0 9 * * *", "check things"),
        scheduler_job("naxos-schedule-analyst", "0 9 * * *", "placeholder", paused=True),
        scheduler_job("unrelated-job", "* * * * *", "x"),
    ]

    schedules = Schedules(client=client, project="p").list()

    assert schedules == [
        {"role": "analyst", "cron": "0 9 * * *", "prompt": "placeholder", "paused": True},
        {"role": "ops", "cron": "0 9 * * *", "prompt": "check things", "paused": False},
    ]


def test_update_patches_values_without_state_change():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-ops", "*/30 * * * *", "new prompt")

    Schedules(client=client, project="p").update("ops", "*/30 * * * *", "new prompt", paused=False)

    job = client.update_job.call_args.kwargs["job"]
    assert job.schedule == "*/30 * * * *"
    assert json.loads(job.http_target.body) == {"overrides": {"containerOverrides": [{"args": ["new prompt"]}]}}
    assert client.update_job.call_args.kwargs["update_mask"] == {"paths": ["schedule", "http_target.body"]}
    client.resume_job.assert_not_called()
    client.pause_job.assert_not_called()


def test_update_pauses_on_transition():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-ops", "0 9 * * *", "p")

    Schedules(client=client, project="p").update("ops", "0 9 * * *", "p", paused=True)

    client.pause_job.assert_called_once()
    client.resume_job.assert_not_called()


def test_update_resumes_on_transition():
    client = Mock()
    client.update_job.return_value = scheduler_job("naxos-schedule-ops", "0 9 * * *", "p", paused=True)

    Schedules(client=client, project="p").update("ops", "0 9 * * *", "p", paused=False)

    client.resume_job.assert_called_once()
    client.pause_job.assert_not_called()
