import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime

from naxos_shared.events import EventType, StopReason

from .config import IDLE_LINGER_SECONDS
from .control import ControlChannel
from .harness import CONTINUE_PROMPT, Harness
from .memory_sync import MemorySync
from .workspace import Workspace

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30.0
QUEUE_WAIT_SECONDS = 25


def _prompts(events: list[dict]) -> tuple[list[str], bool]:
    """Turn queued client events into prompts. Returns (prompts, interrupted)."""
    prompts: list[str] = []
    interrupted = False
    for event in events:
        type_ = event["type"]
        payload = event.get("payload") or {}
        if type_ == str(EventType.USER_MESSAGE):
            text = "\n".join(b.get("text", "") for b in payload.get("content", []))
            if text.strip():
                prompts.append(text)
        elif type_ == str(EventType.USER_INTERRUPT):
            interrupted = True
        elif type_ == str(EventType.USER_TOOL_CONFIRMATION):
            prompts.append(CONTINUE_PROMPT)
        elif type_ == str(EventType.USER_CUSTOM_TOOL_RESULT):
            prompts.append(f"Tool result: {payload.get('content')}")
    return prompts, interrupted


async def _watch_queue(channel: ControlChannel, harness, carry: list[dict]) -> None:
    """While a turn runs, pull the queue so an interrupt lands immediately.

    Non-interrupt events claimed here are carried over to the next loop
    iteration instead of being lost.
    """
    while True:
        try:
            batch = await channel.poll_queue(QUEUE_WAIT_SECONDS)
        except Exception:
            log.exception("queue watcher poll failed")
            await asyncio.sleep(5)
            continue
        if batch.get("control") in ("kill", "terminate"):
            await harness.interrupt()
            return
        for event in batch.get("events", []):
            if event["type"] == str(EventType.USER_INTERRUPT):
                await harness.interrupt()
            else:
                carry.append(event)


async def _heartbeat(channel: ControlChannel, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            alive = await channel.heartbeat()
            if not alive:
                stop.set()
                return
        except Exception:
            log.exception("heartbeat failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            continue


async def run_session(session_id: str) -> None:
    channel = ControlChannel(session_id)
    started_at = datetime.now(UTC).isoformat()
    stop = asyncio.Event()
    heartbeat_task: asyncio.Task | None = None
    workspace: Workspace | None = None
    memory: MemorySync | None = None
    stop_reason = StopReason.END_TURN
    harness: Harness | None = None
    try:
        await channel.claim()
        config = await channel.config()
        workspace = Workspace(session_id, config["session_bucket"])
        workspace.restore()
        memory = MemorySync(channel, workspace.ws)
        await memory.materialise()
        heartbeat_task = asyncio.create_task(_heartbeat(channel, stop))
        harness = Harness(channel, config, str(workspace.ws))

        idle_since: float | None = None
        carry: list[dict] = []
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            if carry:
                batch = {"control": None, "events": carry}
                carry = []
            else:
                batch = await channel.poll_queue(QUEUE_WAIT_SECONDS)
            if batch.get("control") in ("kill", "terminate"):
                stop_reason = StopReason.END_TURN
                break
            prompts, interrupted = _prompts(batch.get("events", []))
            if interrupted and not prompts:
                stop_reason = StopReason.END_TURN
                break
            if not prompts:
                idle_since = idle_since if idle_since is not None else loop.time()
                if loop.time() - idle_since >= IDLE_LINGER_SECONDS:
                    break
                continue
            idle_since = None
            harness.interrupted = False
            watcher = asyncio.create_task(_watch_queue(channel, harness, carry))
            try:
                stop_reason = await harness.run(prompts)
            finally:
                watcher.cancel()
            if stop_reason in (StopReason.REQUIRES_ACTION, StopReason.BUDGET_REACHED):
                break
    finally:
        stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if memory is not None:
            try:
                await memory.writeback()
            except Exception:
                log.exception("memory writeback failed")
        if workspace is not None:
            try:
                workspace.checkpoint()
            except Exception:
                log.exception("workspace checkpoint failed")
        try:
            await channel.checkpoint(
                sdk_session_id=harness.sdk_session_id if harness else None,
                cost_usd=harness.cost_usd if harness else None,
                stop_reason=str(stop_reason),
                run_id=harness.run_id if harness else None,
                started_at=started_at,
                num_turns=harness.num_turns if harness else 0,
            )
        except Exception:
            log.exception("control-plane checkpoint failed")
        await channel.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="naxos-sandbox")
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_session(args.session))


if __name__ == "__main__":
    main()
