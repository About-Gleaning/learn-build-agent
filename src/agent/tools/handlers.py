import logging
from pathlib import Path
from typing import Any

WORKDIR = Path.cwd()
PLAN_WRITE_ROOT = (WORKDIR / "src" / "plan").resolve()
logger = logging.getLogger(__name__)


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_read(path: str, limit: int | None = None, offset: int = 0) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        start = max(offset, 0)
        selected = lines[start:]
        if limit is not None and limit < len(selected):
            selected = selected[:limit] + [f"... ({len(lines) - start - limit} more lines)"]
        return "\n".join(selected)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def is_allowed_plan_write_path(path: str) -> bool:
    try:
        target = safe_path(path)
    except Exception:
        return False
    return target.is_relative_to(PLAN_WRITE_ROOT)


def run_write(path: str, content: str) -> str:
    try:
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        target = safe_path(path)
        content = target.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"

        target.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"


def build_plan_placeholder_path(session_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session_id).strip("._")
    normalized = safe_id or "default_session"
    return (PLAN_WRITE_ROOT / f"{normalized}.md").resolve()


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
