"""SQLAlchemy models + async CRUD for users, chats, messages, and tokens."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship, selectinload

Base = declarative_base()
DB_PATH = Path(os.environ.get("OPERATIONS_DB", str(Path(__file__).resolve().with_name("operations.db"))))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    chats = relationship("Chat", back_populates="user", cascade="all, delete-orphan")
    tokens = relationship("Token", back_populates="user", cascade="all, delete-orphan")


class Chat(Base):
    __tablename__ = "chats"
    id = Column(String, primary_key=True)
    username = Column(String, ForeignKey("users.username"), nullable=False, index=True)
    title = Column(String, nullable=True)
    is_processing = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="chats")
    messages = relationship("Msg", back_populates="chat", cascade="all, delete-orphan", order_by="Msg.id")


class Msg(Base):
    __tablename__ = "msgs"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, ForeignKey("chats.id"), nullable=False, index=True)
    is_user = Column(Boolean, nullable=False, default=True)
    text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    chat = relationship("Chat", back_populates="messages")
    reasoning = relationship("Reasoning", back_populates="msg", uselist=False, cascade="all, delete-orphan")


class Reasoning(Base):
    __tablename__ = "reasoning"
    id = Column(Integer, primary_key=True)
    msg_id = Column(Integer, ForeignKey("msgs.id"), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False, default="[]")
    msg = relationship("Msg", back_populates="reasoning")


class Token(Base):
    __tablename__ = "tokens"
    token = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="tokens")


async def create_user(session: AsyncSession, username: str, name: str, password_hash: str) -> User:
    user = User(username=username.strip().lower(), name=name, password_hash=password_hash)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id))


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username.strip().lower()))


async def create_token(session: AsyncSession, user_id: int) -> Token:
    row = Token(token=uuid.uuid4().hex, user_id=user_id)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_token(session: AsyncSession, token: str) -> Token | None:
    return await session.scalar(select(Token).where(Token.token == token))


async def delete_token(session: AsyncSession, token: str) -> bool:
    row = await get_token(session, token)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def create_chat(session: AsyncSession, username: str, title: str | None = None, chat_id: str | None = None) -> Chat:
    chat = Chat(id=chat_id or str(uuid.uuid4()), username=username.strip().lower(), title=title)
    session.add(chat)
    await session.flush()
    await session.refresh(chat)
    return chat


async def get_chat(session: AsyncSession, chat_id: str) -> Chat | None:
    return await session.scalar(select(Chat).where(Chat.id == chat_id))


async def list_chats(session: AsyncSession, username: str) -> list[Chat]:
    result = await session.scalars(select(Chat).where(Chat.username == username.strip().lower()).order_by(Chat.created_at.desc()))
    return list(result)


async def delete_chat(session: AsyncSession, chat_id: str) -> bool:
    chat = await get_chat(session, chat_id)
    if chat is None:
        return False
    await session.delete(chat)
    await session.flush()
    return True


async def get_or_create_chat(session: AsyncSession, username: str, chat_id: str, title: str | None = None) -> Chat:
    chat = await get_chat(session, chat_id)
    return chat if chat is not None else await create_chat(session, username, title, chat_id)


async def claim_chat(session: AsyncSession, chat_id: str) -> bool:
    """Atomically book a chat for one in-flight request."""
    result = await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.is_processing.is_(False))
        .values(is_processing=True)
    )
    return result.rowcount == 1


async def release_chat(session: AsyncSession, chat_id: str) -> None:
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id)
        .values(is_processing=False)
    )


class ChatAlreadyProcessing(RuntimeError):
    def __init__(self, chat_id: str):
        super().__init__(f"Chat {chat_id} is already processing a message")


@asynccontextmanager
async def booked_chat(chat_id: str):
    """Book a chat for one request and release it when the request exits."""
    async with session_scope() as session:
        if not await claim_chat(session, chat_id):
            raise ChatAlreadyProcessing(chat_id)

    try:
        yield
    finally:
        async with session_scope() as session:
            await release_chat(session, chat_id)


async def set_chat_title(session: AsyncSession, chat_id: str, title: str) -> None:
    chat = await get_chat(session, chat_id)
    if chat is not None:
        chat.title = title
        await session.flush()


async def list_messages(session: AsyncSession, chat_id: str) -> list[Msg]:
    result = await session.scalars(select(Msg).options(selectinload(Msg.reasoning)).where(Msg.chat_id == chat_id).order_by(Msg.id.asc()))
    return list(result)


async def prev_conv_for_chat(session: AsyncSession, chat_id: str) -> list[dict]:
    out = []
    for msg in await list_messages(session, chat_id):
        content = (msg.text or "").strip()
        if content:
            out.append({"role": "user" if msg.is_user else "assistant", "content": content})
    return out


async def add_message(session: AsyncSession, chat_id: str, *, is_user: bool, text: str) -> Msg:
    msg = Msg(chat_id=chat_id, is_user=is_user, text=text or "")
    session.add(msg)
    await session.flush()
    await session.refresh(msg)
    return msg


async def set_reasoning(session: AsyncSession, msg_id: int, data: list | dict | str) -> Reasoning:
    payload = data if isinstance(data, str) else json.dumps(data)
    row = await session.scalar(select(Reasoning).where(Reasoning.msg_id == msg_id))
    if row is None:
        row = Reasoning(msg_id=msg_id, data=payload)
        session.add(row)
    else:
        row.data = payload
    await session.flush()
    await session.refresh(row)
    return row


def reasoning_steps(msg: Msg) -> list:
    if msg.reasoning is None or not msg.reasoning.data:
        return []
    try:
        parsed = json.loads(msg.reasoning.data)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []
