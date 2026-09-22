"""Responses-API streaming helpers."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, WebSocketException

from llm_sockets import Conn

_CONN_ERRORS = (
    BrokenPipeError,
    ConnectionError,
    OSError,
    ConnectionClosed,
    WebSocketException,
)
_TERMINAL_ERRORS = frozenset({"error", "response.failed", "response.incomplete"})


@dataclass
class LlmTurnResult:
    """One response.create turn drained until response.completed."""

    text: str
    response_id: str | None
    tool_calls: list[dict]


async def _send(conn: Conn, payload: dict) -> None:
    try:
        await conn.ws.send(json.dumps(payload))
    except _CONN_ERRORS as exc:
        raise RuntimeError("LLM websocket closed while sending") from exc


async def _recv(ws: ClientConnection) -> str | bytes:
    try:
        raw = await ws.recv()
    except _CONN_ERRORS as exc:
        raise RuntimeError("LLM websocket closed while receiving") from exc
    if not raw:
        raise RuntimeError("Empty WS frame — LLM link closed")
    return raw


async def stream_turn(
    conn: Conn,
    payload: dict,
) -> AsyncIterator[str | LlmTurnResult]:
    """Send response.create; yield text deltas, then LlmTurnResult."""
    await _send(conn, payload)
    ws = conn.ws

    text_parts: list[str] = []
    curr_calls: dict = {}

    while True:
        event = json.loads(await _recv(ws))
        etype = event.get("type")

        if etype in _TERMINAL_ERRORS:
            raise RuntimeError(event)

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id")
                curr_calls[item_id] = {
                    "call_id": item.get("call_id"),
                    "name": item.get("name"),
                    "args": "",
                    "done": False,
                }

        elif etype == "response.function_call_arguments.delta":
            call = curr_calls.get(event.get("item_id"))
            if call is not None:
                call["args"] += event.get("delta", "")

        elif etype == "response.function_call_arguments.done":
            call = curr_calls.get(event.get("item_id"))
            if call is not None:
                args = event.get("arguments") or call["args"]
                if isinstance(args, str):
                    try:
                        call["args"] = json.loads(args) if args else {}
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Invalid tool call arguments JSON: {args!r}"
                        ) from exc
                else:
                    call["args"] = args if args is not None else {}
                call["done"] = True

        elif etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                text_parts.append(delta)
                yield delta

        elif etype == "response.completed":
            response = event.get("response") or {}
            yield LlmTurnResult(
                text="".join(text_parts),
                response_id=response.get("id"),
                tool_calls=[
                    {
                        "call_id": c["call_id"],
                        "name": c["name"],
                        "args": c["args"] if isinstance(c["args"], dict) else {},
                    }
                    for c in curr_calls.values()
                    if c["done"]
                ],
            )
            return
