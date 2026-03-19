import json
import uuid
from datetime import datetime
from typing import Any

from ..core.message import DisplayPart, Message, ProcessItem, ResponseMeta, utc_now_iso
from .agents import get_agent


def _new_stream_event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _resolve_agent_kind(agent_name: str) -> str:
    definition = get_agent(agent_name)
    if definition is None:
        return "primary"
    return definition.model


def _build_stream_event(
    event_type: str,
    *,
    session_id: str,
    agent: str,
    agent_kind: str,
    depth: int,
    delegation_id: str | None = None,
    parent_tool_call_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type,
        "event_id": _new_stream_event_id(),
        "timestamp": utc_now_iso(),
        "session_id": session_id,
        "agent": agent,
        "agent_kind": agent_kind,
        "depth": depth,
    }
    if delegation_id:
        event["delegation_id"] = delegation_id
    if parent_tool_call_id:
        event["parent_tool_call_id"] = parent_tool_call_id
    event.update(payload)
    return event


def _describe_runtime(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "")).strip()
    agent = str(payload.get("agent", "")).strip()
    provider = str(payload.get("provider", "")).strip()
    model = str(payload.get("model", "")).strip()
    tags = [mode or agent, provider, model]
    return " / ".join([tag for tag in tags if tag])


def _describe_agent(payload: dict[str, Any]) -> str:
    agent = str(payload.get("agent", "unknown")).strip() or "unknown"
    agent_kind = "子代理" if str(payload.get("agent_kind", "primary")).strip() == "subagent" else "主代理"
    return f"{agent_kind} · {agent}"


def _build_process_item(event: dict[str, Any]) -> ProcessItem | None:
    event_type = str(event.get("type", "")).strip()
    if not event_type or event_type == "text_delta":
        return None

    payload = dict(event)
    created_at = str(payload.get("timestamp", "")).strip() or utc_now_iso()
    agent = str(payload.get("agent", "unknown")).strip() or "unknown"
    agent_kind = str(payload.get("agent_kind", "primary")).strip() or "primary"
    depth = int(payload.get("depth", 0) or 0)
    round_no = int(payload.get("round", 0) or 0)
    delegation_id = str(payload.get("delegation_id", "")).strip()
    parent_tool_call_id = str(payload.get("parent_tool_call_id", "")).strip()
    status = str(payload.get("status", "")).strip()
    tool_name = str(payload.get("name", "")).strip()
    tool_call_id = str(payload.get("tool_call_id", "")).strip()

    title = f"{agent} 事件: {event_type}"
    detail = json.dumps(payload, ensure_ascii=False)
    if event_type == "start":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 会话开始"
        detail = f"{_describe_agent(payload)}{f' · {runtime_desc}' if runtime_desc else ''}"
    elif event_type == "round_start":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 第 {round_no} 轮开始"
        detail = runtime_desc or _describe_agent(payload)
    elif event_type == "tool_call":
        title = f"{agent} 调用工具: {tool_name or 'unknown'}"
        detail = str(payload.get("arguments", "{}"))
    elif event_type == "tool_result":
        title = f"{agent} 工具结果: {tool_name or 'unknown'}"
        if tool_name == "task":
            title = f"{agent} 委派结果"
        detail = f"{status or 'completed'} {str(payload.get('output_preview', '')).strip()}".strip()
    elif event_type == "round_end":
        title = f"{agent} 第 {round_no} 轮结束"
        detail = f"状态: {status or 'completed'}"
    elif event_type == "done":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 会话完成"
        detail = f"{status or 'completed'} {runtime_desc}".strip()
    elif event_type == "error":
        title = f"{agent} 会话异常"
        detail = str(payload.get("message", "未知错误"))

    return {
        "id": str(payload.get("event_id", "")),
        "kind": event_type,
        "title": title,
        "detail": detail,
        "created_at": created_at,
        "agent": agent,
        "agent_kind": agent_kind,
        "depth": depth,
        "round": round_no,
        "status": status,
        "delegation_id": delegation_id,
        "parent_tool_call_id": parent_tool_call_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
    }


def _should_hide_display_event(event_type: str) -> bool:
    return event_type in {"start", "round_start", "round_end", "done"}


def _build_display_part_from_event(event: dict[str, Any]) -> DisplayPart | None:
    process_item = _build_process_item(event)
    if process_item is None:
        return None

    if _should_hide_display_event(str(process_item.get("kind", "")).strip()):
        return None

    return {
        "id": str(process_item.get("id", "")),
        "kind": str(process_item.get("kind", "")),
        "title": str(process_item.get("title", "")),
        "detail": str(process_item.get("detail", "")),
        "text": "",
        "created_at": str(process_item.get("created_at", "")),
        "agent": str(process_item.get("agent", "")),
        "agent_kind": str(process_item.get("agent_kind", "")),
        "depth": int(process_item.get("depth", 0) or 0),
        "round": int(process_item.get("round", 0) or 0),
        "status": str(process_item.get("status", "")),
        "delegation_id": str(process_item.get("delegation_id", "")),
        "parent_tool_call_id": str(process_item.get("parent_tool_call_id", "")),
        "tool_name": str(process_item.get("tool_name", "")),
        "tool_call_id": str(process_item.get("tool_call_id", "")),
    }


