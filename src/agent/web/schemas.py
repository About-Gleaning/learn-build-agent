from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    user_input: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "plan"] = "build"
    provider: str | None = None


class MessageVO(BaseModel):
    message_id: str
    role: str
    text: str
    created_at: str
    status: str
    agent: str
    provider: str = ""
    model: str = ""


class SessionMessagesVO(BaseModel):
    session_id: str
    messages: list[MessageVO]


class SessionClearedVO(BaseModel):
    session_id: str
    cleared: bool = True


class RuntimeProviderVO(BaseModel):
    name: str
    default_model: str


class RuntimeAgentVO(BaseModel):
    name: Literal["build", "plan"]
    default_provider: str
    default_model: str


class RuntimeOptionsVO(BaseModel):
    default_agent: Literal["build", "plan"]
    agents: list[RuntimeAgentVO]
    providers: list[RuntimeProviderVO]
