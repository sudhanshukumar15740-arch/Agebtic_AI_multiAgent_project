"""Pydantic request/response models for the Operations API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    password: str = Field(min_length=6)


class UserOut(BaseModel):
    id: int
    username: str
    name: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: int
    username: str
    name: str


class ChatCreate(BaseModel):
    title: str | None = None


class ChatOut(BaseModel):
    id: str
    username: str
    title: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class MsgOut(BaseModel):
    id: int
    chat_id: str
    is_user: bool
    text: str
    created_at: datetime | None = None
    reasoning: list[Any] = Field(default_factory=list)

    model_config = {"from_attributes": True}
