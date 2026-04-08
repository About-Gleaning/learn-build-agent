from __future__ import annotations

from pathlib import Path
from typing import Any

from ..lsp import collect_file_diagnostics
from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path, get_workspace
from .file_edit_state import record_file_edit
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_or_skills_path


class FileToolError(Exception):
    """表示 agent 可通过调整参数自行修复的文件工具业务错误。"""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def _resolve_write_target(file_path: str) -> Path:
    raw_path = Path(file_path).expanduser()
    if not raw_path.is_absolute():
        raise FileToolError(
            "write_file 只接受绝对路径。请先将目标路径转换为绝对路径后重试。",
            error_code="write_path_not_absolute",
        )
    plan_path = build_plan_storage_path(get_session_id())
    if raw_path.is_absolute() and raw_path.resolve() == plan_path:
        return plan_path
    return resolve_workspace_or_skills_path(file_path)


def _build_write_title(target: Path) -> str:
    workspace = get_workspace()
    for base in (workspace.root.resolve(), workspace.skills_dir.resolve()):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return str(target)


def _build_success_result(target: Path, *, content: str) -> dict[str, Any]:
    title = _build_write_title(target)
    diagnostics_result = collect_file_diagnostics(file_path=target, content=content)
    bytes_written = len(content.encode("utf-8"))
    message = f"创建成功：{title}，共写入 {bytes_written} 字节。"
    output = message
    output += diagnostics_result.build_llm_excerpt()
    return {
        "success": True,
        "filePath": str(target),
        "bytesWritten": bytes_written,
        "message": message,
        "title": title,
        "output": output,
        "metadata": {
            "status": "completed",
            "filepath": str(target),
            "exists": False,
            "success": True,
            "filePath": str(target),
            "bytesWritten": bytes_written,
            "message": message,
            **diagnostics_result.to_metadata(),
        },
    }


def run_write(file_path: str, content: str) -> dict[str, Any]:
    try:
        target = _resolve_write_target(file_path)
        if target.exists() and target.is_dir():
            raise FileToolError(
                f"目标路径是目录，无法创建文件：{target}。请改用文件绝对路径。",
                error_code="write_path_is_directory",
            )
        if target.exists():
            raise FileToolError(
                f"文件已存在：{target}。write_file 只能创建新文件，请改用 edit_file 编辑已有文件。",
                error_code="write_file_exists",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        record_file_edit(target, mtime_ns=target.stat().st_mtime_ns)
        return _build_success_result(target, content=content)
    except FileToolError as exc:
        return build_tool_failure(
            f"Error: {exc.message}",
            error_code=exc.error_code,
            error_type=type(exc).__name__,
            success=False,
            filePath=file_path,
            error=exc.message,
            message=exc.message,
        )
    except ValueError as exc:
        message = str(exc)
        error_code = "write_path_forbidden" if "超出允许范围" in message else "write_failed"
        return build_tool_failure(
            f"Error: {message}",
            error_code=error_code,
            error_type=type(exc).__name__,
            success=False,
            filePath=file_path,
            error=message,
            message=message,
        )
    except Exception as exc:
        return build_tool_failure(
            f"Error: 系统异常：{exc}。请检查路径、权限或重试；若仍失败，请重新读取文件上下文后再操作。",
            error_code="write_failed",
            error_type=type(exc).__name__,
            success=False,
            filePath=file_path,
            error=str(exc),
            message=(
                f"系统异常：{exc}。请检查路径、权限或重试；若仍失败，请重新读取文件上下文后再操作。"
            ),
        )