def _append_display_text_part(
    display_parts: list[DisplayPart],
    *,
    delta: str,
    created_at: str,
    agent: str,
    agent_kind: str,
    depth: int,
    round_no: int,
    delegation_id: str | None,
    parent_tool_call_id: str | None,
    merge_allowed: bool,
) -> None:
    if not delta:
        return

    if merge_allowed and display_parts:
        last_part = display_parts[-1]
        if (
            str(last_part.get("kind", "")) == "assistant_text"
            and str(last_part.get("agent", "")) == agent
            and str(last_part.get("agent_kind", "")) == agent_kind
            and int(last_part.get("depth", 0) or 0) == depth
            and int(last_part.get("round", 0) or 0) == round_no
            and str(last_part.get("delegation_id", "")) == str(delegation_id or "")
            and str(last_part.get("parent_tool_call_id", "")) == str(parent_tool_call_id or "")
        ):
            last_part["text"] = f"{str(last_part.get('text', ''))}{delta}"
            return

    display_parts.append(
        {
            "id": _new_stream_event_id(),
            "kind": "assistant_text",
            "title": f"{agent} 回复",
            "detail": "",
            "text": delta,
            "created_at": created_at,
            "agent": agent,
            "agent_kind": agent_kind,
            "depth": depth,
            "round": round_no,
            "status": "completed",
            "delegation_id": str(delegation_id or ""),
            "parent_tool_call_id": str(parent_tool_call_id or ""),
            "tool_name": "",
            "tool_call_id": "",
        }
    )


def _append_display_event_part(
    display_parts: list[DisplayPart],
    *,
    event: dict[str, Any],
) -> None:
    display_part = _build_display_part_from_event(event)
    if display_part is None:
        return
    display_parts.append(display_part)


def _build_display_parts_from_message(message: Message) -> list[DisplayPart]:
    info = message.get("info", {})
    agent = str(info.get("agent", "")).strip()
    created_at = str(info.get("created_at", "")).strip() or utc_now_iso()
    parts: list[DisplayPart] = []
    for part in message.get("parts", []):
        part_type = str(part.get("type", "")).strip()
        if part_type not in {"text", "compaction", "compact_summary", "reasoning", "error"}:
            continue
        content = str(part.get("content", ""))
        if not content:
            continue
        parts.append(
            {
                "id": str(part.get("part_id", "")) or _new_stream_event_id(),
                "kind": "assistant_text" if part_type != "error" else "error",
                "title": f"{agent or 'assistant'} 回复" if part_type != "error" else f"{agent or 'assistant'} 会话异常",
                "detail": "" if part_type != "error" else content,
                "text": content if part_type != "error" else "",
                "created_at": str(part.get("created_at", "")) or created_at,
                "agent": agent,
                "agent_kind": "primary",
                "depth": 0,
                "round": 0,
                "status": str(info.get("status", "")),
                "delegation_id": "",
                "parent_tool_call_id": "",
                "tool_name": "",
                "tool_call_id": "",
            }
        )
    return parts


def _merge_display_parts_with_message(display_parts: list[DisplayPart], message: Message) -> list[DisplayPart]:
    merged = [dict(item) for item in display_parts]
    if not merged:
        return _build_display_parts_from_message(message)
    fallback_parts = _build_display_parts_from_message(message)
    if not fallback_parts:
        return merged

    existing_text_keys = {
        (
            str(item.get("kind", "")),
            str(item.get("text", "")),
            str(item.get("detail", "")),
            str(item.get("agent", "")),
        )
        for item in merged
        if str(item.get("kind", "")) in {"assistant_text", "error"}
    }
    for fallback_part in fallback_parts:
        fallback_key = (
            str(fallback_part.get("kind", "")),
            str(fallback_part.get("text", "")),
            str(fallback_part.get("detail", "")),
            str(fallback_part.get("agent", "")),
        )
        if fallback_key in existing_text_keys:
            continue
        merged.append(fallback_part)
        existing_text_keys.add(fallback_key)
    return merged


def _compute_duration_ms(started_at: str, completed_at: str) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        start_dt = datetime.fromisoformat(started_at)
        completed_dt = datetime.fromisoformat(completed_at)
    except ValueError:
        return 0
    duration = int((completed_dt - start_dt).total_seconds() * 1000)
    return max(duration, 0)


def _build_response_meta(process_items: list[ProcessItem], *, turn_started_at: str, turn_completed_at: str) -> ResponseMeta:
    tool_names: list[str] = []
    delegated_agents: list[str] = []
    delegation_ids: set[str] = set()
    round_count = 0
    tool_call_count = 0

    for item in process_items:
        kind = str(item.get("kind", "")).strip()
        if kind == "round_start":
            round_count += 1
        if kind == "tool_call":
            tool_call_count += 1
            tool_name = str(item.get("tool_name", "")).strip()
            if tool_name and tool_name not in tool_names:
                tool_names.append(tool_name)
        if kind == "start" and str(item.get("agent_kind", "primary")).strip() == "subagent":
            delegated_agent = str(item.get("agent", "")).strip()
            if delegated_agent and delegated_agent not in delegated_agents:
                delegated_agents.append(delegated_agent)
        delegation_id = str(item.get("delegation_id", "")).strip()
        if delegation_id:
            delegation_ids.add(delegation_id)

    return {
        "round_count": round_count,
        "tool_call_count": tool_call_count,
        "tool_names": tool_names,
        "delegation_count": len(delegation_ids),
        "delegated_agents": delegated_agents,
        "duration_ms": _compute_duration_ms(turn_started_at, turn_completed_at),
    }


def _attach_response_summary(
    message: Message,
    *,
    process_items: list[ProcessItem],
    display_parts: list[DisplayPart],
    turn_started_at: str,
    turn_completed_at: str,
) -> ResponseMeta:
    response_meta = _build_response_meta(process_items, turn_started_at=turn_started_at, turn_completed_at=turn_completed_at)
    message["info"]["process_items"] = [dict(item) for item in process_items]
    message["info"]["display_parts"] = _merge_display_parts_with_message(display_parts, message)
    message["info"]["response_meta"] = dict(response_meta)
    return response_meta
