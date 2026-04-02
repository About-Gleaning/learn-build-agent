from __future__ import annotations

from pathlib import Path
from typing import Any

from ..lsp import collect_file_diagnostics
from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path, get_workspace
from .file_edit_state import get_file_state, record_file_edit
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_or_skills_path


def _resolve_write_target(file_path: str) -> Path:
    raw_path = Path(file_path).expanduser()
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


def _build_success_result(target: Path, *, existed_before: bool, content: str) -> dict[str, Any]:
    title = _build_write_title(target)
    diagnostics_result = collect_file_diagnostics(file_path=target, content=content)
    output = f"写入成功：{title}。"
    output += diagnostics_result.build_llm_excerpt()
    return {
        "title": title,
        "output": output,
        "metadata": {
            "status": "completed",
            "filepath": str(target),
            "exists": existed_before,
            **diagnostics_result.to_metadata(),
        },
    }


def _validate_existing_file_write_state(target: Path) -> dict[str, Any] | None:
    state = get_file_state(target)
    if state is None:
        return build_tool_failure(
            f"Error: 覆盖写入前必须先使用 read_file 读取 {target}",
            error_code="write_read_required",
        )
    current_mtime_ns = target.stat().st_mtime_ns
    if current_mtime_ns != state.read_mtime_ns or state.last_read_at_ns <= state.last_edit_at_ns:
        return build_tool_failure(
            f"Error: 文件 {target} 自最近一次读取后已发生变化，请重新执行 read_file。",
            error_code="write_stale_read",
        )
    return None


def run_write(file_path: str, content: str) -> dict[str, Any]:
    try:
        target = _resolve_write_target(file_path)
        if target.exists() and target.is_dir():
            return build_tool_failure(
                f"Error: 路径是目录，无法写入: {target}",
                error_code="write_path_is_directory",
            )

        existed_before = target.exists()
        if existed_before:
            write_guard_failure = _validate_existing_file_write_state(target)
            if write_guard_failure is not None:
                return write_guard_failure

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        record_file_edit(target, mtime_ns=target.stat().st_mtime_ns)
        return _build_success_result(target, existed_before=existed_before, content=content)
    except ValueError as exc:
        message = str(exc)
        error_code = "write_path_forbidden" if "超出允许范围" in message else "write_failed"
        return build_tool_failure(
            f"Error: {message}",
            error_code=error_code,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return build_tool_failure(
            f"Error: {exc}",
            error_code="write_failed",
            error_type=type(exc).__name__,
        )
