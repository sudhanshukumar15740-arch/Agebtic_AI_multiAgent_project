"""Chat execution: run agent and stream SSE events."""

import asyncio
import json
from typing import AsyncIterator

from agent import Agent

SENTINEL_DONE = "_internal_done"
SENTINEL_ERROR = "_internal_error"

async def execute_chat(
    chat_agent: Agent,
    user_message: str,
    prev_conv: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Async SSE generator — runs the agent and streams terminal events."""
    prev_conv = list(prev_conv or [])
    event_queue: asyncio.Queue = asyncio.Queue()


    async def _run() -> None:
        try:
            parts: list[str] = []
            async for event in chat_agent.run(
                user_message,
                prev_conv,
            ):
                if event.get("type") == "delta":
                    parts.append(event.get("text") or "")
                await event_queue.put(event)
            await event_queue.put({
                "type": SENTINEL_DONE,
                "text": "".join(parts),
            })
        except asyncio.CancelledError:
            await event_queue.put({"type": "cancelled"})
            raise
        except Exception as exc:
            await event_queue.put({
                "type": SENTINEL_ERROR,
                "message": str(exc),
            })

    run_task = asyncio.create_task(_run())

    yield _sse({"type": "ready"})

    try:
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                if run_task.done() and event_queue.empty():
                    break
                continue

            etype = event.get("type")
            if etype == "delta":
                yield _sse({"type": "delta", "text": event.get("text") or ""})
                continue

            if etype == "text":
                yield _sse({"type": "text", "text": event.get("text") or ""})
                continue

            if etype == "tool_call":
                yield _sse({
                    "type": "tool_call",
                    "call_id": event.get("call_id"),
                    "name": event.get("name") or "",
                    "args": event.get("args") or {},
                })
                continue

            if etype == SENTINEL_DONE:
                yield _sse({"type": "done", "text": event.get("text") or ""})
                break

            if etype == SENTINEL_ERROR:
                yield _sse({
                    "type": "error",
                    "message": event.get("message", "unknown error"),
                })
                break

            if etype == "cancelled":
                yield _sse({"type": "cancelled"})
                break
    except asyncio.CancelledError:
        yield _sse({"type": "cancelled"})
    finally:
        if not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except (asyncio.CancelledError, Exception):
                pass


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
