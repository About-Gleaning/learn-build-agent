from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..lsp import query_lsp
from ..runtime.workspace import get_workspace
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_path

LSP_OPERATIONS = (
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
)


def _resolve_lsp_target(file_path: str) -> Path:
    return resolve_workspace_path(file_path, allow_missing=False)


def _build_display_path(target: Path) -> str:
    workspace_root = get_workspace().root.resolve()
    try:
        return str(target.resolve().relative_to(workspace_root))
    except ValueError:
        return str(target.resolve())


def _build_title(operation: str, target: Path, line: int, character: int) -> str:
    return f"{operation} {_build_display_path(target)}:{line}:{character}"


def _validate_position(text: str, *, line: int, character: int) -> tuple[int, int]:
    if line < 1 or character < 1:
        raise ValueError("line 和 character 必须从 1 开始计数")
    lines = text.split("\n")
    if line > len(lines):
        raise ValueError(f"line 超出文件范围：{line}")
    max_character = len(lines[line - 1]) + 1
    if character > max_character:
        raise ValueError(f"character 超出当前行范围：{character}")
    return line - 1, character - 1


def _has_results(result: Any) -> bool:
    if result is None:
        return False
    if isinstance(result, (list, dict)):
        return len(result) > 0
    return True


def _build_failed_result(title: str, message: str, *, error_code: str, **metadata: Any) -> dict[str, Any]:
    result = build_tool_failure(message, error_code=error_code, **metadata)
    result["title"] = title
    return result


def run_lsp(operation: str, file_path: str, line: int, character: int) -> dict[str, Any]:
    normalized_operation = str(operation).strip()
    raw_line = int(line)
    raw_character = int(character)
    title = _build_title(normalized_operation or "lsp", Path(file_path).expanduser(), raw_line, raw_character)
    try:
        if normalized_operation not in LSP_OPERATIONS:
            return _build_failed_result(
                title,
                f"Error: 未支持的 LSP operation: {operation}",
                error_code="lsp_operation_invalid",
            )

        target = _resolve_lsp_target(file_path)
        title = _build_title(normalized_operation, target, raw_line, raw_character)
        if target.is_dir():
            return _build_failed_result(
                title,
                f"Error: 路径是目录，无法执行 LSP 查询: {target}",
                error_code="lsp_path_is_directory",
            )

        text = target.read_text(encoding="utf-8")
        zero_based_line, zero_based_character = _validate_position(
            text,
            line=raw_line,
            character=raw_character,
        )
        query_result = query_lsp(
            operation=normalized_operation,
            file_path=target,
            content=text,
            line=zero_based_line,
            character=zero_based_character,
        )
        metadata = {
            "status": "completed",
            **query_result.to_metadata(),
        }
        if query_result.status != "completed":
            error_code = {
                "not_enabled": "lsp_not_enabled",
                "unsupported_language": "lsp_unsupported_language",
                "server_unavailable": "lsp_request_failed",
                "request_failed": "lsp_request_failed",
                "project_import_failed": "lsp_request_failed",
                "operation_unsupported": "lsp_operation_unsupported",
            }.get(query_result.status, "lsp_request_failed")
            failed = _build_failed_result(
                title,
                f"Error: {query_result.lsp_error or query_result.status}",
                error_code=error_code,
                **query_result.to_metadata(),
            )
            return failed

        output = (
            json.dumps(query_result.result, ensure_ascii=False, indent=2)
            if _has_results(query_result.result)
            else f"No results found for {normalized_operation}"
        )
        return {
            "title": title,
            "output": output,
            "metadata": metadata,
        }
    except FileNotFoundError as exc:
        return _build_failed_result(
            title,
            f"Error: {exc}",
            error_code="lsp_file_not_found",
            error_type=type(exc).__name__,
        )
    except ValueError as exc:
        message = str(exc)
        if "路径超出工作区范围" in message:
            error_code = "lsp_path_forbidden"
        elif "line" in message or "character" in message:
            error_code = "lsp_position_invalid"
        else:
            error_code = "lsp_failed"
        return _build_failed_result(
            title,
            f"Error: {message}",
            error_code=error_code,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return _build_failed_result(
            title,
            f"Error: {exc}",
            error_code="lsp_failed",
            error_type=type(exc).__name__,
        )
