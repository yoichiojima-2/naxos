"""Result helpers shared by the in-process MCP servers (artifacts, schedules)."""

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


def text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def error(message: str) -> dict[str, Any]:
    return {**text(message), "is_error": True}


def guarded(handler, noun: str):
    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await handler(args)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            return error(
                f"control plane rejected the request ({exc.response.status_code}): {detail}"
            )
        except Exception as exc:
            log.exception("%s tool failed", noun)
            return error(f"{noun} operation failed: {exc}")

    return wrapped
