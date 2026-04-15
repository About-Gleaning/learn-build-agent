from __future__ import annotations

import json
from datetime import datetime
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


def _compute_duration_ms(started_at: str, completed_at: str) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return 0
    return max(int((completed - started).total_seconds() * 1000), 0)


def _normalize_response_meta(raw_value: Any, *, info: dict[str, Any] | None = None, message: Message | None = None) -> dict[str, Any]:
    response_meta = raw_value if isinstance(raw_value, dict) else {}
    runtime_info = info or {}
    block_summary = _summarize_tool_blocks(message) if isinstance(message, dict) else {"tool_call_count": 0, "tool_names": []}
    started_at = str(runtime_info.get("turn_started_at", ""))
    completed_at = str(runtime_info.get("turn_completed_at", ""))
    return {
        "round_count": _first_int(response_meta.get("round_count"), runtime_info.get("round_count")),
        "tool_call_count": _first_int(
            response_meta.get("tool_call_count"),
            runtime_info.get("tool_call_count"),
            block_summary["tool_call_count"],
        ),
        "tool_names": _first_list(response_meta.get("tool_names"), runtime_info.get("tool_names"), block_summary["tool_names"]),
        "delegation_count": _first_int(response_meta.get("delegation_count"), runtime_info.get("delegation_count")),
        "delegated_agents": _first_list(response_meta.get("delegated_agents"), runtime_info.get("delegated_agents")),
        "duration_ms": _first_int(response_meta.get("duration_ms"), _compute_duration_ms(started_at, completed_at)),
    }


def _first_int(*values: Any) -> int:
    for value in values:
        try:
            normalized = int(value or 0)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            return normalized
    return 0


def _first_list(*values: Any) -> list[str]:
    for value in values:
        if not isinstance(value, list):
            continue
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if normalized:
            return normalized
    return []


def _summarize_tool_blocks(message: Message | None) -> dict[str, Any]:
    tool_names: list[str] = []
    tool_call_count = 0
    for part in (message or {}).get("parts", []):
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        if str(state.get("status", "")).strip() != "requested":
            continue
        tool_call_count += 1
        tool_name = str(part.get("name", "")).strip()
        if tool_name and tool_name not in tool_names:
            tool_names.append(tool_name)
    return {"tool_call_count": tool_call_count, "tool_names": tool_names}


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


def _display_parts_from_message(message: Message, *, attached_tool_results: list[Message] | None = None) -> list[DisplayPartVO]:
    info = message.get("info", {})
    agent = str(info.get("agent", "")).strip()
    created_at = str(info.get("created_at", ""))
    status = str(info.get("status", ""))
    parts: list[DisplayPartVO] = []
    for part in message.get("parts", []):
        part_type = str(part.get("type", "")).strip()
        part_id = str(part.get("part_id", ""))
        part_created_at = str(part.get("created_at", "")) or created_at
        if part_type == "text":
            text = str(part.get("content", ""))
            if text:
                parts.append(
                    DisplayPartVO(
                        id=part_id,
                        kind="assistant_text",
                        title=f"{agent or 'assistant'} 回复",
                        text=text,
                        created_at=part_created_at,
                        agent=agent,
                        agent_kind="primary",
                        round=_first_int(info.get("round_count"), 1),
                        status=status,
                    )
                )
            continue
        if part_type == "tool":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            if str(state.get("status", "")).strip() != "requested":
                continue
            tool_name = str(part.get("name", "")).strip()
            tool_call_id = str(state.get("tool_call_id", "")).strip()
            input_data = state.get("input") if isinstance(state.get("input"), dict) else {}
            parts.append(
                DisplayPartVO(
                    id=part_id,
                    kind="tool_call",
                    title=f"{agent or 'assistant'} 调用工具: {tool_name or 'unknown'}",
                    detail=str(input_data.get("arguments", "{}")),
                    created_at=part_created_at,
                    agent=agent,
                    agent_kind="primary",
                    round=_first_int(info.get("round_count"), 1),
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )
            )
    parts.extend(_display_parts_from_tool_results(attached_tool_results or [], agent=agent, round_no=_first_int(info.get("round_count"), 1)))
    return parts


def _display_parts_from_tool_results(messages: list[Message], *, agent: str, round_no: int) -> list[DisplayPartVO]:
    parts: list[DisplayPartVO] = []
    for message in messages:
        for part in message.get("parts", []):
            if part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            state_status = str(state.get("status", "")).strip()
            if state_status not in {"completed", "failed"}:
                continue
            output = state.get("output") if isinstance(state.get("output"), dict) else {}
            metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
            status = str(metadata.get("status", state_status)).strip() or state_status
            output_text = _stringify_output(output.get("output", ""))
            tool_name = str(part.get("name", "")).strip()
            title = f"{agent or 'assistant'} 工具结果: {tool_name or 'unknown'}"
            parts.append(
                DisplayPartVO(
                    id=str(part.get("part_id", "")),
                    kind="tool_result",
                    title=title,
                    detail=f"{status} {output_text}".strip(),
                    created_at=str(part.get("created_at", "")),
                    agent=agent,
                    agent_kind="primary",
                    round=round_no,
                    status=status,
                    tool_name=tool_name,
                    tool_call_id=str(state.get("tool_call_id", "")),
                )
            )
    return parts


def _stringify_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


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


def message_to_vo(message: Message, *, attached_tool_results: list[Message] | None = None) -> MessageVO:
    # Web 层统一在这里兜底缺省字段，避免路由层重复手工搬运。
    info = message.get("info", {})
    display_parts = _normalize_display_parts(info.get("display_parts"))
    if not display_parts and str(info.get("role", "")) == "assistant":
        display_parts = _display_parts_from_message(message, attached_tool_results=attached_tool_results)
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
        response_meta=_normalize_response_meta(info.get("response_meta"), info=info, message=message),
        process_items=_normalize_process_items(info.get("process_items")),
        display_parts=display_parts,
        confirmation=_normalize_confirmation(info.get("confirmation")),
        question=_normalize_question(info.get("question")),
    )


def messages_to_vos(messages: list[Message]) -> list[MessageVO]:
    tool_results_by_owner: dict[str, list[Message]] = {}
    tool_owner_by_call_id: dict[str, str] = {}
    for message in messages:
        role = str(message.get("info", {}).get("role", ""))
        message_id = str(message.get("info", {}).get("message_id", ""))
        if role == "assistant":
            for part in message.get("parts", []):
                if part.get("type") != "tool":
                    continue
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                if str(state.get("status", "")).strip() == "requested":
                    tool_call_id = str(state.get("tool_call_id", "")).strip()
                    if tool_call_id:
                        tool_owner_by_call_id[tool_call_id] = message_id
            continue
        if role != "tool":
            continue
        for part in message.get("parts", []):
            if part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_call_id = str(state.get("tool_call_id", "")).strip()
            owner_id = tool_owner_by_call_id.get(tool_call_id, "")
            if owner_id:
                tool_results_by_owner.setdefault(owner_id, []).append(message)

    return [
        message_to_vo(
            message,
            attached_tool_results=tool_results_by_owner.get(str(message.get("info", {}).get("message_id", "")), []),
        )
        for message in messages
    ]


def sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_stream_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    event_type = str(event.get("type", "")).strip()
    if not event_type:
        return None
    # SSE 协议把事件名和 data 分开传输，这里只保留真正的 payload 字段。
    payload: dict[str, Any] = {key: value for key, value in event.items() if key != "type"}
    return event_type, payload
