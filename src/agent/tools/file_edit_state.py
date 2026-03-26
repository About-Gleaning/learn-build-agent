import time
from dataclasses import dataclass
from pathlib import Path

from ..core.context import get_session_id


@dataclass
class FileEditState:
    file_path: str
    read_mtime_ns: int
    last_read_at_ns: int
    last_edit_at_ns: int = 0


_SESSION_FILE_STATES: dict[str, dict[str, FileEditState]] = {}


def _normalize_path(file_path: Path | str) -> str:
    return str(Path(file_path).resolve())


def record_file_read(file_path: Path, *, mtime_ns: int) -> None:
    session_id = get_session_id()
    normalized_path = _normalize_path(file_path)
    session_states = _SESSION_FILE_STATES.setdefault(session_id, {})
    existing = session_states.get(normalized_path)
    last_edit_at_ns = existing.last_edit_at_ns if existing is not None else 0
    session_states[normalized_path] = FileEditState(
        file_path=normalized_path,
        read_mtime_ns=mtime_ns,
        last_read_at_ns=time.time_ns(),
        last_edit_at_ns=last_edit_at_ns,
    )


def get_file_state(file_path: Path) -> FileEditState | None:
    session_id = get_session_id()
    return _SESSION_FILE_STATES.get(session_id, {}).get(_normalize_path(file_path))


def record_file_edit(file_path: Path, *, mtime_ns: int) -> None:
    session_id = get_session_id()
    normalized_path = _normalize_path(file_path)
    session_states = _SESSION_FILE_STATES.setdefault(session_id, {})
    existing = session_states.get(normalized_path)
    last_read_at_ns = existing.last_read_at_ns if existing is not None else 0
    session_states[normalized_path] = FileEditState(
        file_path=normalized_path,
        read_mtime_ns=mtime_ns,
        last_read_at_ns=last_read_at_ns,
        last_edit_at_ns=time.time_ns(),
    )


def clear_file_edit_states() -> None:
    _SESSION_FILE_STATES.clear()
