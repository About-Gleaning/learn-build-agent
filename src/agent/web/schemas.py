from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResponseMetaVO(BaseModel):
    round_count: int = 0
    tool_call_count: int = 0
    tool_names: list[str] = Field(default_factory=list)
    delegation_count: int = 0
    delegated_agents: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class ProcessItemVO(BaseModel):
    id: str = ""
    kind: str = ""
    title: str = ""
    detail: str = ""
    created_at: str = ""
    agent: str = ""
    agent_kind: str = ""
    depth: int = 0
    round: int = 0
    status: str = ""
    delegation_id: str = ""
    parent_tool_call_id: str = ""
    tool_name: str = ""
    tool_call_id: str = ""


class DisplayPartVO(BaseModel):
    id: str = ""
    kind: str = ""
    title: str = ""
    detail: str = ""
    text: str = ""
    created_at: str = ""
    agent: str = ""
    agent_kind: str = ""
    depth: int = 0
    round: int = 0
    status: str = ""
    delegation_id: str = ""
    parent_tool_call_id: str = ""
    tool_name: str = ""
    tool_call_id: str = ""


class ConfirmationVO(BaseModel):
    tool: str = ""
    question: str = ""
    target_agent: str = ""
    current_agent: str = ""
    action_type: str = ""
    plan_path: str = ""


class QuestionOptionVO(BaseModel):
    label: str = ""
    description: str = ""


class QuestionItemVO(BaseModel):
    question: str = ""
    header: str = ""
    options: list[QuestionOptionVO] = Field(default_factory=list)
    multiple: bool = False
    custom: bool = True


class QuestionVO(BaseModel):
    tool: str = ""
    request_id: str = ""
    title: str = ""
    questions: list[QuestionItemVO] = Field(default_factory=list)


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    user_input: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "plan"] = "build"
    provider: str | None = None
    model: str | None = None


class MessageVO(BaseModel):
    message_id: str
    role: str
    text: str
    created_at: str
    status: str
    agent: str
    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    turn_started_at: str = ""
    turn_completed_at: str = ""
    response_meta: ResponseMetaVO = Field(default_factory=ResponseMetaVO)
    process_items: list[ProcessItemVO] = Field(default_factory=list)
    display_parts: list[DisplayPartVO] = Field(default_factory=list)
    confirmation: ConfirmationVO | None = None
    question: QuestionVO | None = None


class SessionMessagesVO(BaseModel):
    session_id: str
    messages: list[MessageVO]


class SessionClearedVO(BaseModel):
    session_id: str
    cleared: bool = True


class StopSessionVO(BaseModel):
    session_id: str
    stopped: bool = True
    status: Literal["requested"] = "requested"


class ModeSwitchActionReq(BaseModel):
    action: Literal["confirm", "cancel"]


class ModeSwitchActionVO(BaseModel):
    session_id: str
    status: str
    current_mode: Literal["build", "plan"]
    message: MessageVO


class QuestionAnswerItemReq(BaseModel):
    answers: list[str] = Field(default_factory=list)
    notes: str = ""


class QuestionAnswerReq(BaseModel):
    answers: list[QuestionAnswerItemReq] = Field(default_factory=list)


class QuestionActionVO(BaseModel):
    session_id: str
    status: str
    current_mode: Literal["build", "plan"]
    message: MessageVO


class RuntimeProviderVO(BaseModel):
    name: str
    vendor: str
    default_model: str
    models: list[str]
    api_mode: Literal["responses", "chat_completions"]


class RuntimeAgentVO(BaseModel):
    name: Literal["build", "plan"]
    default_provider: str
    default_model: str
    api_mode: Literal["responses", "chat_completions"]


class SlashCommandVO(BaseModel):
    name: str
    description: str
    usage: str
    placeholder: str


class RuntimeOptionsVO(BaseModel):
    default_agent: Literal["build", "plan"]
    agents: list[RuntimeAgentVO]
    providers: list[RuntimeProviderVO]
    slash_commands: list[SlashCommandVO] = Field(default_factory=list)
    workspace_root: str
    workspace_name: str
    has_agents_md: bool
    launch_mode: str


class PathSuggestionVO(BaseModel):
    path: str
    name: str
    relative_path: str
    kind: Literal["file", "directory"]


class PathSuggestionsVO(BaseModel):
    query: str
    suggestions: list[PathSuggestionVO] = Field(default_factory=list)


class PathSelectionReq(BaseModel):
    relative_path: str = Field(min_length=1, max_length=1024)


class PathSelectionVO(BaseModel):
    recorded: bool = True
    relative_path: str
