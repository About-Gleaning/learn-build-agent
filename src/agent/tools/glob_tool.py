import heapq
from pathlib import Path

from ..runtime.workspace import get_workspace
from .handlers import build_tool_failure
from .path_utils import resolve_workspace_directory

MAX_GLOB_RESULTS = 100


def resolve_glob_search_path(path: str | None = None) -> Path:
    return resolve_workspace_directory(path)


def run_glob(pattern: str, path: str | None = None) -> dict[str, object]:
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            return build_tool_failure("Error: pattern 不能为空", error_code="glob_pattern_invalid")

        search_root = resolve_glob_search_path(path)
        latest_matches: list[tuple[float, str]] = []
        matched_paths: dict[str, Path] = {}
        total_files = 0

        for matched in search_root.glob(pattern):
            try:
                if not matched.is_file():
                    continue
                resolved = matched.resolve()
                stat_result = resolved.stat()
            except OSError:
                # 搜索过程中条目可能被删除或暂时不可访问，跳过单个异常条目即可。
                continue

            total_files += 1
            candidate_key = str(resolved)
            candidate = (stat_result.st_mtime, candidate_key)
            if len(latest_matches) < MAX_GLOB_RESULTS:
                heapq.heappush(latest_matches, candidate)
                matched_paths[candidate_key] = resolved
                continue
            if candidate > latest_matches[0]:
                removed = heapq.heapreplace(latest_matches, candidate)
                matched_paths.pop(removed[1], None)
                matched_paths[candidate_key] = resolved

        ordered_keys = [item[1] for item in sorted(latest_matches, reverse=True)]
        ordered_paths = [matched_paths[key] for key in ordered_keys]
        relative_title = search_root.relative_to(get_workspace().root)
        title = "." if str(relative_title) == "." else relative_title.as_posix()
        truncated = total_files > MAX_GLOB_RESULTS

        if not ordered_paths:
            output = "No files found"
        else:
            output_lines = [str(item) for item in ordered_paths]
            if truncated:
                output_lines.append(f"... truncated, omitted {total_files - MAX_GLOB_RESULTS} older matches")
            output = "\n".join(output_lines)

        return {
            "title": title,
            "output": output,
            "metadata": {
                "status": "completed",
                "count": len(ordered_paths),
                "truncated": truncated,
            },
        }
    except FileNotFoundError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="glob_path_not_found", error_type=type(exc).__name__)
    except NotADirectoryError as exc:
        return build_tool_failure(
            f"Error: {exc}",
            error_code="glob_path_not_directory",
            error_type=type(exc).__name__,
        )
    except ValueError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="glob_path_forbidden", error_type=type(exc).__name__)
    except Exception as exc:
        return build_tool_failure(f"Error: {exc}", error_code="glob_failed", error_type=type(exc).__name__)
