from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ConversationRole = Literal["user", "assistant", "tool", "system"]
BlockType = Literal["text", "tool_use", "tool_result"]


class ConversationPersistenceError(ValueError):
    """会话 JSONL 持久化错误，错误信息必须能直接定位坏行或坏字段。"""


@dataclass
class ConversationMessage:
    role: ConversationRole
    blocks: list[dict[str, Any]]
    usage: dict[str, int] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def user_text(cls, text: str) -> "ConversationMessage":
        return cls(role="user", blocks=[{"type": "text", "text": text}])

    @classmethod
    def assistant(cls, blocks: list[dict[str, Any]], usage: dict[str, int] | None = None) -> "ConversationMessage":
        return cls(role="assistant", blocks=blocks, usage=usage)

    @classmethod
    def tool_result(cls, tool_use_id: str, tool_name: str, output: str, is_error: bool) -> "ConversationMessage":
        return cls(
            role="tool",
            blocks=[
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "output": output,
                    "is_error": is_error,
                }
            ],
        )

    @classmethod
    def system_text(cls, text: str) -> "ConversationMessage":
        return cls(role="system", blocks=[{"type": "text", "text": text}])

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "blocks": self.blocks,
        }
        if self.usage is not None:
            payload["usage"] = self.usage
        if self.meta:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: Any, *, path: Path, line_no: int) -> "ConversationMessage":
        if not isinstance(payload, dict):
            raise ConversationPersistenceError(f"{path}:{line_no} message 必须是对象")
        role = payload.get("role")
        if role not in {"user", "assistant", "tool", "system"}:
            raise ConversationPersistenceError(f"{path}:{line_no} message.role 非法或缺失: {role!r}")
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            raise ConversationPersistenceError(f"{path}:{line_no} message.blocks 必须是数组")
        for index, block in enumerate(blocks, 1):
            _validate_block(block, path=path, line_no=line_no, index=index)
        usage = payload.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ConversationPersistenceError(f"{path}:{line_no} message.usage 必须是对象")
        meta = payload.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise ConversationPersistenceError(f"{path}:{line_no} message.meta 必须是对象")
        return cls(
            role=role,  # type: ignore[arg-type]
            blocks=[dict(block) for block in blocks],
            usage={str(k): int(v or 0) for k, v in usage.items()} if isinstance(usage, dict) else None,
            meta=dict(meta or {}),
        )


