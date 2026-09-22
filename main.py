"""Operations API — users, auth, chats; proxies agent streaming to AgentsExecution."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles
from fastmcp.utilities.lifespan import combine_lifespans
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db as db_mod
import proxy
from MCP.mcp_server import mcp
from schemas import (
    ChatCreate,
    ChatOut,
    LoginRequest,
    LoginResponse,
    MsgOut,
    UserCreate,
    UserOut,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_mod.init_db()
    yield
    await db_mod.close_db()


mcp_app = mcp.http_app(path="/", stateless_http=True)
app = FastAPI(
    title="theta-operations",
    lifespan=combine_lifespans(lifespan, mcp_app.lifespan),
)
app.mount("/mcp", mcp_app)


@dataclass
class ChatRun:
    """The process-owned stream for one chat, independent of any browser socket."""

    task: asyncio.Task | None = None
    events: list[dict] = field(default_factory=list)
    subscribers: set[WebSocket] = field(default_factory=set)


_chat_runs: dict[str, ChatRun] = {}
_chat_runs_lock = asyncio.Lock()


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    return token


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(db_mod.get_session),
) -> db_mod.User:
    user = await auth.user_from_token(session, _bearer_token(authorization))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return user


async def _owned_chat(
    session: AsyncSession,
    chat_id: str,
    username: str,
) -> db_mod.Chat:
    chat = await db_mod.get_chat(session, chat_id)
    if chat is None or chat.username != username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _msg_out(msg: db_mod.Msg) -> MsgOut:
    return MsgOut(
        id=msg.id,
        chat_id=msg.chat_id,
        is_user=msg.is_user,
        text=msg.text or "",
        created_at=msg.created_at,
        reasoning=db_mod.reasoning_steps(msg),
    )


def _auto_title(text: str) -> str:
    text = text.strip()
    if len(text) > 42:
        return f"{text[:42]}…"
    return text


@app.get("/health")
async def health():
    agents = await proxy.agents_health()
    return {
        "ok": True,
        "agents_url": proxy.AGENTS_URL,
        "agents": agents,
    }


@app.get("/ready")
async def ready():
    """Readiness probe that does not depend on the Agents service."""
    return {"ok": True}


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: AsyncSession = Depends(db_mod.get_session),
):
    if await db_mod.get_user_by_username(session, body.username):
        raise HTTPException(status_code=400, detail="Username already taken")
    try:
        user = await auth.register_user(
            session,
            body.username,
            body.name,
            body.password,
        )
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Username already taken")
    return user


@app.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(db_mod.get_session),
):
    result = await auth.login_user(session, body.username, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user, token = result
    return LoginResponse(
        token=token.token,
        user_id=user.id,
        username=user.username,
        name=user.name,
    )


@app.post("/logout")
async def logout(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(db_mod.get_session),
):
    ok = await auth.logout_token(session, _bearer_token(authorization))
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"message": "Logged out"}


@app.get("/chats", response_model=list[ChatOut])
async def list_chats(
    session: AsyncSession = Depends(db_mod.get_session),
    user: db_mod.User = Depends(current_user),
):
    return await db_mod.list_chats(session, user.username)


@app.post("/chats", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_chat(
    body: ChatCreate | None = None,
    session: AsyncSession = Depends(db_mod.get_session),
    user: db_mod.User = Depends(current_user),
):
    title = body.title if body else None
    return await db_mod.create_chat(
        session,
        username=user.username,
        title=title,
    )


@app.get("/chats/{chat_id}", response_model=ChatOut)
async def get_chat(
    chat_id: str,
    session: AsyncSession = Depends(db_mod.get_session),
    user: db_mod.User = Depends(current_user),
):
    return await _owned_chat(session, chat_id, user.username)


@app.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: str,
    session: AsyncSession = Depends(db_mod.get_session),
    user: db_mod.User = Depends(current_user),
):
    await _owned_chat(session, chat_id, user.username)
    await db_mod.delete_chat(session, chat_id)
    return {"message": "Chat deleted", "chat_id": chat_id}


@app.get("/chats/{chat_id}/messages", response_model=list[MsgOut])
async def get_messages(
    chat_id: str,
    session: AsyncSession = Depends(db_mod.get_session),
    user: db_mod.User = Depends(current_user),
):
    await _owned_chat(session, chat_id, user.username)
    return [_msg_out(m) for m in await db_mod.list_messages(session, chat_id)]


async def _ensure_chat_for_user(
    session: AsyncSession,
    chat_id: str,
    username: str,
) -> db_mod.Chat:
    chat = await db_mod.get_chat(session, chat_id)
    if chat is None:
        return await db_mod.get_or_create_chat(
            session,
            username=username,
            chat_id=chat_id,
        )
    if chat.username != username:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


async def _begin_user_turn(chat_id: str, username: str, text: str) -> list[dict]:
    """Load prev_conv first, then persist the current user message."""
    async with db_mod.session_scope() as session:
        chat = await _ensure_chat_for_user(session, chat_id, username)
        prev_conv = await db_mod.prev_conv_for_chat(session, chat_id)
        await db_mod.add_message(session, chat_id, is_user=True, text=text)
        if not chat.title:
            await db_mod.set_chat_title(session, chat_id, _auto_title(text))
        return prev_conv


async def _finish_assistant_turn(
    chat_id: str,
    *,
    answer: str,
    steps: list,
) -> None:
    async with db_mod.session_scope() as session:
        msg = await db_mod.add_message(
            session,
            chat_id,
            is_user=False,
            text=answer or "",
        )
        await db_mod.set_reasoning(session, msg.id, steps)


async def _iter_and_persist(
    chat_id: str,
    user_message: str,
    prev_conv: list[dict],
):
    """Yield agent events; on terminal event persist assistant msg + reasoning."""
    steps: list = []
    # Agent text is provisional: it may be narration before a tool call, or it
    # may be the final answer that is emitted again as a delta. Do not persist
    # it as WIP until a tool call proves that it was narration.
    pending_text: list[str] = []
    answer_parts: list[str] = []
    terminal = False
    try:
        async for event in proxy.iter_chat_events(user_message, prev_conv=prev_conv):
            etype = event.get("type")
            if etype == "text":
                pending_text.append(event.get("text") or "")
            elif etype == "tool_call":
                # Text immediately before a tool call was narration. Text in
                # a no-tool turn is the final answer and is discarded from WIP
                # when the corresponding delta arrives.
                if pending_text:
                    steps.append({"type": "text", "text": "".join(pending_text)})
                    pending_text.clear()
                # WIP tool traces — Reasoning only
                steps.append({
                    "type": "tool_call",
                    "call_id": event.get("call_id"),
                    "name": event.get("name"),
                    "args": event.get("args"),
                })
            elif etype == "delta":
                # Final answer stream only
                answer_parts.append(event.get("text") or "")
            elif etype in {"done", "error"}:
                terminal = True
                if etype == "done":
                    # Prefer assembled deltas (final output); ignore any
                    # provisional text that was not followed by a tool call.
                    answer = "".join(answer_parts)
                    if not answer and event.get("text"):
                        answer = str(event.get("text") or "")
                else:
                    err = event.get("message") or "unknown error"
                    answer = "".join(answer_parts)
                    if answer:
                        answer = f"{answer}\n\nError: {err}"
                    else:
                        answer = f"Error: {err}"
                await _finish_assistant_turn(chat_id, answer=answer, steps=steps)
            yield event
        if not terminal:
            message = "agent stream ended unexpectedly"
            await _finish_assistant_turn(
                chat_id,
                answer="".join(answer_parts) or f"Error: {message}",
                steps=steps,
            )
            yield {"type": "error", "message": message}
    except asyncio.CancelledError:
        await _finish_assistant_turn(
            chat_id,
            answer="".join(answer_parts) or "(cancelled)",
            steps=steps,
        )
        raise
    except Exception:
        if not terminal:
            answer = "".join(answer_parts) or "Error: agent stream failed"
            await _finish_assistant_turn(
                chat_id,
                answer=answer,
                steps=steps,
            )
        raise


async def _publish(chat_id: str, event: dict) -> None:
    """Buffer an event and fan it out to currently connected chat subscribers."""
    async with _chat_runs_lock:
        run = _chat_runs.get(chat_id)
        if run is None:
            return
        run.events.append(event)
        subscribers = tuple(run.subscribers)
        for websocket in subscribers:
            try:
                await websocket.send_json(event)
            except Exception:
                run.subscribers.discard(websocket)


async def _run_chat(chat_id: str, username: str, text: str) -> None:
    try:
        async with db_mod.booked_chat(chat_id):
            prev_conv = await _begin_user_turn(chat_id, username, text)
            async for event in _iter_and_persist(chat_id, text, prev_conv):
                await _publish(chat_id, event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _publish(chat_id, {"type": "error", "message": str(exc)})
    finally:
        async with _chat_runs_lock:
            _chat_runs.pop(chat_id, None)


async def _subscribe_chat(chat_id: str, websocket: WebSocket) -> None:
    """Subscribe atomically and replay the in-memory stream snapshot."""
    async with _chat_runs_lock:
        run = _chat_runs.get(chat_id)
        websocket_events = list(run.events) if run else []
        if run:
            run.subscribers.add(websocket)
        await websocket.send_json({
            "type": "ready",
            "chat_id": chat_id,
            "active": run is not None,
            "events": websocket_events,
        })


async def _unsubscribe_chat(chat_id: str, websocket: WebSocket) -> None:
    async with _chat_runs_lock:
        run = _chat_runs.get(chat_id)
        if run:
            run.subscribers.discard(websocket)


@app.websocket("/chats/{chat_id}/ws")
async def chat_ws(websocket: WebSocket, chat_id: str, token: str | None = None):
    """
    Chat over WebSocket.

    Connect:  ws://host/chats/{chat_id}/ws?token=<bearer>
    Client →  {"type": "message", "text": "..."}
    Server →  same slim events as SSE: delta | text | tool_call | done | error
    """
    async with db_mod.session_scope() as session:
        user = await auth.user_from_token(session, token or "")
        if user is None:
            await websocket.close(code=4401, reason="Unauthorized")
            return
        username = user.username

        chat = await db_mod.get_chat(session, chat_id)
        if chat is None:
            await db_mod.get_or_create_chat(
                session,
                username=username,
                chat_id=chat_id,
            )
        elif chat.username != username:
            await websocket.close(code=4404, reason="Chat not found")
            return

    await websocket.accept()
    await _subscribe_chat(chat_id, websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid JSON"})
                continue

            mtype = msg.get("type")
            if mtype == "message":
                text = (msg.get("text") or msg.get("user_message") or "").strip()
                if not text:
                    await websocket.send_json({"type": "error", "message": "empty message"})
                    continue
                async with _chat_runs_lock:
                    if chat_id in _chat_runs:
                        await websocket.send_json({
                            "type": "error",
                            "message": "A message is already being processed for this chat",
                        })
                        continue
                    run = ChatRun()
                    _chat_runs[chat_id] = run
                    run.subscribers.add(websocket)
                    run.task = asyncio.create_task(_run_chat(chat_id, username, text))

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"unknown type: {mtype!r}",
                })
    except WebSocketDisconnect:
        await _unsubscribe_chat(chat_id, websocket)


# Serve the Theta frontend (API routes above take precedence)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
