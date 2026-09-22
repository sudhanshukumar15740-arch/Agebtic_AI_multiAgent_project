"""HTTP proxy to AgentsExecution (/chat SSE, /health)."""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

AGENTS_URL = os.environ.get("AGENTS_URL", "http://127.0.0.1:6000").rstrip("/")


async def agents_health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{AGENTS_URL}/health")
            resp.raise_for_status()
            return {"ok": True, "status": resp.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def iter_chat_events(
    user_message: str,
    prev_conv: list[dict] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Parse AgentsExecution SSE into JSON event dicts (for WebSocket clients)."""
    buffer = ""
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{AGENTS_URL}/chat",
            json={
                "user_message": user_message,
                "prev_conv": list(prev_conv or []),
            },
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                detail = body.decode("utf-8", errors="replace")
                yield {"type": "error", "message": detail}
                return

            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    for line in block.splitlines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            if not raw:
                                continue
                            try:
                                yield json.loads(raw)
                            except json.JSONDecodeError:
                                yield {"type": "error", "message": raw}
