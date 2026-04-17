from __future__ import annotations

import heapq
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..runtime.workspace import get_workspace

MAX_PATH_SUGGESTIONS = 50
INDEX_TTL_SECONDS = 30.0
PATH_MRU_LIMIT = 200
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    ".gradle",
    "out",
    "dist",
    ".venv",
    "venv",
    ".tox",
    ".ruff_cache",
    "coverage",
    ".mypy_cache",
    ".pytest_cache",
    ".runtime",
}


@dataclass(frozen=True)
class PathSuggestion:
    path: str
    name: str
    relative_path: str
    kind: str


@dataclass(frozen=True)
class _IndexedPath:
    path: Path
    relative_path: str
    name_lower: str
    relative_lower: str
    kind: str
    path_parts_lower: tuple[str, ...]
    depth: int


_INDEX_CACHE: dict[str, tuple[float, list[_IndexedPath]]] = {}
_INDEX_CACHE_LOCK = threading.Lock()
_INDEX_BUILD_LOCKS: dict[str, threading.Lock] = {}
_MRU_CACHE: dict[str, tuple[float, dict[str, float]]] = {}


@dataclass(frozen=True)
class _MatchResult:
    score: int
    match_indices: tuple[int, ...]


def _iter_workspace_entries(root: Path) -> list[_IndexedPath]:
    indexed: list[_IndexedPath] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            continue

        for entry in entries:
            entry_path = Path(entry.path)
            is_symlink = entry.is_symlink()
            kind = "directory" if entry.is_dir(follow_symlinks=False) else "file"
            if kind == "directory" and entry.name.lower() in SKIP_DIR_NAMES:
                continue
            if is_symlink:
                try:
                    resolved_entry_path = entry_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if not resolved_entry_path.is_relative_to(root):
                    continue
                # 符号链接目录只作为可选路径展示，不继续递归，避免越界遍历和重复扫描。
                kind = "directory" if resolved_entry_path.is_dir() else "file"
            try:
                relative_path = entry_path.relative_to(root).as_posix()
            except ValueError:
                continue
            relative_parts = Path(relative_path).parts
            indexed.append(
                _IndexedPath(
                    path=entry_path,
                    relative_path=relative_path,
                    name_lower=entry.name.lower(),
                    relative_lower=relative_path.lower(),
                    kind=kind,
                    path_parts_lower=tuple(part.lower() for part in relative_parts),
                    depth=len(relative_parts),
                )
            )
            if kind == "directory" and not is_symlink:
                stack.append(entry_path)
    return indexed


def _get_index_build_lock(cache_key: str) -> threading.Lock:
    with _INDEX_CACHE_LOCK:
        lock = _INDEX_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _INDEX_BUILD_LOCKS[cache_key] = lock
        return lock


def _load_workspace_index() -> list[_IndexedPath]:
    workspace_root = get_workspace().root.resolve()
    cache_key = str(workspace_root)
    now = time.monotonic()
    cached = _INDEX_CACHE.get(cache_key)
    if cached and now - cached[0] <= INDEX_TTL_SECONDS:
        return cached[1]
    build_lock = _get_index_build_lock(cache_key)
    with build_lock:
        now = time.monotonic()
        cached = _INDEX_CACHE.get(cache_key)
        if cached and now - cached[0] <= INDEX_TTL_SECONDS:
            return cached[1]
        indexed = _iter_workspace_entries(workspace_root)
        _INDEX_CACHE[cache_key] = (time.monotonic(), indexed)
        return indexed


def _mru_storage_path() -> Path:
    workspace = get_workspace()
    return (workspace.workspace_home / "web" / "path_suggestions_mru.json").resolve()


def _load_recent_selection_scores() -> dict[str, float]:
    workspace = get_workspace()
    cache_key = str(workspace.root.resolve())
    cached = _MRU_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] <= INDEX_TTL_SECONDS:
        return cached[1]

    storage_path = _mru_storage_path()
    try:
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        scores: dict[str, float] = {}
    else:
        if not isinstance(payload, dict):
            scores = {}
        else:
            scores = {
                str(path).lower(): float(timestamp)
                for path, timestamp in payload.items()
                if isinstance(path, str) and isinstance(timestamp, int | float)
            }
    _MRU_CACHE[cache_key] = (now, scores)
    return scores


