from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..lsp import collect_file_diagnostics
from ..core.context import get_session_id
from ..runtime.workspace import build_plan_storage_path
from .file_edit_state import get_file_state, record_file_edit
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_or_skills_path
from .write_file_tool import FileToolError


@dataclass(frozen=True)
class EditCandidate:
    start: int
    end: int
    matched_text: str
    strategy: str


def _resolve_edit_target(file_path: str) -> Path:
    raw_path = Path(file_path).expanduser()
    if not raw_path.is_absolute():
        raise FileToolError(
            "edit_file 只接受绝对路径。请先将目标路径转换为绝对路径后重试。",
            error_code="edit_path_not_absolute",
        )
    plan_path = build_plan_storage_path(get_session_id())
    if raw_path.is_absolute() and raw_path.resolve() == plan_path:
        return plan_path
    return resolve_workspace_or_skills_path(file_path)


def _detect_binary_file(target: Path) -> bool:
    sample = target.read_bytes()[:4096]
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _find_exact_candidates(content: str, needle: str) -> list[EditCandidate]:
    matches: list[EditCandidate] = []
    start = 0
    while True:
        index = content.find(needle, start)
        if index < 0:
            return matches
        matches.append(EditCandidate(index, index + len(needle), needle, "exact"))
        start = index + len(needle)


def _collect_line_ranges(content: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    cursor = 0
    for line in content.splitlines(keepends=True):
        start = cursor
        cursor += len(line)
        ranges.append((start, cursor, line))
    if not ranges and content == "":
        return []
    if cursor < len(content):
        ranges.append((cursor, len(content), content[cursor:]))
    return ranges


def _find_line_normalized_candidates(content: str, needle: str) -> list[EditCandidate]:
    needle_lines = needle.splitlines()
    if not needle_lines:
        return []
    content_lines = content.splitlines()
    if len(content_lines) < len(needle_lines):
        return []
    line_ranges = _collect_line_ranges(content)
    normalized_needle = [line.rstrip() for line in needle_lines]
    matches: list[EditCandidate] = []
    window_size = len(needle_lines)
    for index in range(len(content_lines) - window_size + 1):
        window_lines = content_lines[index : index + window_size]
        if [line.rstrip() for line in window_lines] != normalized_needle:
            continue
        start = line_ranges[index][0]
        end = line_ranges[index + window_size - 1][1]
        matches.append(EditCandidate(start, end, content[start:end], "rstrip_lines"))
    return matches


def _dedent_lines(text: str) -> str:
    lines = text.splitlines()
    indents = [
        len(line) - len(line.lstrip(" \t"))
        for line in lines
        if line.strip() != ""
    ]
    if not indents:
        return text
    margin = min(indents)
    normalized_lines = [line[margin:] if line.strip() != "" else "" for line in lines]
    return "\n".join(normalized_lines)


def _find_dedent_candidates(content: str, needle: str) -> list[EditCandidate]:
    needle_lines = needle.splitlines()
    if not needle_lines:
        return []
    content_lines = content.splitlines()
    if len(content_lines) < len(needle_lines):
        return []
    line_ranges = _collect_line_ranges(content)
    normalized_needle = _dedent_lines(needle)
    matches: list[EditCandidate] = []
    window_size = len(needle_lines)
    for index in range(len(content_lines) - window_size + 1):
        window_lines = content_lines[index : index + window_size]
        candidate_text = "\n".join(window_lines)
        if _dedent_lines(candidate_text) != normalized_needle:
            continue
        start = line_ranges[index][0]
        end = line_ranges[index + window_size - 1][1]
        matches.append(EditCandidate(start, end, content[start:end], "dedent_lines"))
    return matches


def _find_trimmed_candidates(content: str, needle: str) -> list[EditCandidate]:
    trimmed = needle.strip()
    if not trimmed or trimmed == needle:
        return []
    return [
        EditCandidate(candidate.start, candidate.end, candidate.matched_text, "trimmed_exact")
        for candidate in _find_exact_candidates(content, trimmed)
    ]


def _reindent_like(new_text: str, old_text: str, matched_text: str) -> str:
    old_lines = old_text.splitlines()
    matched_lines = matched_text.splitlines()
    if not old_lines or not matched_lines:
        return new_text

    def _min_indent(lines: list[str]) -> int:
        values = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
        return min(values) if values else 0

    old_margin = _min_indent(old_lines)
    matched_margin = _min_indent(matched_lines)
    delta = max(matched_margin - old_margin, 0)
    indent_prefix = " " * delta
    result_lines: list[str] = []
    for line in new_text.splitlines():
        if line.strip():
            result_lines.append(indent_prefix + line)
        else:
            result_lines.append(line)
    return "\n".join(result_lines)


def _render_replacement(candidate: EditCandidate, *, old_string: str, new_string: str) -> str:
    if candidate.strategy == "dedent_lines":
        return _reindent_like(new_string, old_string, candidate.matched_text)
    return new_string


def _find_candidates(content: str, old_string: str) -> list[EditCandidate]:
    strategies = (
        _find_exact_candidates(content, old_string),
        _find_trimmed_candidates(content, old_string),
        _find_line_normalized_candidates(content, old_string),
        _find_dedent_candidates(content, old_string),
    )
    for matches in strategies:
        if matches:
            return matches
    return []


def _replace_candidates(
    content: str,
    candidates: list[EditCandidate],
    *,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, int]:
    if not candidates:
        raise FileToolError(
            "未找到可替换的文本。可能是空白字符、缩进或文件内容已变化导致，请先重新执行 read_file 后补充更精确的上下文。",
            error_code="edit_text_not_found",
        )
    if not replace_all and len(candidates) != 1:
        raise FileToolError(
            "oldString 匹配到多处内容，当前无法唯一定位。请在 oldString 中额外携带前后 1-2 行上下文，或显式设置 replaceAll=true。",
            error_code="edit_match_not_unique",
        )

    selected = candidates if replace_all else [candidates[0]]
    updated = content
    for candidate in sorted(selected, key=lambda item: item.start, reverse=True):
        replacement = _render_replacement(candidate, old_string=old_string, new_string=new_string)
        updated = updated[: candidate.start] + replacement + updated[candidate.end :]
    return updated, len(selected)


def _build_unified_diff(file_path: Path, before: str, after: str) -> str:
    diff_lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=str(file_path),
        tofile=str(file_path),
        lineterm="",
    )
    return "\n".join(diff_lines)


