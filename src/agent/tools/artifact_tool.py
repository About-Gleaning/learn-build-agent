from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.message import Message, get_role
from ..runtime.task_artifacts import (
    get_artifacts_dir,
    get_task_facts_path,
    load_task_facts,
)
from .handlers import build_tool_failure, build_tool_success


def _artifact_result_metadata(message: Message) -> dict[str, Any] | None:
    if get_role(message) != "tool":
        return None
    for part in message.get("parts", []):
        if part.get("type") != "tool" or part.get("name") != "read_artifact":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
        if metadata.get("status") == "completed" and metadata.get("artifact_read") is True:
            return metadata
    return None


def _current_artifact_index(session_id: str) -> dict[str, dict[str, Any]]:
    facts = load_task_facts(session_id)
    result: dict[str, dict[str, Any]] = {}
    for item in facts.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("file", "")).strip()
        if filename:
            result[filename] = item
    return result


def _find_read_marker(messages: list[Message], filename: str) -> dict[str, Any] | None:
    for message in reversed(messages):
        metadata = _artifact_result_metadata(message)
        if not metadata:
            continue
        if str(metadata.get("artifact_file", "")).strip() == filename:
            return metadata
    return None


def _is_marker_current(marker: dict[str, Any] | None, fact: dict[str, Any]) -> bool:
    if not marker:
        return False
    fact_hash = str(fact.get("hash", "")).strip()
    fact_version = str(fact.get("version", "")).strip()
    marker_hash = str(marker.get("artifact_hash", "")).strip()
    marker_version = str(marker.get("artifact_version", "")).strip()
    if fact_hash and marker_hash != fact_hash:
        return False
    if fact_version and marker_version != fact_version:
        return False
    return True


def _safe_artifact_path(session_id: str, filename: str) -> Path:
    normalized = (filename or "").strip()
    if not normalized:
        raise ValueError("filename 不能为空")
    if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
        raise ValueError("filename 不能是绝对路径或包含上级目录")
    root = get_artifacts_dir(session_id)
    target = (root / normalized).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("filename 超出任务工件目录")
    return target


def run_list_artifacts(session_id: str, messages: list[Message]) -> dict[str, Any]:
    facts = load_task_facts(session_id)
    rows: list[dict[str, Any]] = []
    for fact in facts.get("artifacts", []):
        if not isinstance(fact, dict):
            continue
        filename = str(fact.get("file", "")).strip()
        if not filename:
            continue
        marker = _find_read_marker(messages, filename)
        read = _is_marker_current(marker, fact)
        rows.append(
            {
                "file": filename,
                "note": str(fact.get("note", "")).strip(),
                "read": read,
                "stale": marker is not None and not read,
            }
        )
    return build_tool_success(json.dumps({"artifacts": rows}, ensure_ascii=False), artifacts=rows)


def run_read_artifact(session_id: str, filename: str) -> dict[str, Any]:
    try:
        target = _safe_artifact_path(session_id, filename)
        if not target.exists() or not target.is_file():
            return build_tool_failure(
                f"Error: 未找到任务工件：{filename}",
                error_code="artifact_not_found",
                artifact_file=filename,
            )
        content = target.read_text(encoding="utf-8")
        facts = _current_artifact_index(session_id)
        fact = facts.get(filename, {})
        return build_tool_success(
            content,
            artifact_read=True,
            artifact_file=filename,
            artifact_hash=str(fact.get("hash", "")).strip(),
            artifact_version=str(fact.get("version", "")).strip(),
            artifact_note=str(fact.get("note", "")).strip(),
        )
    except ValueError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="artifact_path_invalid", artifact_file=filename)
    except Exception as exc:
        return build_tool_failure(
            f"Error: 读取任务工件失败：{exc}",
            error_code="artifact_read_failed",
            error_type=type(exc).__name__,
            artifact_file=filename,
        )


def run_update_artifact(session_id: str, filename: str, content: str) -> dict[str, Any]:
    try:
        target = _safe_artifact_path(session_id, filename)
        if not target.exists() or not target.is_file():
            return build_tool_failure(
                f"Error: 未找到任务工件：{filename}",
                error_code="artifact_not_found",
                artifact_file=filename,
            )
        from ..runtime.task_artifacts import _hash_text, _timestamp, _write_facts

        root = get_artifacts_dir(session_id)
        history_dir = root / ".history"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_target = history_dir / f"{filename}.{_timestamp()}"
        target.replace(history_target)
        target.write_text(content, encoding="utf-8")

        facts = load_task_facts(session_id)
        for item in facts.get("artifacts", []):
            if isinstance(item, dict) and str(item.get("file", "")).strip() == filename:
                item["hash"] = _hash_text(content)
                item["version"] = _timestamp()
        _write_facts(session_id, facts)
        return build_tool_success(
            f"任务工件已更新：{filename}",
            artifact_file=filename,
            history_path=str(history_target),
        )
    except ValueError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="artifact_path_invalid", artifact_file=filename)
    except Exception as exc:
        return build_tool_failure(
            f"Error: 更新任务工件失败：{exc}",
            error_code="artifact_update_failed",
            error_type=type(exc).__name__,
            artifact_file=filename,
        )


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords if keyword)


def _artifact_not_read(required: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "success": False,
        "error": "ARTIFACT_NOT_READ",
        "message": "以下权威资料尚未读取，请先调用 read_artifact 读取后再执行写操作。",
        "required_artifacts": [
            {
                "file": str(item.get("file", "")).strip(),
                "note": str(item.get("note", "")).strip(),
            }
            for item in required
        ],
        "hint": "请依次调用 read_artifact({filename: '...'}) 读取上述文件后重试",
    }
    return build_tool_failure(
        json.dumps(payload, ensure_ascii=False),
        error_code="ARTIFACT_NOT_READ",
        success=False,
        error="ARTIFACT_NOT_READ",
        message=payload["message"],
        required_artifacts=payload["required_artifacts"],
        hint=payload["hint"],
    )


def check_before_write(
    session_id: str,
    related_artifacts: list[str],
    file_path: str,
    content: str,
    messages: list[Message],
) -> dict[str, Any]:
    facts = _current_artifact_index(session_id)
    required: list[dict[str, Any]] = []
    normalized_related = [str(item).strip() for item in related_artifacts if str(item).strip()]

    if normalized_related:
        for filename in normalized_related:
            fact = facts.get(filename, {"file": filename, "note": ""})
            marker = _find_read_marker(messages, filename)
            if not _is_marker_current(marker, fact):
                required.append(fact)
        if required:
            return _artifact_not_read(required)
        return {"success": True}

    search_text = f"{file_path}\n{content}"
    for fact in facts.values():
        triggers = fact.get("triggers") if isinstance(fact.get("triggers"), dict) else {}
        raw_keywords = triggers.get("keywords") if isinstance(triggers, dict) else []
        keywords = [str(item).strip() for item in raw_keywords] if isinstance(raw_keywords, list) else []
        if _contains_any(search_text, keywords):
            required.append(fact)
    if required:
        return _artifact_not_read(required)
    return {"success": True}


def has_task_artifacts(session_id: str) -> bool:
    return get_task_facts_path(session_id).exists()
