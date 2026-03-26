from pathlib import Path
from typing import Any

from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path
from .path_utils import resolve_workspace_path


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
    plan_path = build_plan_storage_path(get_session_id())
    raw_path = Path(path_str).expanduser()
    if raw_path.is_absolute() and raw_path.resolve() == plan_path:
        return plan_path
    try:
        return resolve_workspace_path(path_str)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {path_str}") from exc


def is_allowed_plan_write_path(path: str) -> bool:
    try:
        target = safe_path(path)
    except Exception:
        return False
    return target == build_plan_storage_path(get_session_id())


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

