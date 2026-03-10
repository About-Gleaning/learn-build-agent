import logging
import os
from pathlib import Path
import subprocess
from typing import Any, TypedDict

WORKDIR = Path.cwd()
TODO_DESC_FILE = Path(__file__).with_name("todo_write.txt")
TODO_TOOL_DESCRIPTION = TODO_DESC_FILE.read_text().strip()
logger = logging.getLogger(__name__)


class ToolHookContext(TypedDict, total=False):
    session_id: str
    tool_name: str
    tool_call_id: str
    arguments: str
    parsed_args: dict[str, Any]
    round_no: int
    started_at: float
    duration_ms: int
    result_size: int


class ToolNormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class ToolHook:
    """工具调用 Hook 基类，支持调用前后与异常阶段扩展。"""

    def __init__(self, name: str, fail_fast: bool = False) -> None:
        self.name = name
        self.fail_fast = fail_fast

    def before_call(self, ctx: ToolHookContext) -> None:
        """在工具调用前执行。"""

    def after_call(self, ctx: ToolHookContext, result: str) -> None:
        """在工具调用成功后执行。"""

    def on_error(self, ctx: ToolHookContext, error: Exception, normalized_error: ToolNormalizedError) -> None:
        """在工具调用异常后执行。"""


class ToolLoggingHook(ToolHook):
    """默认工具日志 Hook，记录调用前后与异常关键信息。"""

    def __init__(self, fail_fast: bool = False) -> None:
        super().__init__(name="tool_logging", fail_fast=fail_fast)

    def before_call(self, ctx: ToolHookContext) -> None:
        logger.info(
            "tool.request session_id=%s tool=%s tool_call_id=%s args_size=%d round=%d",
            ctx.get("session_id", ""),
            ctx.get("tool_name", ""),
            ctx.get("tool_call_id", ""),
            len(ctx.get("arguments", "")),
            ctx.get("round_no", 0),
        )

    def after_call(self, ctx: ToolHookContext, result: str) -> None:
        logger.info(
            "tool.response session_id=%s tool=%s tool_call_id=%s duration_ms=%d result_size=%d",
            ctx.get("session_id", ""),
            ctx.get("tool_name", ""),
            ctx.get("tool_call_id", ""),
            ctx.get("duration_ms", 0),
            len(result),
        )

    def on_error(self, ctx: ToolHookContext, error: Exception, normalized_error: ToolNormalizedError) -> None:
        logger.warning(
            "tool.error session_id=%s tool=%s tool_call_id=%s duration_ms=%d error_code=%s error_type=%s",
            ctx.get("session_id", ""),
            ctx.get("tool_name", ""),
            ctx.get("tool_call_id", ""),
            ctx.get("duration_ms", 0),
            normalized_error.get("code", "execution_error"),
            normalized_error.get("details", type(error).__name__),
            exc_info=True,
        )


_GLOBAL_TOOL_HOOKS: list[ToolHook] = []


def register_global_tool_hook(hook: ToolHook) -> None:
    _GLOBAL_TOOL_HOOKS.append(hook)


def clear_global_tool_hooks() -> None:
    _GLOBAL_TOOL_HOOKS.clear()


def get_global_tool_hooks() -> list[ToolHook]:
    return list(_GLOBAL_TOOL_HOOKS)


def normalize_tool_error(exc: Exception, code: str = "execution_error") -> ToolNormalizedError:
    return {
        "code": code,
        "message": str(exc)[:300],
        "details": type(exc).__name__,
    }


def invoke_tool_hook(
    hook: ToolHook,
    stage: str,
    *,
    ctx: ToolHookContext,
    result: str | None = None,
    error: Exception | None = None,
    normalized_error: ToolNormalizedError | None = None,
) -> None:
    try:
        if stage == "before":
            hook.before_call(ctx)
        elif stage == "after" and result is not None:
            hook.after_call(ctx, result)
        elif stage == "error" and error is not None and normalized_error is not None:
            hook.on_error(ctx, error, normalized_error)
    except Exception as hook_exc:
        logger.warning(
            "tool.hook_failed hook=%s stage=%s fail_fast=%s error=%s",
            hook.name,
            stage,
            hook.fail_fast,
            f"{type(hook_exc).__name__}: {hook_exc}",
            exc_info=True,
        )
        if hook.fail_fast:
            raise RuntimeError(f"Hook '{hook.name}' failed at stage '{stage}': {hook_exc}") from hook_exc


def _default_tool_hooks() -> None:
    if not any(isinstance(h, ToolLoggingHook) for h in _GLOBAL_TOOL_HOOKS):
        register_global_tool_hook(ToolLoggingHook())


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


BASE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["path"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": TODO_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                            },
                            "required": ["text", "status", "priority"],
                        },
                    }
                },
                "required": ["todo_list"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "使用这个工具来阅读你的待办事项清单。",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "加载一个或多个 skill 的完整内容。当你需要查看某个 skill 的详细说明时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要加载的 skill 名称列表"
                    }
                },
                "required": ["skill_names"]
            }
        }
    }
]

MAIN_AGENT_TOOL = BASE_TOOL + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "当需要把一个相对独立的复杂任务委托给子代理时调用。子代理拥有全新上下文，不继承当前对话历史，但共享文件系统。",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string", "description": "发给子代理的完整任务说明，包含目标、上下文、约束条件和期望输出。"}},
                "required": ["prompt"],
            }
        }
    },
]

_default_tool_hooks()