@dataclass
class Session:
    version: int
    session_id: str
    created_at_ms: int
    updated_at_ms: int
    messages: list[ConversationMessage] = field(default_factory=list)
    compaction: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    persistence_path: Path | None = None

    @classmethod
    def new(cls, session_id: str) -> "Session":
        now = _now_ms()
        return cls(version=1, session_id=session_id, created_at_ms=now, updated_at_ms=now)

    def with_persistence_path(self, path: str | Path) -> "Session":
        self.persistence_path = Path(path)
        return self

    @classmethod
    def load_from_path(cls, path: str | Path) -> "Session":
        file_path = Path(path)
        if not file_path.exists():
            raise ConversationPersistenceError(f"session 文件不存在: {file_path}")

        meta: dict[str, Any] | None = None
        compaction: dict[str, Any] | None = None
        messages: list[ConversationMessage] = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line_no, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ConversationPersistenceError(f"{file_path}:{line_no} 非法 JSON: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise ConversationPersistenceError(f"{file_path}:{line_no} JSONL 记录必须是对象")
                record_type = record.get("type")
                if record_type == "session_meta":
                    meta = _parse_session_meta(record, path=file_path, line_no=line_no)
                    continue
                if record_type == "compaction":
                    compaction = _parse_compaction(record, path=file_path, line_no=line_no)
                    continue
                if record_type == "message":
                    if "message" not in record:
                        raise ConversationPersistenceError(f"{file_path}:{line_no} 缺少 message 字段")
                    messages.append(ConversationMessage.from_dict(record["message"], path=file_path, line_no=line_no))
                    continue
                raise ConversationPersistenceError(f"{file_path}:{line_no} 未知记录 type: {record_type!r}")

        if meta is None:
            raise ConversationPersistenceError(f"{file_path}: 缺少 session_meta 记录")
        session = cls(
            version=int(meta["version"]),
            session_id=str(meta["session_id"]),
            created_at_ms=int(meta["created_at_ms"]),
            updated_at_ms=int(meta["updated_at_ms"]),
            messages=messages,
            compaction=compaction,
            runtime=dict(meta.get("runtime") or {}) or None,
            persistence_path=file_path,
        )
        return session

    def push_user_text(self, text: str) -> None:
        self.push_message(ConversationMessage.user_text(text))

    def push_message(self, message: ConversationMessage) -> None:
        old_updated_at_ms = self.updated_at_ms
        self.updated_at_ms = _now_ms()
        self.messages.append(message)
        try:
            if self.persistence_path is not None:
                self._append_message_to_path(self.persistence_path, message)
        except Exception:
            self.messages.pop()
            self.updated_at_ms = old_updated_at_ms
            raise

    def record_compaction(self, summary: str, removed_message_count: int) -> None:
        current = self.compaction if isinstance(self.compaction, dict) else {}
        self.compaction = {
            "type": "compaction",
            "count": int(current.get("count", 0) or 0) + 1,
            "removed_message_count": removed_message_count,
            "summary": summary,
        }
        self.updated_at_ms = _now_ms()

    def save_to_path(self, path: str | Path) -> None:
        file_path = Path(path)
        self.persistence_path = file_path
        self.updated_at_ms = _now_ms()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{file_path.name}.", suffix=".tmp", dir=str(file_path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for record in self._iter_snapshot_records():
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(file_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _append_message_to_path(self, path: Path, message: ConversationMessage) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size == 0:
            # 首次追加先写 meta，保证 JSONL 文件从任意时刻打开都知道所属 session。
            path.write_text(
                json.dumps(self._session_meta_record(), ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        record = {"type": "message", "message": message.to_dict()}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def _iter_snapshot_records(self) -> list[dict[str, Any]]:
        records = [self._session_meta_record()]
        if self.compaction:
            record = dict(self.compaction)
            record["type"] = "compaction"
            records.append(record)
        records.extend({"type": "message", "message": message.to_dict()} for message in self.messages)
        return records

    def _session_meta_record(self) -> dict[str, Any]:
        record = {
            "type": "session_meta",
            "version": self.version,
            "session_id": self.session_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }
        if isinstance(self.runtime, dict) and self.runtime:
            record["runtime"] = dict(self.runtime)
        return record


def _now_ms() -> int:
    return int(time.time() * 1000)


def _validate_block(block: Any, *, path: Path, line_no: int, index: int) -> None:
    if not isinstance(block, dict):
        raise ConversationPersistenceError(f"{path}:{line_no} blocks[{index}] 必须是对象")
    block_type = block.get("type")
    if block_type == "text":
        if not isinstance(block.get("text"), str):
            raise ConversationPersistenceError(f"{path}:{line_no} text block 缺少 text 字符串")
        return
    if block_type == "tool_use":
        for field_name in ("id", "name", "input"):
            if not isinstance(block.get(field_name), str):
                raise ConversationPersistenceError(f"{path}:{line_no} tool_use block 缺少 {field_name} 字符串")
        return
    if block_type == "tool_result":
        for field_name in ("tool_use_id", "tool_name", "output"):
            if not isinstance(block.get(field_name), str):
                raise ConversationPersistenceError(f"{path}:{line_no} tool_result block 缺少 {field_name} 字符串")
        if not isinstance(block.get("is_error"), bool):
            raise ConversationPersistenceError(f"{path}:{line_no} tool_result block 缺少 is_error 布尔值")
        return
    raise ConversationPersistenceError(f"{path}:{line_no} 未知 block.type: {block_type!r}")


def _parse_session_meta(record: dict[str, Any], *, path: Path, line_no: int) -> dict[str, Any]:
    required = ("version", "session_id", "created_at_ms", "updated_at_ms")
    for field_name in required:
        if field_name not in record:
            raise ConversationPersistenceError(f"{path}:{line_no} session_meta 缺少 {field_name}")
    runtime = record.get("runtime")
    if runtime is not None and not isinstance(runtime, dict):
        raise ConversationPersistenceError(f"{path}:{line_no} session_meta.runtime 必须是对象")
    return {
        "version": int(record["version"]),
        "session_id": str(record["session_id"]),
        "created_at_ms": int(record["created_at_ms"]),
        "updated_at_ms": int(record["updated_at_ms"]),
        "runtime": dict(runtime or {}),
    }


def _parse_compaction(record: dict[str, Any], *, path: Path, line_no: int) -> dict[str, Any]:
    for field_name in ("count", "removed_message_count", "summary"):
        if field_name not in record:
            raise ConversationPersistenceError(f"{path}:{line_no} compaction 缺少 {field_name}")
    return {
        "type": "compaction",
        "count": int(record["count"]),
        "removed_message_count": int(record["removed_message_count"]),
        "summary": str(record["summary"]),
    }
