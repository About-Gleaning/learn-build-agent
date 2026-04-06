from __future__ import annotations

import json
from typing import Any

from ..core.message import Message, get_message_text
from .schemas import DisplayPartVO, MessageVO


def _resolve_message_display_text(message: Message) -> str:
    for part in message.get("parts", []):
        if part.get("type") != "text":
            continue
        meta = part.get("meta")
        if not isinstance(meta, dict):
            continue
        display_text = str(meta.get("display_text", "")).strip()
        if display_text:
            return display_text
    return get_message_text(message)


def _normalize_response_meta(raw_value: Any) -> dict[str, Any]:
    response_meta = raw_value if isinstance(raw_value, dict) else {}
    return {
        "round_count": int(response_meta.get("round_count", 0) or 0),
        "tool_call_count": int(response_meta.get("tool_call_count", 0) or 0),
        "tool_names": [str(item) for item in response_meta.get("tool_names", []) if str(item).strip()],
        "delegation_count": int(response_meta.get("delegation_count", 0) or 0),
        "delegated_agents": [str(item) for item in response_meta.get("delegated_agents", []) if str(item).strip()],
        "duration_ms": int(response_meta.get("duration_ms", 0) or 0),
    }


def _normalize_process_items(raw_value: Any) -> list[dict[str, Any]]:
    process_items = raw_value if isinstance(raw_value, list) else []
    return [
        {
            "id": str(item.get("id", "")),
            "kind": str(item.get("kind", "")),
            "title": str(item.get("title", "")),
            "detail": str(item.get("detail", "")),
            "created_at": str(item.get("created_at", "")),
            "agent": str(item.get("agent", "")),
            "agent_kind": str(item.get("agent_kind", "")),
            "depth": int(item.get("depth", 0) or 0),
            "round": int(item.get("round", 0) or 0),
            "status": str(item.get("status", "")),
            "delegation_id": str(item.get("delegation_id", "")),
            "parent_tool_call_id": str(item.get("parent_tool_call_id", "")),
            "tool_name": str(item.get("tool_name", "")),
            "tool_call_id": str(item.get("tool_call_id", "")),
        }
        for item in process_items
        if isinstance(item, dict)
    ]


def _normalize_display_parts(raw_value: Any) -> list[DisplayPartVO]:
    display_parts = raw_value if isinstance(raw_value, list) else []
    return [
        DisplayPartVO(
            id=str(item.get("id", "")),
            kind=str(item.get("kind", "")),
            title=str(item.get("title", "")),
            detail=str(item.get("detail", "")),
            text=str(item.get("text", "")),
            created_at=str(item.get("created_at", "")),
            agent=str(item.get("agent", "")),
            agent_kind=str(item.get("agent_kind", "")),
            depth=int(item.get("depth", 0) or 0),
            round=int(item.get("round", 0) or 0),
            status=str(item.get("status", "")),
            delegation_id=str(item.get("delegation_id", "")),
            parent_tool_call_id=str(item.get("parent_tool_call_id", "")),
            tool_name=str(item.get("tool_name", "")),
            tool_call_id=str(item.get("tool_call_id", "")),
        )
        for item in display_parts
        if isinstance(item, dict)
    ]


def _normalize_confirmation(raw_value: Any) -> dict[str, str] | None:
    if not isinstance(raw_value, dict):
        return None
    return {
        "tool": str(raw_value.get("tool", "")),
        "question": str(raw_value.get("question", "")),
        "target_agent": str(raw_value.get("target_agent", "")),
        "current_agent": str(raw_value.get("current_agent", "")),
        "action_type": str(raw_value.get("action_type", "")),
        "plan_path": str(raw_value.get("plan_path", "")),
    }


def _normalize_question(raw_value: Any) -> dict[str, Any] | None:
    if not isinstance(raw_value, dict):
        return None
    questions = raw_value.get("questions")
    normalized_questions: list[dict[str, Any]] = []
    if isinstance(questions, list):
        for item in questions:
            if not isinstance(item, dict):
                continue
            raw_options = item.get("options")
            normalized_options: list[dict[str, str]] = []
            if isinstance(raw_options, list):
                for option in raw_options:
                    if not isinstance(option, dict):
                        continue
                    normalized_options.append(
                        {
                            "label": str(option.get("label", "")),
                            "description": str(option.get("description", "")),
                        }
                    )
            normalized_questions.append(
                {
                    "question": str(item.get("question", "")),
                    "header": str(item.get("header", "")),
                    "options": normalized_options,
                    "multiple": bool(item.get("multiple", False)),
                    "custom": bool(item.get("custom", True)),
                }
            )
    return {
        "tool": str(raw_value.get("tool", "")),
        "request_id": str(raw_value.get("request_id", "")),
        "title": str(raw_value.get("title", "")),
        "questions": normalized_questions,
    }


def message_to_vo(message: Message) -> MessageVO:
    # Web 层统一在这里兜底缺省字段，避免路由层重复手工搬运。
    info = message.get("info", {})
    return MessageVO(
        message_id=str(info.get("message_id", "")),
        role=str(info.get("role", "")),
        text=_resolve_message_display_text(message),
        created_at=str(info.get("created_at", "")),
        status=str(info.get("status", "")),
        agent=str(info.get("agent", "")),
        provider=str(info.get("provider", "")),
        model=str(info.get("model", "")),
        finish_reason=str(info.get("finish_reason", "")),
        turn_started_at=str(info.get("turn_started_at", "")),
        turn_completed_at=str(info.get("turn_completed_at", "")),
        response_meta=_normalize_response_meta(info.get("response_meta")),
        process_items=_normalize_process_items(info.get("process_items")),
        display_parts=_normalize_display_parts(info.get("display_parts")),
        confirmation=_normalize_confirmation(info.get("confirmation")),
        question=_normalize_question(info.get("question")),
    )


def sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_stream_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    event_type = str(event.get("type", "")).strip()
    if not event_type:
        return None
    # SSE 协议把事件名和 data 分开传输，这里只保留真正的 payload 字段。
    payload: dict[str, Any] = {key: value for key, value in event.items() if key != "type"}
    return event_type, payload
