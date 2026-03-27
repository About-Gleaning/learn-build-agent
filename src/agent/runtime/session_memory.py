from __future__ import annotations

import json
from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path

from ..core.message import Message, append_text_part, create_message, extract_tool_calls, get_role, trim_messages_by_compaction_checkpoint
from .workspace import get_workspace


class SessionMemoryStore(ABC):
    """会话记忆存储抽象，便于后续替换为 Redis/DB 等实现。"""

    @abstractmethod
    def load(self, session_id: str) -> list[Message]:
        """读取某个会话的历史消息。"""

    @abstractmethod
    def save(self, session_id: str, messages: list[Message]) -> None:
        """保存某个会话的历史消息。"""

    @abstractmethod
    def clear(self, session_id: str | None = None) -> None:
        """清理会话记忆；session_id 为空时清空全部。"""


def normalize_history_prefix(messages: list[Message]) -> list[Message]:
    """规范化历史前缀，避免非法片段直接作为会话起点进入运行时。"""

    normalized_messages = [message for message in messages if isinstance(message, dict)]
    if not normalized_messages:
        return []

    first_message = normalized_messages[0]
    first_role = get_role(first_message)
    if first_role == "user":
        return normalized_messages

    if first_role == "assistant" and not extract_tool_calls(first_message):
        return normalized_messages

    session_id = str(first_message.get("info", {}).get("session_id", "")).strip()
    synthetic_user = create_message("user", session_id, status="completed")
    append_text_part(
        synthetic_user,
        "系统恢复提示：更早的对话前缀已缺失，以下历史为从不完整片段恢复出的续接上下文，请结合当前状态谨慎判断。",
    )
    return [synthetic_user, *normalized_messages]


class InMemorySessionMemoryStore(SessionMemoryStore):
    """默认内存记忆实现，适合单进程场景。"""

    def __init__(self, max_messages: int = 24) -> None:
        self._max_messages = max_messages
        self._store: dict[str, list[Message]] = {}

    def load(self, session_id: str) -> list[Message]:
        stored = self._store.get(session_id, [])
        trimmed = trim_messages_by_compaction_checkpoint(stored)
        return deepcopy(normalize_history_prefix(trimmed))

    def save(self, session_id: str, messages: list[Message]) -> None:
        non_system_messages = [msg for msg in messages if get_role(msg) != "system"]
        trimmed_messages = trim_messages_by_compaction_checkpoint(non_system_messages)
        self._store[session_id] = deepcopy(trimmed_messages)

    def clear(self, session_id: str | None = None) -> None:
        normalized = (session_id or "").strip()
        if not normalized:
            self._store.clear()
            return
        self._store.pop(normalized, None)


class FileSessionMemoryStore(SessionMemoryStore):
    """按工作区落盘的会话记忆实现，便于 CLI/Web 重启后继续读取历史。"""

    def __init__(self, base_dir: Path | None = None, max_messages: int = 24) -> None:
        self._base_dir = base_dir
        self._max_messages = max_messages

    def _storage_dir(self) -> Path:
        return (self._base_dir or get_workspace().sessions_dir).resolve()

    def _session_file(self, session_id: str) -> Path:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id 不能为空")
        normalized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in normalized_session_id).strip("._")
        if not normalized:
            raise ValueError("session_id 缺少可用字符")
        return self._storage_dir() / f"{normalized}.json"

    def load(self, session_id: str) -> list[Message]:
        file_path = self._session_file(session_id)
        if not file_path.exists():
            return []
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        trimmed = trim_messages_by_compaction_checkpoint([msg for msg in payload if isinstance(msg, dict)])
        return deepcopy(normalize_history_prefix(trimmed))

    def save(self, session_id: str, messages: list[Message]) -> None:
        non_system_messages = [msg for msg in messages if get_role(msg) != "system"]
        trimmed_messages = trim_messages_by_compaction_checkpoint(non_system_messages)
        if self._max_messages > 0:
            trimmed_messages = trimmed_messages[-self._max_messages :]
        file_path = self._session_file(session_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(trimmed_messages, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self, session_id: str | None = None) -> None:
        normalized = (session_id or "").strip()
        if not normalized:
            storage_dir = self._storage_dir()
            if not storage_dir.exists():
                return
            for file_path in storage_dir.glob("*.json"):
                file_path.unlink(missing_ok=True)
            return
        self._session_file(normalized).unlink(missing_ok=True)
