from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from pathlib import Path

from ..core.message import Message, append_text_part, create_message, extract_tool_calls, get_role, trim_messages_by_compaction_checkpoint
from .conversation import ConversationMessage, Session
from .conversation_adapter import conversation_message_to_runtime_message, detect_compaction_record, message_to_conversation_message, runtime_messages_to_conversation_messages
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
    def load_runtime(self, session_id: str) -> dict[str, object] | None:
        """读取某个会话当前持久化的 runtime 偏好。"""

    @abstractmethod
    def save_runtime(self, session_id: str, runtime: dict[str, object] | None) -> None:
        """保存某个会话当前 runtime 偏好；传入 None 表示清空。"""

    def append(self, session_id: str, message: Message) -> None:
        """追加保存单条消息；默认退化为 load + save，文件实现会使用 JSONL O(1) 追加。"""
        self.save(session_id, [*self.load(session_id), message])

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

    def __init__(self, max_messages: int = 24, *, trim_enabled: bool = True) -> None:
        self._max_messages = max_messages
        self._trim_enabled = trim_enabled
        self._store: dict[str, list[Message]] = {}
        self._runtime_store: dict[str, dict[str, object]] = {}

    def load(self, session_id: str) -> list[Message]:
        stored = self._store.get(session_id, [])
        trimmed = trim_messages_by_compaction_checkpoint(stored)
        return deepcopy(normalize_history_prefix(trimmed))

    def save(self, session_id: str, messages: list[Message]) -> None:
        trimmed_messages = _prepare_messages_for_storage(
            messages,
            max_messages=self._max_messages,
            trim_enabled=self._trim_enabled,
        )
        self._store[session_id] = deepcopy(trimmed_messages)

    def load_runtime(self, session_id: str) -> dict[str, object] | None:
        runtime = self._runtime_store.get(session_id)
        return deepcopy(runtime) if isinstance(runtime, dict) else None

    def save_runtime(self, session_id: str, runtime: dict[str, object] | None) -> None:
        normalized = (session_id or "").strip()
        if not normalized:
            raise ValueError("session_id 不能为空")
        if runtime:
            self._runtime_store[normalized] = deepcopy(runtime)
        else:
            self._runtime_store.pop(normalized, None)

    def append(self, session_id: str, message: Message) -> None:
        stored = self._store.get(session_id, [])
        self.save(session_id, [*stored, message])

    def clear(self, session_id: str | None = None) -> None:
        normalized = (session_id or "").strip()
        if not normalized:
            self._store.clear()
            self._runtime_store.clear()
            return
        self._store.pop(normalized, None)
        self._runtime_store.pop(normalized, None)


class FileSessionMemoryStore(SessionMemoryStore):
    """按工作区落盘的会话记忆实现，便于 CLI/Web 重启后继续读取历史。"""

    def __init__(self, base_dir: Path | None = None, max_messages: int = 24, *, trim_enabled: bool = True) -> None:
        self._base_dir = base_dir
        self._max_messages = max_messages
        self._trim_enabled = trim_enabled

    def _storage_dir(self) -> Path:
        return (self._base_dir or get_workspace().sessions_dir).resolve()

    def _session_file(self, session_id: str) -> Path:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id 不能为空")
        normalized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in normalized_session_id).strip("._")
        if not normalized:
            raise ValueError("session_id 缺少可用字符")
        return self._storage_dir() / f"{normalized}.jsonl"

    def load(self, session_id: str) -> list[Message]:
        file_path = self._session_file(session_id)
        if not file_path.exists():
            return []
        try:
            session = Session.load_from_path(file_path)
        except (OSError, ValueError):
            return []
        restored = [conversation_message_to_runtime_message(msg, session.session_id) for msg in session.messages]
        trimmed = trim_messages_by_compaction_checkpoint([msg for msg in restored if isinstance(msg, dict)])
        return deepcopy(normalize_history_prefix(trimmed))

    def load_runtime(self, session_id: str) -> dict[str, object] | None:
        file_path = self._session_file(session_id)
        if not file_path.exists():
            return None
        try:
            session = Session.load_from_path(file_path)
        except (OSError, ValueError):
            return None
        return deepcopy(dict(session.runtime or {})) or None

    def save(self, session_id: str, messages: list[Message]) -> None:
        trimmed_messages = _prepare_messages_for_storage(
            messages,
            max_messages=self._max_messages,
            trim_enabled=self._trim_enabled,
        )
        file_path = self._session_file(session_id)
        session = Session.new(session_id).with_persistence_path(file_path)
        existing_runtime = self.load_runtime(session_id)
        if existing_runtime:
            session.runtime = existing_runtime
        session.messages = runtime_messages_to_conversation_messages(trimmed_messages)
        session.compaction = detect_compaction_record(trimmed_messages)
        # save_to_path 是 O(n) 快照重写，用于 turn 结束和 compact 后保证主文件自洽。
        session.save_to_path(file_path)

    def save_runtime(self, session_id: str, runtime: dict[str, object] | None) -> None:
        file_path = self._session_file(session_id)
        if file_path.exists():
            session = Session.load_from_path(file_path).with_persistence_path(file_path)
        else:
            session = Session.new(session_id).with_persistence_path(file_path)
        session.runtime = deepcopy(runtime) if runtime else None
        session.save_to_path(file_path)

    def append(self, session_id: str, message: Message) -> None:
        file_path = self._session_file(session_id)
        session = Session.new(session_id).with_persistence_path(file_path)
        # push_message 是 JSONL O(1) 追加；失败时 Session 会回滚内存状态。
        session.push_message(message_to_conversation_message(message))

    def append_conversation_message(self, session_id: str, message: ConversationMessage) -> None:
        file_path = self._session_file(session_id)
        session = Session.new(session_id).with_persistence_path(file_path)
        session.push_message(message)

    def clear(self, session_id: str | None = None) -> None:
        normalized = (session_id or "").strip()
        if not normalized:
            storage_dir = self._storage_dir()
            if not storage_dir.exists():
                return
            for file_path in storage_dir.glob("*.jsonl"):
                file_path.unlink(missing_ok=True)
            return
        self._session_file(normalized).unlink(missing_ok=True)


def _prepare_messages_for_storage(messages: list[Message], *, max_messages: int, trim_enabled: bool) -> list[Message]:
    """统一收敛持久化前的历史裁剪，保证内存/文件存储行为一致。"""

    # 普通 system prompt 不进入历史；compact 生成的 system summary 需要保留为恢复入口。
    persistable_messages = [
        msg
        for msg in messages
        if get_role(msg) != "system" or bool(msg.get("info", {}).get("summary"))
    ]
    trimmed_messages = trim_messages_by_compaction_checkpoint(persistable_messages)
    if trim_enabled:
        trimmed_messages = trimmed_messages[-max_messages:]
    return trimmed_messages
