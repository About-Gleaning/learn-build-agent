import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

WORKDIR = Path.cwd()
PLAN_WRITE_ROOT = (WORKDIR / "src" / "storage" / "plan").resolve()
logger = logging.getLogger(__name__)

DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
READ_ONLY_BASH_COMMANDS = {
    "ls",
    "cat",
    "rg",
    "find",
    "head",
    "tail",
    "wc",
    "pwd",
    "sed",
    "sort",
    "uniq",
    "cut",
    "echo",
    "tree",
}
FORBIDDEN_BASH_FRAGMENTS = [";", "&&", "||", "|", ">", "<", "$(", "`"]
DANGEROUS_BASH_ARGS = {"-i", "--in-place", "-exec", "--output"}


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_bash(command: str) -> str:
    if any(pattern in command for pattern in DANGEROUS_PATTERNS):
        return "Error: Dangerous command blocked"

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

    output = (completed.stdout + completed.stderr).strip()
    return output[:50000] if output else "(no output)"


def validate_readonly_bash(command: str) -> str | None:
    if any(fragment in command for fragment in FORBIDDEN_BASH_FRAGMENTS):
        return "Error: plan 模式下 bash 仅允许单条只读命令，禁止重定向、管道和链式执行。"

    try:
        parts = shlex.split(command)
    except Exception as exc:
        return f"Error: bash 命令解析失败: {type(exc).__name__}: {exc}"

    if not parts:
        return "Error: 空命令。"

    base = Path(parts[0]).name
    if base not in READ_ONLY_BASH_COMMANDS:
        return f"Error: plan 模式下不允许执行命令 `{base}`。"

    if any(arg in DANGEROUS_BASH_ARGS for arg in parts[1:]):
        return "Error: plan 模式下检测到潜在写入参数，已拒绝执行。"

    return None


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
    confirmed: bool | None = None,
) -> dict[str, Any]:
    if current_mode == "plan":
        return {
            "title": "已在 plan 模式",
            "output": "当前已处于 plan 模式，无需重复切换。",
            "metadata": {
                "status": "cancelled",
                "target_agent": "plan",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    if confirmed is False:
        return {
            "title": "已取消切换",
            "output": "用户取消进入 plan 模式，保持当前 build 模式。",
            "metadata": {
                "status": "cancelled",
                "target_agent": "build",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    if confirmed is not True:
        return {
            "title": "切换确认",
            "output": "请确认是否切换到 plan 模式。",
            "metadata": {
                "status": "confirmation_required",
                "target_agent": "plan",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": True,
                "confirmation_question": "是否切换到 plan 模式？",
            },
        }

    base_message = "用户请求进入plan模式。切换到plan模式并开始计划。"
    extra = "已有计划文件，去编辑它。" if plan_exists else "还没有，去创建它。"
    synthetic_message = f"{base_message}{extra}"

    return {
        "title": "已切换到 plan 模式",
        "output": synthetic_message,
        "metadata": {
            "status": "switched",
            "target_agent": "plan",
            "plan_path": plan_path,
            "model": latest_model,
            "requires_confirmation": False,
            "synthetic_user_message": synthetic_message,
            "synthetic_agent": "plan",
        },
    }


def run_plan_exit(
    *,
    current_mode: str,
    plan_path: str,
    plan_exists: bool,
    latest_model: str,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    if current_mode != "plan":
        return {
            "title": "当前不在 plan 模式",
            "output": "当前不在 plan 模式，无需退出。",
            "metadata": {
                "status": "cancelled",
                "target_agent": "build",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    if confirmed is False:
        return {
            "title": "已取消切换",
            "output": "用户取消退出 plan 模式，继续留在 plan 模式。",
            "metadata": {
                "status": "cancelled",
                "target_agent": "plan",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": False,
            },
        }

    if confirmed is not True:
        return {
            "title": "切换确认",
            "output": "请确认计划是否已完成并切换到 build 模式。",
            "metadata": {
                "status": "confirmation_required",
                "target_agent": "build",
                "plan_path": plan_path,
                "model": latest_model,
                "requires_confirmation": True,
                "confirmation_question": "计划是否已完成，并切换到 build 模式？",
            },
        }

    base_message = "计划已被批准，您现在可以编辑文件了。执行计划。"
    extra = f"计划文件在 {plan_path}，按它执行。" if plan_exists else ""
    synthetic_message = f"{base_message}{extra}"

    return {
        "title": "已切换到 build 模式",
        "output": synthetic_message,
        "metadata": {
            "status": "switched",
            "target_agent": "build",
            "plan_path": plan_path,
            "model": latest_model,
            "requires_confirmation": False,
            "synthetic_user_message": synthetic_message,
            "synthetic_agent": "build",
        },
    }
