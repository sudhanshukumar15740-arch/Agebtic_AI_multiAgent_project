"""Async pool of Responses-API websocket connections."""

import asyncio
import os
import time
from asyncio import Queue
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State

BASE_URL = os.environ.get("LLM_BASE_URL", "ws://127.0.0.1:4000/v1")
API_KEY = os.environ.get("LLM_API_KEY", "sk-1234")
LINK_MAX_AGE_S = float(os.environ.get("LLM_LINK_MAX_AGE_S", 50 * 60))


async def _connect() -> ClientConnection:
    return await connect(
        f"{BASE_URL}/responses",
        additional_headers={"Authorization": f"Bearer {API_KEY}"},
    )


@dataclass
class Conn:
    ws: ClientConnection
    opened_at: float

    @classmethod
    async def create(cls) -> "Conn":
        now = time.monotonic()
        return cls(await _connect(), now)

    def expired(self) -> bool:
        now = time.monotonic()
        return (
            self.ws.state is not State.OPEN
            or now - self.opened_at > LINK_MAX_AGE_S
        )

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass

    async def refresh(self) -> None:
        await self.close()
        self.ws = await _connect()
        now = time.monotonic()
        self.opened_at = now


class ConnPool:
    """Fixed-size async pool; one connection is checked out per turn."""

    def __init__(self, size: int):
        self.size = max(1, size)
        self.free: Queue[Conn] = Queue(maxsize=self.size)

    @classmethod
    async def create(cls, size: int) -> "ConnPool":
        pool = cls(size)
        for conn in await asyncio.gather(*(Conn.create() for _ in range(pool.size))):
            await pool.free.put(conn)
        return pool

    async def acquire(self) -> Conn:
        conn = await self.free.get()
        if conn.expired():
            await conn.refresh()
        return conn

    async def release(self, conn: Conn) -> None:
        await self.free.put(conn)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Conn, None]:
        conn = await self.acquire()
        try:
            yield conn
        except BaseException:
            await conn.close()
            raise
        finally:
            await self.release(conn)

    async def close(self) -> None:
        while not self.free.empty():
            await (await self.free.get()).close()
