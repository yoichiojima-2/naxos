import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres@/naxos?host=/tmp&port=55432")

from naxos_cp import api, db, internal, wake


@pytest_asyncio.fixture
async def pool():
    pool = await db.connect()
    async with pool.acquire() as conn:
        await db.migrate(conn)
        await conn.execute(
            "TRUNCATE session_events, tool_confirmations, sessions, agent_versions, "
            "agents, environments, deployment_runs, deployments RESTART IDENTITY CASCADE"
        )
    yield pool
    await db.disconnect()


@pytest_asyncio.fixture
async def client(pool):
    transport = ASGITransport(app=api.create_app_without_lifespan())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def internal_client(pool):
    transport = ASGITransport(app=internal.create_app_without_lifespan())
    async with AsyncClient(transport=transport, base_url="http://internal") as c:
        yield c


@pytest.fixture
def launched(monkeypatch):
    """Record sandbox launches instead of calling Cloud Run."""
    calls: list[tuple[str, str]] = []

    async def fake_launch(job_name: str, session_id: str) -> str:
        calls.append((job_name, session_id))
        return f"exec-{len(calls)}"

    monkeypatch.setattr(wake.sandbox, "launch", fake_launch)
    return calls
