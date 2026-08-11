import functools
import logging

from google.cloud import run_v2

from . import config

log = logging.getLogger(__name__)


@functools.cache
def _jobs() -> run_v2.JobsAsyncClient:
    return run_v2.JobsAsyncClient()


async def launch(job_name: str, session_id: str) -> str:
    """Start one Cloud Run Job execution to process a session's queued events."""
    name = f"projects/{config.PROJECT_ID}/locations/{config.REGION}/jobs/{job_name}"
    request = run_v2.RunJobRequest(
        name=name,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(args=["--session", session_id])
            ]
        ),
    )
    operation = await _jobs().run_job(request=request)
    execution = operation.metadata.name if operation.metadata else ""
    log.info("launched sandbox job=%s session=%s execution=%s", job_name, session_id, execution)
    return execution
