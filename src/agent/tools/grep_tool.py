import json
import shutil
import subprocess
from pathlib import Path

from ..runtime.workspace import get_workspace
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_directory

MAX_GREP_RESULTS = 100
MAX_GREP_LINE_LENGTH = 2000


def resolve_grep_search_path(path: str | None = None) -> Path:
    return resolve_workspace_directory(path)


def _build_rg_command(pattern: str, search_root: Path, include: list[str]) -> list[str]:
    command = [
        "rg",
        "--json",
        "--line-number",
        "--color",
        "never",
        pattern,
        str(search_root),
    ]
    for glob_pattern in include:
        command.extend(["--glob", glob_pattern])
    return command


def _normalize_include(include: object) -> list[str]:
    if include is None:
        return []
    if not isinstance(include, list):
        raise ValueError("include 必须是字符串数组")

    normalized: list[str] = []
    for item in include:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("include 里的每一项都必须是非空字符串")
        normalized.append(item.strip())
    return normalized


def _truncate_line(text: str) -> str:
    if len(text) <= MAX_GREP_LINE_LENGTH:
        return text
    return f"{text[:MAX_GREP_LINE_LENGTH]}..."


def run_grep(pattern: str, path: str | None = None, include: object = None) -> dict[str, object]:
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            return build_tool_failure("Error: pattern 不能为空", error_code="grep_pattern_invalid")
        if shutil.which("rg") is None:
            return build_tool_failure("Error: 未找到 rg 命令", error_code="grep_unavailable")

        normalized_pattern = pattern.strip()
        normalized_include = _normalize_include(include)
        search_root = resolve_grep_search_path(path)
        command = _build_rg_command(normalized_pattern, search_root, normalized_include)
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        if process.returncode not in (0, 1):
            error_text = process.stderr.strip() or "rg 执行失败"
            return build_tool_failure(
                f"Error: {error_text}",
                error_code="grep_failed",
                return_code=process.returncode,
            )

        matches: list[dict[str, object]] = []
        partial = bool(process.stderr.strip())

        for raw_line in process.stdout.splitlines():
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            if event.get("type") != "match":
                continue

            data = event.get("data") or {}
            path_data = data.get("path") or {}
            lines_data = data.get("lines") or {}
            line_number = data.get("line_number")
            path_text = path_data.get("text")
            line_text = lines_data.get("text")
            if not isinstance(path_text, str) or not isinstance(line_text, str) or not isinstance(line_number, int):
                continue

            file_path = Path(path_text).resolve()
            try:
                stat_result = file_path.stat()
            except OSError:
                # 文件在搜索和收集之间被删除或暂时不可读时，保留已收集结果即可。
                partial = True
                continue

            matches.append(
                {
                    "file_path": file_path,
                    "line_number": line_number,
                    "line_text": _truncate_line(line_text.rstrip("\n\r")),
                    "mtime": stat_result.st_mtime,
                }
            )

        matches.sort(key=lambda item: (-float(item["mtime"]), str(item["file_path"]), int(item["line_number"])))
        truncated = len(matches) > MAX_GREP_RESULTS
        limited_matches = matches[:MAX_GREP_RESULTS]

        if not limited_matches:
            output = "No matches found"
        else:
            output_lines = [
                f'{item["file_path"]}:{item["line_number"]}:{item["line_text"]}'
                for item in limited_matches
            ]
            if truncated:
                output_lines.append(f"... truncated, omitted {len(matches) - MAX_GREP_RESULTS} additional matches")
            if partial:
                output_lines.append("... some files were skipped during search")
            output = "\n".join(output_lines)

        relative_title = search_root.relative_to(get_workspace().root)
        title = "." if str(relative_title) == "." else relative_title.as_posix()
        return {
            "title": title,
            "output": output,
            "metadata": {
                "status": "completed",
                "pattern": normalized_pattern,
                "path": str(search_root),
                "include": normalized_include,
                "count": len(limited_matches),
                "truncated": truncated,
                "partial": partial,
            },
        }
    except FileNotFoundError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="grep_path_not_found", error_type=type(exc).__name__)
    except NotADirectoryError as exc:
        return build_tool_failure(
            f"Error: {exc}",
            error_code="grep_path_not_directory",
            error_type=type(exc).__name__,
        )
    except ValueError as exc:
        error_message = str(exc)
        error_code = "grep_pattern_invalid" if "include" in error_message else "grep_path_forbidden"
        return build_tool_failure(f"Error: {error_message}", error_code=error_code, error_type=type(exc).__name__)
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="grep_failed", error_type=type(exc).__name__)
