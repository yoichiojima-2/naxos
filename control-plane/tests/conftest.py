import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "postgresql://postgres@/naxos?host=/tmp&port=55432")
os.environ.setdefault("NAXOS_DEV_MODE", "1")

from naxos_cp import api, db, internal, wake

TABLES = (
    "favorites, session_runs, session_events, tool_confirmations, egress_routes, artifacts, "
    "sessions, "
    "agent_versions, "
    "agents, environments, deployment_runs, deployments, vault_credentials, vaults, "
    "memories, memory_stores, skill_files, skills"
)


@pytest_asyncio.fixture(scope="session")
async def pool():
    """One pool for the whole run: asyncpg connections are bound to their loop."""
    pool = await db.connect()
    async with pool.acquire() as conn:
        await db.migrate(conn)
    yield pool
    await db.disconnect()


@pytest_asyncio.fixture(autouse=True)
async def clean(pool):
    async with pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    yield


@pytest_asyncio.fixture
async def client(pool):
    transport = ASGITransport(app=api.create_app(manage_pool=False))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def internal_client(pool):
    transport = ASGITransport(app=internal.create_app(manage_pool=False))
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
