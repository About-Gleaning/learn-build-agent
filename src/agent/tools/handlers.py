import base64
from pathlib import Path
from typing import Any

from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path
from ..runtime.workspace import get_workspace


def build_tool_success(output: str, **metadata: Any) -> dict[str, Any]:
    """统一构造成功结果，便于工具执行层稳定读取 metadata。"""
    return {
        "output": output,
        "metadata": {
            "status": "completed",
            **metadata,
        },
    }


def build_tool_failure(output: str, *, error_code: str, **metadata: Any) -> dict[str, Any]:
    """统一构造失败结果，保留原有输出文本以兼容模型侧提示。"""
    return {
        "output": output,
        "metadata": {
            "status": "failed",
            "error_code": error_code,
            **metadata,
        },
    }


def safe_path(path_str: str) -> Path:
    workspace_root = get_workspace().root
    plan_path = build_plan_storage_path(get_session_id())
    raw_path = Path(path_str).expanduser()
    path = raw_path.resolve() if raw_path.is_absolute() else (workspace_root / path_str).resolve()
    if not path.is_relative_to(workspace_root) and path != plan_path:
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_read(path: str, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
    try:
        target = safe_path(path)
        if target.suffix.lower() == ".pdf":
            pdf_bytes = target.read_bytes()
            pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
            # OpenAI 官方文档当前说明：单个文件小于 50 MB。
            if len(pdf_bytes) >= 50 * 1024 * 1024:
                return build_tool_failure(
                    "Error: PDF file is too large for OpenAI Responses inline file input.",
                    error_code="pdf_file_too_large",
                    file_type="pdf",
                    filename=target.name,
                    path=path,
                    size_bytes=len(pdf_bytes),
                    base64_size=len(pdf_base64),
                )

            return {
                "output": "PDF read successfully",
                "metadata": {
                    "status": "completed",
                    "path": path,
                    "file_type": "pdf",
                    "filename": target.name,
                    "size_bytes": len(pdf_bytes),
                    "encoding": "base64",
                    "paging_ignored": limit is not None or offset != 0,
                },
                "attachments": [
                    {
                        "type": "file",
                        "mime": "application/pdf",
                        "url": f"data:application/pdf;base64,{pdf_base64}",
                    }
                ],
            }

        text = target.read_text()
        lines = text.splitlines()
        start = max(offset, 0)
        selected = lines[start:]
        if limit is not None and limit < len(selected):
            selected = selected[:limit] + [f"... ({len(lines) - start - limit} more lines)"]
        return build_tool_success("\n".join(selected)[:50000], path=path)
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="read_failed", error_type=type(exc).__name__)


def is_allowed_plan_write_path(path: str) -> bool:
    try:
        target = safe_path(path)
    except Exception:
        return False
    return target == build_plan_storage_path(get_session_id())


def run_write(path: str, content: str) -> dict[str, Any]:
    try:
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return build_tool_success(f"Wrote {len(content)} bytes to {path}")
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="write_failed", error_type=type(exc).__name__)


def run_edit(path: str, old_text: str, new_text: str) -> dict[str, Any]:
    try:
        target = safe_path(path)
        content = target.read_text()
        if old_text not in content:
            return build_tool_failure(f"Error: Text not found in {path}", error_code="text_not_found")

        target.write_text(content.replace(old_text, new_text, 1))
        return build_tool_success(f"Edited {path}")
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="edit_failed", error_type=type(exc).__name__)


def build_plan_placeholder_path(session_id: str) -> Path:
    return build_plan_storage_path(session_id)


def run_plan_enter(
    *,
    current_mode: str,
    plan_path: str,
    plan_exists: bool,
    latest_model: str,
) -> dict[str, Any]:
    if current_mode == "plan":
        return {
            "title": "已在 plan 模式",
            "output": "当前已处于 plan 模式，无需重复切换。",
            "metadata": {
                "status": "completed",
                "target_agent": "plan",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    return {
        "title": "等待确认",
        "output": "等待用户确认是否切换到 plan 模式。",
        "metadata": {
            "status": "confirmation_required",
            "target_agent": "plan",
            "current_agent": "build",
            "plan_path": plan_path,
            "model": latest_model,
            "plan_exists": plan_exists,
            "requires_confirmation": True,
            "confirmation_question": "是否切换到 plan 模式？",
            "action_type": "enter_plan",
        },
    }


def run_plan_exit(
    *,
    current_mode: str,
    plan_path: str,
    plan_exists: bool,
    latest_model: str,
) -> dict[str, Any]:
    if current_mode != "plan":
        return {
            "title": "当前不在 plan 模式",
            "output": "当前不在 plan 模式，无需退出。",
            "metadata": {
                "status": "completed",
                "target_agent": "build",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    return {
        "title": "等待确认",
        "output": "等待用户确认计划是否已完成，并切换到 build 模式。",
        "metadata": {
            "status": "confirmation_required",
            "target_agent": "build",
            "current_agent": "plan",
            "plan_path": plan_path,
            "model": latest_model,
            "plan_exists": plan_exists,
            "requires_confirmation": True,
            "confirmation_question": "计划是否已完成，并切换到 build 模式？",
            "action_type": "exit_plan",
        },
    }
