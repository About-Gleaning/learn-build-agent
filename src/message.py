import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypedDict


MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["pending", "running", "completed", "failed", "interrupted"]
PartStatus = Literal["pending", "running", "completed", "failed"]


class NormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class TokenUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class MessageInfo(TypedDict, total=False):
    message_id: str
    session_id: str
    role: MessageRole
    created_at: str
    model: str
    status: MessageStatus
    finish_reason: str
    parent_id: str
    trace_id: str
    token_usage: TokenUsage
    cost: float
    error: NormalizedError


class Part(TypedDict, total=False):
    part_id: str
    type: str
    seq: int
    status: PartStatus
    created_at: str
    updated_at: str
    meta: dict[str, Any]
    content: str
    name: str
    arguments: str
    tool_call_id: str


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
    status: PartStatus = "completed",
    content: str = "",
    name: str = "",
    arguments: str = "",
    tool_call_id: str = "",
    meta: dict[str, Any] | None = None,
) -> Part:
    now = utc_now_iso()
    part: Part = {
        "part_id": _new_id("part"),
        "type": part_type,
        "seq": len(message["parts"]) + 1,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "meta": meta or {},
    }
    if content:
        part["content"] = content
    if name:
        part["name"] = name
    if arguments:
        part["arguments"] = arguments
    if tool_call_id:
        part["tool_call_id"] = tool_call_id
    message["parts"].append(part)
    publish_event("part_appended", message, {"part": part})
    return part


def append_text_part(message: Message, content: str) -> Part:
    return append_part(message, "text", content=content)


def append_compact_summary_part(message: Message, content: str) -> Part:
    return append_part(message, "compact_summary", content=content)


def append_tool_call_part(message: Message, *, tool_call_id: str, name: str, arguments: str) -> Part:
    return append_part(
        message,
        "tool_call",
        name=name,
        arguments=arguments,
        tool_call_id=tool_call_id,
    )


def append_tool_result_part(message: Message, *, tool_call_id: str, name: str, content: str) -> Part:
    return append_part(
        message,
        "tool_result",
        name=name,
        content=content,
        tool_call_id=tool_call_id,
    )


def append_error_part(message: Message, code: str, error_message: str, details: str = "") -> Part:
    part = append_part(
        message,
        "error",
        status="failed",
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
        if part.get("type") in {"text", "compact_summary", "reasoning", "error"} and part.get("content"):
            lines.append(str(part["content"]))
    return "\n".join(lines).strip()


def extract_tool_calls(message: Message) -> list[ToolFunctionCall]:
    tool_calls: list[ToolFunctionCall] = []
    for part in message["parts"]:
        if part.get("type") != "tool_call":
            continue
        tool_call_id = str(part.get("tool_call_id", ""))
        name = str(part.get("name", ""))
        arguments = str(part.get("arguments", "{}"))
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


def _tool_result_for_provider(message: Message) -> tuple[str, str]:
    tool_call_id = ""
    content = ""
    for part in message["parts"]:
        if part.get("type") == "tool_result":
            tool_call_id = str(part.get("tool_call_id", ""))
            content = str(part.get("content", ""))
            break
    if not content:
        content = get_message_text(message)
    return tool_call_id, content


def to_provider_messages(messages: list[Message]) -> list[dict[str, Any]]:
    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        role = get_role(message)

        if role == "tool":
            tool_call_id, content = _tool_result_for_provider(message)
            provider_message: dict[str, Any] = {
                "role": "tool",
                "content": content,
            }
            if tool_call_id:
                provider_message["tool_call_id"] = tool_call_id
            provider_messages.append(provider_message)
            continue

        provider_message = {
            "role": role,
            "content": get_message_text(message),
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

        provider_messages.append(provider_message)

    return provider_messages


def parse_provider_response(
    response: Any,
    *,
    session_id: str,
    model: str,
    parent_id: str = "",
    trace_id: str = "",
) -> Message:
    choice = response.choices[0]
    provider_msg = choice.message
    assistant = create_message(
        "assistant",
        session_id,
        model=model,
        status="running",
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        parent_id=parent_id,
        trace_id=trace_id,
    )

    content = getattr(provider_msg, "content", None)
    if content:
        append_text_part(assistant, str(content))

    for tool_call in getattr(provider_msg, "tool_calls", None) or []:
        append_tool_call_part(
            assistant,
            tool_call_id=str(tool_call.id),
            name=str(tool_call.function.name),
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
    if "401" in lowered or "auth" in lowered or "unauthorized" in lowered:
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
    error: NormalizedError,
    parent_id: str = "",
    trace_id: str = "",
) -> Message:
    message = create_message(
        "assistant",
        session_id,
        model=model,
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
