import base64
from pathlib import Path

from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path, build_session_storage_name, get_workspace
from .file_edit_state import record_file_read
from .handlers import build_tool_failure, build_tool_success
from .path_utils import resolve_workspace_or_skills_path

MAX_INLINE_PDF_BYTES = 50 * 1024 * 1024
EMPTY_FILE_OUTPUT = "文件存在，但内容为空。"


def _build_current_session_history_path(session_id: str) -> Path:
    workspace = get_workspace()
    session_name = build_session_storage_name(session_id, suffix=".json")
    return (workspace.sessions_dir / session_name).resolve()


def _build_current_session_tool_output_dir(session_id: str) -> Path:
    workspace = get_workspace()
    session_name = build_session_storage_name(session_id)
    return (workspace.tool_output_root / session_name).resolve()


def resolve_readable_file_path(file_path: str) -> Path:
    raw_path = Path(file_path).expanduser()
    if not raw_path.is_absolute():
        raise ValueError("read_file 仅支持绝对路径 file_path")

    target = raw_path.resolve()
    session_id = get_session_id()
    workspace = get_workspace()
    current_plan_path = build_plan_storage_path(session_id)
    current_history_path = _build_current_session_history_path(session_id)
    current_tool_output_dir = _build_current_session_tool_output_dir(session_id)

    if target == current_plan_path:
        return target
    if target == current_history_path:
        return target
    if target.is_relative_to(current_tool_output_dir):
        return target
    if target.is_relative_to(workspace.skills_dir):
        return target
    if target.is_relative_to(workspace.runtime_home):
        raise ValueError(f"read_file 路径超出允许范围: {file_path}")
    try:
        return resolve_workspace_or_skills_path(file_path)
    except ValueError as exc:
        raise ValueError(f"read_file 路径超出允许范围: {file_path}") from exc


def run_read(file_path: str, limit: int | None = None, offset: int = 0) -> dict[str, object]:
    try:
        target = resolve_readable_file_path(file_path)
        if target.suffix.lower() == ".pdf":
            pdf_bytes = target.read_bytes()
            pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
            # OpenAI 官方文档当前说明：单个文件小于 50 MB。
            if len(pdf_bytes) >= MAX_INLINE_PDF_BYTES:
                return build_tool_failure(
                    "Error: PDF file is too large for OpenAI Responses inline file input.",
                    error_code="pdf_file_too_large",
                    file_type="pdf",
                    filename=target.name,
                    file_path=str(target),
                    size_bytes=len(pdf_bytes),
                    base64_size=len(pdf_base64),
                )

            return {
                "output": "PDF read successfully",
                "metadata": {
                    "status": "completed",
                    "file_path": str(target),
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
        if text == "":
            # 空文件属于成功读取，显式返回提示文案，避免后续链路把空字符串误判成错误或无结果。
            record_file_read(target, mtime_ns=target.stat().st_mtime_ns)
            return build_tool_success(
                EMPTY_FILE_OUTPUT,
                file_path=str(target),
                is_empty=True,
            )

        lines = text.splitlines()
        start = max(offset, 0)
        selected = lines[start:]
        if limit is not None and limit < len(selected):
            selected = selected[:limit] + [f"... ({len(lines) - start - limit} more lines)"]
        record_file_read(target, mtime_ns=target.stat().st_mtime_ns)
        return build_tool_success(
            "\n".join(selected)[:50000],
            file_path=str(target),
            is_empty=False,
        )
    except ValueError as exc:
        error_message = str(exc)
        if "仅支持绝对路径" in error_message:
            return build_tool_failure(
                f"Error: {error_message}",
                error_code="read_path_must_be_absolute",
                error_type=type(exc).__name__,
            )
        return build_tool_failure(
            f"Error: {error_message}",
            error_code="read_path_forbidden",
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="read_failed", error_type=type(exc).__name__)
