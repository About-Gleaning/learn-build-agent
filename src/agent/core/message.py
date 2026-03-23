import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypedDict


MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["pending", "running", "completed", "failed", "interrupted"]
ToolStateStatus = Literal["requested", "completed", "failed"]


class NormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class TokenUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ResponseMeta(TypedDict, total=False):
    round_count: int
    tool_call_count: int
    tool_names: list[str]
    delegation_count: int
    delegated_agents: list[str]
    duration_ms: int


class ProcessItem(TypedDict, total=False):
    id: str
    kind: str
    title: str
    detail: str
    created_at: str
    agent: str
    agent_kind: str
    depth: int
    round: int
    status: str
    delegation_id: str
    parent_tool_call_id: str
    tool_name: str
    tool_call_id: str


class DisplayPart(TypedDict, total=False):
    id: str
    kind: str
    title: str
    detail: str
    text: str
    created_at: str
    agent: str
    agent_kind: str
    depth: int
    round: int
    status: str
    delegation_id: str
    parent_tool_call_id: str
    tool_name: str
    tool_call_id: str


class ConfirmationInfo(TypedDict, total=False):
    tool: str
    question: str
    target_agent: str
    current_agent: str
    action_type: str
    plan_path: str


class MessageInfo(TypedDict, total=False):
    message_id: str
    session_id: str
    role: MessageRole
    created_at: str
    model: str
    provider: str
    status: MessageStatus
    finish_reason: str
    parent_id: str
    trace_id: str
    token_usage: TokenUsage
    cost: float
    error: NormalizedError
    agent: str
    turn_started_at: str
    turn_completed_at: str
    summary: bool
    response_meta: ResponseMeta
    process_items: list[ProcessItem]
    display_parts: list[DisplayPart]
    confirmation: ConfirmationInfo


class Part(TypedDict, total=False):
    part_id: str
    type: str
    seq: int
    created_at: str
    updated_at: str
    meta: dict[str, Any]
    content: str
    name: str
    state: dict[str, Any]


class Message(TypedDict):
    info: MessageInfo
    parts: list[Part]


class ToolFunctionCall(TypedDict):
    id: str
    name: str
    arguments: str


class SessionEvent(TypedDict, total=False):
    type: str
    timestamp: str
    session_id: str
    message_id: str
    payload: dict[str, Any]