def _count_line_changes(before: str, after: str) -> tuple[int, int]:
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines())
    additions = 0
    deletions = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "insert"}:
            additions += j2 - j1
        if tag in {"replace", "delete"}:
            deletions += i2 - i1
    return additions, deletions


def _build_success_result(
    file_path: Path,
    before: str,
    after: str,
    *,
    operation: str,
    replaced_count: int,
) -> dict[str, Any]:
    additions, deletions = _count_line_changes(before, after)
    diagnostics_result = collect_file_diagnostics(file_path=file_path, content=after)
    operation_label = {"append": "追加", "replace": "替换", "delete": "删除"}.get(operation, operation)
    message = f"{operation_label}成功：{file_path}，共处理 {replaced_count} 处。"
    output = message
    output += diagnostics_result.build_llm_excerpt()
    return {
        "success": True,
        "filePath": str(file_path),
        "operation": operation,
        "replacedCount": replaced_count,
        "message": message,
        "title": str(file_path),
        "output": output,
        "metadata": {
            "status": "completed",
            "success": True,
            "filePath": str(file_path),
            "operation": operation,
            "replacedCount": replaced_count,
            "message": message,
            "diff": _build_unified_diff(file_path, before, after),
            "filediff": {
                "file": str(file_path),
                "before": before,
                "after": after,
                "additions": additions,
                "deletions": deletions,
            },
            **diagnostics_result.to_metadata(),
        },
    }


def _validate_edit_state(target: Path) -> dict[str, Any] | None:
    state = get_file_state(target)
    if state is None:
        return build_tool_failure(
            f"Error: 编辑前必须先使用 read_file 读取 {target}",
            error_code="edit_read_required",
        )
    current_mtime_ns = target.stat().st_mtime_ns
    if current_mtime_ns != state.read_mtime_ns or state.last_read_at_ns <= state.last_edit_at_ns:
        return build_tool_failure(
            f"Error: 文件 {target} 自最近一次读取后已发生变化，请重新执行 read_file。",
            error_code="edit_stale_read",
        )
    return None


def _build_operation(old_string: str, new_string: str) -> str:
    if old_string == "":
        return "append"
    if new_string == "":
        return "delete"
    return "replace"


def run_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    try:
        if old_string == new_string:
            raise FileToolError(
                "newString 必须与 oldString 不同。若无需修改，请不要调用 edit_file。",
                error_code="edit_new_string_unchanged",
            )

        target = _resolve_edit_target(file_path)
        if target.exists() and target.is_dir():
            raise FileToolError(
                f"目标路径是目录，无法编辑：{target}。请改用文件绝对路径。",
                error_code="edit_path_is_directory",
            )

        if not target.exists():
            raise FileToolError(
                f"文件不存在：{target}。若要新建文件，请先使用 write_file 创建。",
                error_code="edit_file_missing",
            )
        if _detect_binary_file(target):
            raise FileToolError(
                f"edit_file 仅支持文本文件：{target}。请改用适合二进制文件的处理方式。",
                error_code="edit_binary_unsupported",
            )

        read_guard_failure = _validate_edit_state(target)
        if read_guard_failure is not None:
            return read_guard_failure

        before = target.read_text(encoding="utf-8")
        operation = _build_operation(old_string, new_string)
        if old_string == "":
            after = before + new_string
            replaced_count = 1
        else:
            candidates = _find_candidates(before, old_string)
            if not candidates:
                raise FileToolError(
                    (
                        f"未在 {target} 中找到 oldString。可能原因包括空白字符不一致、缩进层级变化，"
                        "或文件内容已被修改。请先重新执行 read_file，并在 oldString 中携带前后 1-2 行上下文。"
                    ),
                    error_code="edit_text_not_found",
                )
            after, replaced_count = _replace_candidates(
                before,
                candidates,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )

        target.write_text(after, encoding="utf-8")
        record_file_edit(target, mtime_ns=target.stat().st_mtime_ns)
        return _build_success_result(
            target,
            before,
            after,
            operation=operation,
            replaced_count=replaced_count,
        )
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
        error_code = "edit_path_forbidden" if "超出允许范围" in message else "edit_failed"
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
            f"Error: 系统异常：{exc}。请检查文件内容、权限或先重新读取文件后重试。",
            error_code="edit_failed",
            error_type=type(exc).__name__,
            success=False,
            filePath=file_path,
            error=str(exc),
            message=f"系统异常：{exc}。请检查文件内容、权限或先重新读取文件后重试。",
        )
