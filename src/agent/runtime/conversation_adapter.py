from __future__ import annotations

import json
import re
from typing import Any

from ..core.message import (
    Message,
    append_text_part,
    append_tool_part,
    create_message,
    get_message_text,
    get_role,
    mark_message_completed,
)
from .conversation import ConversationMessage

MAX_PERSISTED_TEXT_CHARS = 200_000
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
]
_SECRET_KEYWORDS = {"api_key", "apikey", "access_token", "authorization", "cookie", "token", "secret", "password"}


def message_to_conversation_message(message: Message) -> ConversationMessage:
    role = get_role(message)
    blocks: list[dict[str, Any]] = []
    for part in message.get("parts", []):
        part_type = str(part.get("type", "")).strip()
        if part_type in {"text", "error", "compaction", "compact_summary"}:
            text = _truncate_text(_sanitize_text(str(part.get("content", ""))))
            if text:
                block = {"type": "text", "text": text}
                meta = part.get("meta")
                if isinstance(meta, dict) and meta:
                    block["meta"] = _sanitize_value(meta)
                blocks.append(block)
            continue
        if part_type != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(state.get("status", "")).strip().lower()
        tool_call_id = str(state.get("tool_call_id", ""))
        tool_name = str(part.get("name", ""))
        if status == "requested":
            input_data = state.get("input") if isinstance(state.get("input"), dict) else {}
            arguments = _truncate_text(_sanitize_text(str(input_data.get("arguments", "{}"))))
            blocks.append({"type": "tool_use", "id": tool_call_id, "name": tool_name, "input": arguments})
            continue
        if status in {"completed", "failed"}:
            output = state.get("output") if isinstance(state.get("output"), dict) else {}
            metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
            raw_output = output.get("output", "")
            text = _truncate_text(_sanitize_text(_stringify(raw_output)))
            is_error = status == "failed" or str(metadata.get("status", "")).strip().lower() == "failed"
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "tool_name": tool_name,
                    "output": text,
                    "is_error": is_error,
                }
            )

    if not blocks:
        text = _truncate_text(_sanitize_text(get_message_text(message)))
        blocks.append({"type": "text", "text": text})

    usage = _convert_usage(message.get("info", {}).get("token_usage"))
    return ConversationMessage(
        role=role,
        blocks=blocks,
        usage=usage,
        meta=_build_persisted_meta(message, blocks),
    )


def conversation_message_to_runtime_message(message: ConversationMessage, session_id: str) -> Message:
    meta = dict(message.meta or {})
    role = message.role
    runtime = create_message(
        role,
        session_id,
        model=str(meta.get("model", "")),
        provider=str(meta.get("provider", "")),
        status=str(meta.get("status", "completed")) or "completed",  # type: ignore[arg-type]
        finish_reason=str(meta.get("finish_reason", "")),
        parent_id=str(meta.get("parent_id", "")),
        trace_id=str(meta.get("trace_id", "")),
    )
    info = runtime["info"]
    for key, value in meta.items():
        if key in {"role", "session_id"}:
            continue
        info[key] = value
    info["role"] = role
    info["session_id"] = session_id
    if message.usage:
        info["token_usage"] = {
            "prompt_tokens": int(message.usage.get("input_tokens", message.usage.get("prompt_tokens", 0)) or 0),
            "completion_tokens": int(message.usage.get("output_tokens", message.usage.get("completion_tokens", 0)) or 0),
            "total_tokens": int(message.usage.get("total_tokens", 0) or 0),
        }

    for block in message.blocks:
        block_type = block.get("type")
        if block_type == "text":
            meta = block.get("meta") if isinstance(block.get("meta"), dict) else None
            append_text_part(runtime, str(block.get("text", "")), meta=meta)
            continue
        if block_type == "tool_use":
            append_tool_part(
                runtime,
                tool_call_id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                status="requested",
                arguments=str(block.get("input", "{}")),
            )
            continue
        if block_type == "tool_result":
            append_tool_part(
                runtime,
                tool_call_id=str(block.get("tool_use_id", "")),
                name=str(block.get("tool_name", "")),
                status="failed" if bool(block.get("is_error")) else "completed",
                output={
                    "output": str(block.get("output", "")),
                    "metadata": {"status": "failed" if bool(block.get("is_error")) else "completed"},
                },
            )
    if not str(info.get("status", "")).strip():
        mark_message_completed(runtime)
    return runtime


def runtime_messages_to_conversation_messages(messages: list[Message]) -> list[ConversationMessage]:
    converted = _replace_compaction_checkpoint(messages)
    return [message_to_conversation_message(message) for message in converted]


def detect_compaction_record(messages: list[Message]) -> dict[str, Any] | None:
    for index, message in enumerate(messages):
        info = message.get("info", {})
        if get_role(message) != "assistant" or not bool(info.get("summary")):
            continue
        summary = get_message_text(message).strip()
        if not summary:
            continue
        removed = max(0, index - 1)
        return {"type": "compaction", "count": 1, "removed_message_count": removed, "summary": summary}
    return None


