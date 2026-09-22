"""Password hashing and bearer-token helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import db as db_mod
from sqlalchemy.ext.asyncio import AsyncSession


def hash_password(password: str, salt: str | None = None) -> str:
    """Return `salt$hash` using PBKDF2-HMAC-SHA256."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, _ = password_hash.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), password_hash)


async def register_user(
    session: AsyncSession,
    username: str,
    name: str,
    password: str,
) -> db_mod.User:
    return await db_mod.create_user(
        session,
        username,
        name,
        hash_password(password),
    )


async def login_user(
    session: AsyncSession,
    username: str,
    password: str,
) -> tuple[db_mod.User, db_mod.Token] | None:
    user = await db_mod.get_user_by_username(session, username)
    if user is None or not verify_password(password, user.password_hash):
        return None
    token = await db_mod.create_token(session, user.id)
    return user, token


async def logout_token(session: AsyncSession, token: str) -> bool:
    return await db_mod.delete_token(session, token)


async def user_from_token(session: AsyncSession, token: str) -> db_mod.User | None:
    row = await db_mod.get_token(session, token)
    if row is None:
        return None
    return await db_mod.get_user_by_id(session, row.user_id)