class EventBus:
    """轻量同步事件总线，用于消息更新时的可观测通知。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[SessionEvent], None]]] = {}

    def subscribe(self, event_type: str, callback: Callable[[SessionEvent], None]) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    def publish(self, event: SessionEvent) -> None:
        for callback in self._subscribers.get(event["type"], []):
            callback(event)


EVENT_BUS = EventBus()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_message(
    role: MessageRole,
    session_id: str,
    *,
    model: str = "",
    provider: str = "",
    status: MessageStatus = "pending",
    finish_reason: str = "",
    parent_id: str = "",
    trace_id: str = "",
) -> Message:
    message: Message = {
        "info": {
            "message_id": _new_id("msg"),
            "session_id": session_id,
            "role": role,
            "created_at": utc_now_iso(),
            "model": model,
            "provider": provider,
            "status": status,
            "finish_reason": finish_reason,
            "parent_id": parent_id,
            "trace_id": trace_id,
        },
        "parts": [],
    }
    publish_event("message_created", message, {})
    return message


def append_part(
    message: Message,
    part_type: str,
    *,
    content: str = "",
    name: str = "",
    state: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Part:
    now = utc_now_iso()
    part: Part = {
        "part_id": _new_id("part"),
        "type": part_type,
        "seq": len(message["parts"]) + 1,
        "created_at": now,
        "updated_at": now,
        "meta": meta or {},
    }
    if content:
        part["content"] = content
    if name:
        part["name"] = name
    if state:
        part["state"] = state
    message["parts"].append(part)
    publish_event("part_appended", message, {"part": part})
    return part


def append_text_part(message: Message, content: str, meta: dict[str, Any] | None = None) -> Part:
    return append_part(message, "text", content=content, meta=meta)


def append_reasoning_part(message: Message, content: str, meta: dict[str, Any] | None = None) -> Part:
    return append_part(message, "reasoning", content=content, meta=meta)


def append_compaction_part(message: Message, content: str, meta: dict[str, Any] | None = None) -> Part:
    return append_part(message, "compaction", content=content, meta=meta)


def append_compact_summary_part(message: Message, content: str) -> Part:
    return append_part(message, "compact_summary", content=content)


def append_tool_call_part(message: Message, *, tool_call_id: str, name: str, arguments: str) -> Part:
    return append_tool_part(
        message,
        tool_call_id=tool_call_id,
        name=name,
        status="requested",
        arguments=arguments,
    )


def append_tool_part(
    message: Message,
    *,
    tool_call_id: str,
    name: str,
    status: ToolStateStatus,
    arguments: str = "",
    output: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> Part:
    state: dict[str, Any] = {
        "status": status,
        "tool_call_id": tool_call_id,
        "input": {
            "arguments": arguments,
        },
    }
    if output is not None:
        state["output"] = output
    return append_part(
        message,
        "tool",
        name=name,
        state=state,
        meta=meta,
    )


def append_tool_result_part(message: Message, *, tool_call_id: str, name: str, content: str) -> Part:
    return append_tool_part(
        message,
        tool_call_id=tool_call_id,
        name=name,
        status="completed",
        output={"output": content, "metadata": {"status": "completed"}},
    )


def append_error_part(message: Message, code: str, error_message: str, details: str = "") -> Part:
    part = append_part(
        message,
        "error",
        content=error_message,
        meta={"code": code, "details": details},
    )
    message["info"]["status"] = "failed"
    message["info"]["error"] = {"code": code, "message": error_message, "details": details}
    publish_event("message_failed", message, {"error": message["info"]["error"]})
    return part


def mark_message_completed(message: Message, finish_reason: str = "stop") -> None:
    message["info"]["status"] = "completed"
    message["info"]["finish_reason"] = finish_reason
    publish_event("message_completed", message, {"finish_reason": finish_reason})


def mark_message_running(message: Message) -> None:
    message["info"]["status"] = "running"
    publish_event("message_updated", message, {"status": "running"})


def get_role(message: Message) -> MessageRole:
    role = message["info"].get("role", "user")
    return role  # type: ignore[return-value]


def get_message_text(message: Message) -> str:
    lines: list[str] = []
    for part in message["parts"]:
        if part.get("type") in {"text", "compaction", "compact_summary", "reasoning", "error"} and part.get("content"):
            lines.append(str(part["content"]))
    return "\n".join(lines).strip()


def _get_provider_message_content(message: Message) -> str:
    lines: list[str] = []
    for part in message["parts"]:
        if part.get("type") in {"text", "compaction", "compact_summary", "error"} and part.get("content"):
            lines.append(str(part["content"]))
    return "\n".join(lines).strip()


def has_compaction_part(message: Message) -> bool:
    return any(part.get("type") == "compaction" for part in message["parts"])


def is_completed_summary_message(message: Message) -> bool:
    info = message.get("info", {})
    if get_role(message) != "assistant":
        return False
    if not bool(info.get("summary")):
        return False
    return bool(str(info.get("finish_reason", "")).strip())


def trim_messages_by_compaction_checkpoint(messages: list[Message]) -> list[Message]:
    completed: set[str] = set()
    suffix: list[Message] = []
    found_boundary = False

    for message in reversed(messages):
        suffix.append(message)
        info = message.get("info", {})

        if is_completed_summary_message(message):
            parent_id = str(info.get("parent_id", "")).strip()
            if parent_id:
                completed.add(parent_id)

        if get_role(message) != "user":
            continue

        message_id = str(info.get("message_id", "")).strip()
        if message_id and message_id in completed and has_compaction_part(message):
            found_boundary = True
            break

    if not found_boundary:
        return messages

    suffix.reverse()
    return suffix


def extract_tool_calls(message: Message) -> list[ToolFunctionCall]:
    tool_calls: list[ToolFunctionCall] = []
    for part in message["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        if str(state.get("status", "")).strip().lower() != "requested":
            continue
        tool_call_id = str(state.get("tool_call_id", ""))
        name = str(part.get("name", ""))
        input_data = state.get("input") if isinstance(state.get("input"), dict) else {}
        arguments = str(input_data.get("arguments", "{}"))
        if not tool_call_id or not name:
            continue
        tool_calls.append(
            {
                "id": tool_call_id,
                "name": name,
                "arguments": arguments,
            }
        )
    return tool_calls


def _tool_result_for_provider(message: Message) -> tuple[str, str, list[dict[str, Any]]]:
    tool_call_id = ""
    content = ""
    attachments: list[dict[str, Any]] = []
    for part in message["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        if str(state.get("status", "")).strip().lower() not in {"completed", "failed"}:
            continue
        tool_call_id = str(state.get("tool_call_id", ""))
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        content = str(output.get("output", ""))
        raw_attachments = output.get("attachments")
        if isinstance(raw_attachments, list):
            attachments = [item for item in raw_attachments if isinstance(item, dict)]
        if content:
            break
    if not content:
        content = get_message_text(message)
    return tool_call_id, content, attachments


def _collect_reasoning_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        content = value.strip()
        return [content] if content else []
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            texts.extend(_collect_reasoning_text(item))
        return texts
    if isinstance(value, dict):
        texts: list[str] = []
        for key in ("text", "content", "reasoning_content", "reasoning"):
            if key in value:
                texts.extend(_collect_reasoning_text(value.get(key)))
        return texts

    for attr_name in ("text", "content", "reasoning_content", "reasoning"):
        attr_value = getattr(value, attr_name, None)
        if attr_value is not None:
            return _collect_reasoning_text(attr_value)
    return []


def extract_reasoning_content(message: Message) -> str:
    texts: list[str] = []
    for part in message["parts"]:
        if part.get("type") != "reasoning":
            continue
        content = str(part.get("content", "")).strip()
        if content:
            texts.append(content)
    return "\n".join(texts).strip()


def extract_provider_reasoning_content(provider_message: Any) -> str:
    texts = _collect_reasoning_text(getattr(provider_message, "reasoning_content", None))
    if not texts and isinstance(provider_message, dict):
        texts = _collect_reasoning_text(provider_message.get("reasoning_content"))
    return "\n".join(texts).strip()


def to_provider_messages(messages: list[Message]) -> list[dict[str, Any]]:
    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        role = get_role(message)

        if role == "tool":
            tool_call_id, content, attachments = _tool_result_for_provider(message)
            provider_message: dict[str, Any] = {
                "role": "tool",
                "content": content,
            }
            if tool_call_id:
                provider_message["tool_call_id"] = tool_call_id
            if attachments:
                provider_message["attachments"] = attachments
            provider_messages.append(provider_message)
            continue

        provider_message = {
            "role": role,
            "content": _get_provider_message_content(message),
        }

        if role == "assistant":
            tool_calls = extract_tool_calls(message)
            if tool_calls:
                provider_message["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in tool_calls
                ]
                reasoning_content = extract_reasoning_content(message)
                if reasoning_content:
                    provider_message["reasoning_content"] = reasoning_content

        provider_messages.append(provider_message)

    return provider_messages


def parse_provider_response(
    response: Any,
    *,
    session_id: str,
    model: str,
    provider: str = "",
    parent_id: str = "",
    trace_id: str = "",
) -> Message:
    choice = response.choices[0]
    provider_msg = choice.message
    assistant = create_message(
        "assistant",
        session_id,
        model=model,
        provider=provider,
        status="running",
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        parent_id=parent_id,
        trace_id=trace_id,
    )

    content = getattr(provider_msg, "content", None)
    if content:
        append_text_part(assistant, str(content))

    reasoning_content = extract_provider_reasoning_content(provider_msg)
    if reasoning_content:
        # 保留 provider thinking 原文，供后续多轮 tool call 历史回放使用。
        append_reasoning_part(assistant, reasoning_content)

    for tool_call in getattr(provider_msg, "tool_calls", None) or []:
        append_tool_part(
            assistant,
            tool_call_id=str(tool_call.id),
            name=str(tool_call.function.name),
            status="requested",
            arguments=str(tool_call.function.arguments),
        )

    usage = getattr(response, "usage", None)
    if usage is not None:
        assistant["info"]["token_usage"] = {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    mark_message_completed(assistant, finish_reason=assistant["info"].get("finish_reason", "stop") or "stop")
    return assistant


def normalize_error(exc: Exception) -> NormalizedError:
    text = str(exc)
    code = "api_error"
    lowered = text.lower()
    if "kimi_file_extract_failed" in lowered:
        code = "kimi_file_extract_failed"
    elif "unsupported_file_input" in lowered:
        code = "unsupported_file_input"
    elif "401" in lowered or "auth" in lowered or "unauthorized" in lowered:
        code = "auth_error"
    elif "timeout" in lowered:
        code = "timeout"
    elif "rate" in lowered and "limit" in lowered:
        code = "rate_limit"
    elif "context" in lowered and "length" in lowered:
        code = "output_too_long"

    return {
        "code": code,
        "message": text[:500],
        "details": type(exc).__name__,
    }


def create_error_message(
    *,
    session_id: str,
    model: str,
    provider: str = "",
    error: NormalizedError,
    parent_id: str = "",
    trace_id: str = "",
) -> Message:
    message = create_message(
        "assistant",
        session_id,
        model=model,
        provider=provider,
        status="failed",
        finish_reason="error",
        parent_id=parent_id,
        trace_id=trace_id,
    )
    append_error_part(
        message,
        code=error.get("code", "api_error"),
        error_message=error.get("message", "Unknown error"),
        details=error.get("details", ""),
    )
    return message


def count_parts(message: Message, part_type: str) -> int:
    return sum(1 for part in message["parts"] if part.get("type") == part_type)


def publish_event(event_type: str, message: Message, payload: dict[str, Any]) -> None:
    EVENT_BUS.publish(
        {
            "type": event_type,
            "timestamp": utc_now_iso(),
            "session_id": message["info"].get("session_id", ""),
            "message_id": message["info"].get("message_id", ""),
            "payload": payload,
        }
    )


def estimate_message_size(message: Message) -> int:
    try:
        return len(json.dumps(message, ensure_ascii=False))
    except Exception:
        return len(str(message))