def _replace_compaction_checkpoint(messages: list[Message]) -> list[Message]:
    for index, message in enumerate(messages):
        info = message.get("info", {})
        if get_role(message) != "assistant" or not bool(info.get("summary")):
            continue
        summary = get_message_text(message).strip()
        if not summary:
            continue
        session_id = str(info.get("session_id", "")).strip()
        system = create_message("system", session_id, status="completed", finish_reason="stop")
        system["info"]["summary"] = True
        append_text_part(system, summary)
        return [system, *messages[index + 1 :]]
    return messages


def _convert_usage(raw_usage: Any) -> dict[str, int] | None:
    if not isinstance(raw_usage, dict):
        return None
    input_tokens = int(raw_usage.get("input_tokens", raw_usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(raw_usage.get("output_tokens", raw_usage.get("completion_tokens", 0)) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": int(raw_usage.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": int(raw_usage.get("cache_read_input_tokens", 0) or 0),
        "total_tokens": int(raw_usage.get("total_tokens", input_tokens + output_tokens) or 0),
    }


def _build_persisted_meta(message: Message, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """压缩落库 meta：保留摘要事实，剥离可由 blocks 重建的展示投影。"""

    raw_info = dict(message.get("info", {}))
    response_meta = raw_info.get("response_meta") if isinstance(raw_info.get("response_meta"), dict) else {}
    process_items = raw_info.get("process_items") if isinstance(raw_info.get("process_items"), list) else []
    block_summary = _summarize_blocks(blocks)
    process_summary = _summarize_process_items(process_items)

    persisted = {
        key: value
        for key, value in raw_info.items()
        if key not in {"response_meta", "process_items", "display_parts"}
    }
    persisted["status"] = _normalize_persisted_status(message, blocks, raw_status=persisted.get("status"))
    persisted["round_count"] = _first_int(persisted.get("round_count"), response_meta.get("round_count"), process_summary["round_count"])
    persisted["tool_call_count"] = _first_int(
        persisted.get("tool_call_count"),
        response_meta.get("tool_call_count"),
        process_summary["tool_call_count"],
        block_summary["tool_call_count"],
    )
    persisted["tool_names"] = _first_list(
        persisted.get("tool_names"),
        response_meta.get("tool_names"),
        process_summary["tool_names"],
        block_summary["tool_names"],
    )
    persisted["delegation_count"] = _first_int(
        persisted.get("delegation_count"),
        response_meta.get("delegation_count"),
        process_summary["delegation_count"],
    )
    persisted["delegated_agents"] = _first_list(
        persisted.get("delegated_agents"),
        response_meta.get("delegated_agents"),
        process_summary["delegated_agents"],
    )
    return _sanitize_value(persisted)


def _normalize_persisted_status(message: Message, blocks: list[dict[str, Any]], *, raw_status: Any) -> str:
    """仅归一化落库状态，避免已完成历史在 Web 展示中仍被标成 pending。"""

    status = str(raw_status or "").strip().lower()
    if status in {"completed", "failed", "interrupted"}:
        return status
    if status not in {"", "pending", "running"}:
        return status

    role = get_role(message)
    info = message.get("info", {})
    if str(info.get("finish_reason", "")).strip():
        return "completed"
    if role in {"user", "tool"}:
        return "completed"
    if role == "system" and bool(info.get("summary")):
        return "completed"
    if role == "assistant" and _has_completed_assistant_evidence(blocks):
        return "completed"
    return status or "pending"


def _has_completed_assistant_evidence(blocks: list[dict[str, Any]]) -> bool:
    for block in blocks:
        block_type = str(block.get("type", "")).strip()
        if block_type in {"tool_use", "tool_result"}:
            return True
        if block_type == "text" and str(block.get("text", "")).strip():
            return True
    return False


def _summarize_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    tool_names: list[str] = []
    tool_call_count = 0
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        tool_call_count += 1
        tool_name = str(block.get("name", "")).strip()
        if tool_name and tool_name not in tool_names:
            tool_names.append(tool_name)
    return {"tool_call_count": tool_call_count, "tool_names": tool_names}


def _summarize_process_items(process_items: list[Any]) -> dict[str, Any]:
    tool_names: list[str] = []
    delegated_agents: list[str] = []
    delegation_ids: set[str] = set()
    round_count = 0
    tool_call_count = 0
    for item in process_items:
        if not isinstance(item, dict):
            continue
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


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.strip().lower() in _SECRET_KEYWORDS:
                sanitized[key_text] = "[MASKED]"
            else:
                sanitized[key_text] = _sanitize_value(item)
        return sanitized
    return value


def _sanitize_text(text: str) -> str:
    sanitized = text
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[MASKED]", sanitized)
    return sanitized


def _truncate_text(text: str) -> str:
    if len(text) <= MAX_PERSISTED_TEXT_CHARS:
        return text
    return text[:MAX_PERSISTED_TEXT_CHARS] + "\n...[内容过长，已截断后落库]"


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
