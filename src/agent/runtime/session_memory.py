from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy

from ..core.message import Message, get_role, trim_messages_by_compaction_checkpoint


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


class InMemorySessionMemoryStore(SessionMemoryStore):
    """默认内存记忆实现，适合单进程场景。"""

    def __init__(self, max_messages: int = 24) -> None:
        self._max_messages = max_messages
        self._store: dict[str, list[Message]] = {}

    def load(self, session_id: str) -> list[Message]:
        stored = self._store.get(session_id, [])
        return deepcopy(trim_messages_by_compaction_checkpoint(stored))

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
