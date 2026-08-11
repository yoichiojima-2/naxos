import logging
from typing import Any

import httpx

from .config import DEV_SA, INTERNAL_URL

log = logging.getLogger(__name__)


class ControlChannel:
    """Client for the control plane's internal surface.

    Authenticates with the sandbox service account's OIDC identity token; the
    control plane matches that identity against the session's environment.
    """

    def __init__(self, session_id: str, base_url: str = INTERNAL_URL) -> None:
        self.session_id = session_id
        self.base_url = base_url.rstrip("/")
        self.lease_id: str | None = None
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=70.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if DEV_SA:
            return {"x-naxos-dev-sa": DEV_SA}
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        token = id_token.fetch_id_token(Request(), self.base_url)
        return {"authorization": f"Bearer {token}"}

    async def _post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(
            f"{self.base_url}/internal/sessions/{self.session_id}{path}",
            json=json or {},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.get(
            f"{self.base_url}/internal/sessions/{self.session_id}{path}",
            params=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def claim(self) -> str:
        self.lease_id = (await self._post("/claim"))["lease_id"]
        return self.lease_id

    async def heartbeat(self) -> bool:
        result = await self._post("/heartbeat", {"lease_id": self.lease_id})
        return not result.get("disabled")

    async def config(self) -> dict[str, Any]:
        return await self._get("/config")

    async def poll_queue(self, wait: int = 25) -> dict[str, Any]:
        return await self._get("/queue", {"wait": wait})

    async def emit(self, events: list[dict[str, Any]], run_id: str) -> None:
        if events:
            await self._post("/events", {"events": events, "run_id": run_id})

    async def ask_permission(
        self, call_hash: str, tool_name: str, tool_input: dict[str, Any], tool_use_id: str | None
    ) -> dict[str, Any]:
        return await self._post(
            "/permission",
            {
                "call_hash": call_hash,
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
            },
        )

    async def checkpoint(
        self,
        sdk_session_id: str | None,
        cost_usd: float | None,
        stop_reason: str,
        terminated: bool = False,
        run_id: str | None = None,
        started_at: str | None = None,
        num_turns: int = 0,
    ) -> None:
        await self._post(
            "/checkpoint",
            {
                "lease_id": self.lease_id,
                "sdk_session_id": sdk_session_id,
                "cost_usd": cost_usd,
                "stop_reason": stop_reason,
                "terminated": terminated,
                "run_id": run_id,
                "started_at": started_at,
                "num_turns": num_turns,
            },
        )
