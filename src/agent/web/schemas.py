from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    user_input: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "plan"] = "build"


class MessageVO(BaseModel):
    message_id: str
    role: str
    text: str
    created_at: str
    status: str
    agent: str


class SessionMessagesVO(BaseModel):
    session_id: str
    messages: list[MessageVO]


class SessionClearedVO(BaseModel):
    session_id: str
    cleared: bool = True