def _save_recent_selection_scores(scores: dict[str, float]) -> None:
    storage_path = _mru_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = dict(sorted(scores.items(), key=lambda item: item[1], reverse=True)[:PATH_MRU_LIMIT])
    storage_path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
    _MRU_CACHE[str(get_workspace().root.resolve())] = (time.monotonic(), trimmed)


def record_path_selection(relative_path: str) -> None:
    normalized_relative_path = (relative_path or "").strip()
    if not normalized_relative_path:
        raise ValueError("relative_path 不能为空")
    workspace_root = get_workspace().root.resolve()
    target = (workspace_root / normalized_relative_path).resolve()
    if not target.is_relative_to(workspace_root):
        raise ValueError("relative_path 超出工作区范围")
    if not target.exists():
        raise ValueError("relative_path 对应路径不存在")

    scores = _load_recent_selection_scores().copy()
    scores[normalized_relative_path.lower()] = time.time()
    _save_recent_selection_scores(scores)


def _query_segments(query_lower: str) -> tuple[str, ...]:
    return tuple(segment for segment in query_lower.replace("\\", "/").split("/") if segment)


def _find_fuzzy_match_indices(query_lower: str, text_lower: str) -> tuple[int, ...] | None:
    if not query_lower:
        return None
    cursor = 0
    indices: list[int] = []
    for index, char in enumerate(text_lower):
        if cursor < len(query_lower) and char == query_lower[cursor]:
            indices.append(index)
            cursor += 1
            if cursor == len(query_lower):
                return tuple(indices)
    return None


def _build_match_result(item: _IndexedPath, query_lower: str) -> _MatchResult | None:
    query_segments = _query_segments(query_lower)
    basename_exact = item.name_lower == query_lower
    basename_prefix = item.name_lower.startswith(query_lower)
    segment_prefix = any(part.startswith(query_lower) for part in item.path_parts_lower)
    segment_sequence_prefix = bool(query_segments) and len(query_segments) <= len(item.path_parts_lower) and any(
        all(item.path_parts_lower[start + offset].startswith(segment) for offset, segment in enumerate(query_segments))
        for start in range(len(item.path_parts_lower) - len(query_segments) + 1)
    )
    basename_substring = query_lower in item.name_lower
    path_substring = query_lower in item.relative_lower
    fuzzy_match_indices = _find_fuzzy_match_indices(query_lower, item.relative_lower)
    basename_match_index = item.name_lower.find(query_lower)
    path_match_index = item.relative_lower.find(query_lower)

    if not basename_substring and not path_substring and fuzzy_match_indices is None:
        return None

    score = 0
    if basename_exact:
        score += 2000
    if basename_prefix:
        score += 1100
    if segment_sequence_prefix:
        score += 650
    elif segment_prefix:
        score += 520
    if basename_substring:
        score += 820
        score += max(0, 120 - max(basename_match_index, 0) * 12)
    if path_substring:
        if not basename_substring:
            score += 260
            score += max(0, 70 - max(path_match_index, 0) * 4)

    if fuzzy_match_indices is not None:
        span = fuzzy_match_indices[-1] - fuzzy_match_indices[0] + 1
        contiguous_pairs = sum(
            1 for left, right in zip(fuzzy_match_indices, fuzzy_match_indices[1:]) if right == left + 1
        )
        # 连续且跨度更紧凑的命中更像用户真正想找的目标。
        score += 80
        score += contiguous_pairs * 35
        score += max(0, 90 - span * 3)
        score += max(0, 45 - fuzzy_match_indices[0] * 2)

    score -= item.depth * 5
    score -= len(item.relative_lower)
    return _MatchResult(score=score, match_indices=fuzzy_match_indices or ())


def suggest_workspace_paths(query: str, *, limit: int = MAX_PATH_SUGGESTIONS) -> list[PathSuggestion]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return []
    query_lower = normalized_query.lower()
    safe_limit = min(max(limit, 1), MAX_PATH_SUGGESTIONS)
    matches: list[tuple[_IndexedPath, _MatchResult]] = []
    for item in _load_workspace_index():
        match_result = _build_match_result(item, query_lower)
        if match_result is None:
            continue
        matches.append((item, match_result))
    best_matches = heapq.nsmallest(safe_limit, matches, key=lambda entry: (-entry[1].score, entry[0].relative_path))
    return [
        PathSuggestion(
            path=str(item.path),
            name=item.path.name,
            relative_path=item.relative_path,
            kind=item.kind,
        )
        for item, _ in best_matches
    ]
