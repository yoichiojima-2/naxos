import json
from types import SimpleNamespace

import httpx
import pytest

from naxos_sbx.schedules import ScheduleTools


class _Channel:
    def __init__(self):
        self.session_id = "session_x"
        self.created = []
        self.archived = []

    async def create_deployment(self, name, cron, prompt, timezone, budget_usd):
        self.created.append((name, cron, prompt, timezone, budget_usd))
        return {"id": "depl_1", "name": name, "cron": cron, "timezone": timezone}

    async def list_deployments(self):
        return {
            "data": [
                {
                    "id": "depl_1",
                    "name": "daily-digest",
                    "cron": "3 8 * * *",
                    "timezone": "Asia/Tokyo",
                    "prompt": "Compile the digest.",
                    "paused": False,
                    "budget_usd": None,
                    "created_by": "agent:session_x",
                }
            ]
        }

    async def archive_deployment(self, deployment_id):
        self.archived.append(deployment_id)
        return {"id": deployment_id, "archived": True}


@pytest.fixture
def tools():
    return ScheduleTools(_Channel())


async def test_create_defaults_timezone_and_reports_semantics(tools):
    result = await tools.create(
        {"name": "daily-digest", "cron": "3 8 * * *", "prompt": "Compile the digest."}
    )
    text = result["content"][0]["text"]
    assert "depl_1" in text
    assert "fresh session" in text
    assert tools.channel.created == [
        ("daily-digest", "3 8 * * *", "Compile the digest.", "Asia/Tokyo", None)
    ]


async def test_create_passes_timezone_and_budget(tools):
    await tools.create(
        {
            "name": "digest",
            "cron": "0 8 * * *",
            "prompt": "p",
            "timezone": "Europe/Berlin",
            "budget_usd": 2.5,
        }
    )
    assert tools.channel.created[0][3:] == ("Europe/Berlin", 2.5)


async def test_list_returns_deployments(tools):
    result = await tools.list({})
    rows = json.loads(result["content"][0]["text"])
    assert rows[0]["id"] == "depl_1"
    assert rows[0]["prompt"] == "Compile the digest."


async def test_delete_archives_by_id(tools):
    result = await tools.delete({"deployment_id": "depl_1"})
    assert "Archived deployment depl_1" in result["content"][0]["text"]
    assert tools.channel.archived == ["depl_1"]


async def test_control_plane_rejection_surfaces_as_tool_error():
    from naxos_sbx.mcp_result import guarded

    async def refuse(args):
        raise httpx.HTTPStatusError(
            "denied",
            request=httpx.Request("POST", "http://internal"),
            response=httpx.Response(403, text="operator-created deployment"),
        )

    result = await guarded(refuse, "schedule")({})
    assert result["is_error"]
    assert "403" in result["content"][0]["text"]


def test_harness_exposes_schedule_server_and_blocks_cli_cron_tools():
    from naxos_shared.events import SessionConfig

    from naxos_sbx.harness import SESSION_LOCAL_SCHEDULER_TOOLS, Harness

    config = SessionConfig.model_validate(
        {
            "session_id": "session_x",
            "agent_id": "agent_x",
            "agent_version": 1,
            "environment_id": "env_x",
            "model": "claude-sonnet-5",
            "session_bucket": "bucket-x",
        }
    )
    options = Harness(SimpleNamespace(session_id="session_x"), config, "/tmp").options()
    assert "schedules" in options.mcp_servers
    assert options.disallowed_tools == SESSION_LOCAL_SCHEDULER_TOOLS
    assert "CronCreate" in options.disallowed_tools
