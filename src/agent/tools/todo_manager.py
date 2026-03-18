import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.context import get_session_id

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TodoManager:
    VALID_STATUSES = ["pending", "in_progress", "completed", "cancelled"]
    VALID_PRIORITIES = ["high", "medium", "low"]

    def __init__(self, storage_dir: str = "src/storage/todo"):
        self.todos = []
        storage_path = Path(storage_dir)
        if not storage_path.is_absolute():
            storage_path = PROJECT_ROOT / storage_path
        self.storage_dir = storage_path.resolve()

    def _safe_session_id(self, session_id: str) -> str:
        """将会话 ID 规整为安全文件名，防止路径穿越。"""
        normalized = re.sub(r"[^a-zA-Z0-9._-]", "_", session_id).strip("._")
        return normalized or "default_session"

    def _session_file(self, session_id: str) -> Path:
        safe_id = self._safe_session_id(session_id)
        return self.storage_dir / f"{safe_id}.json"

    def _persist(self, session_id: str, todo_list: list[dict[str, Any]]) -> None:
        payload = {
            "session_id": session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "todo_list": todo_list,
        }
        file_path = self._session_file(session_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def update(self, todo_list: list[dict[str, Any]]) -> str:
        """更新并持久化 todo 列表。"""
        if len(todo_list) > 20:
            raise ValueError("Todo list cannot exceed 20 items")

        processed_todos = []
        in_progress_count = 0

        for i, todo in enumerate(todo_list):
            if todo.get("id") is None or todo["id"] == "":
                todo_id = i + 1
            else:
                todo_id = todo["id"]

            text = str(todo.get("text", "")).strip()
            if not text:
                raise ValueError(f"Todo item {i+1} has empty text after stripping whitespace")

            status = str(todo.get("status", "")).lower().strip()
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}' for todo item {i+1}. Must be one of: {self.VALID_STATUSES}")

            priority = str(todo.get("priority", "")).lower().strip()
            if priority not in self.VALID_PRIORITIES:
                raise ValueError(
                    f"Invalid priority '{priority}' for todo item {i+1}. Must be one of: {self.VALID_PRIORITIES}"
                )

            if status == "in_progress":
                in_progress_count += 1
                if in_progress_count > 1:
                    raise ValueError("Only one todo can be in progress at a time")

            processed_todos.append({
                "id": todo_id,
                "text": text,
                "status": status,
                "priority": priority,
            })

        self.todos = processed_todos
        self._persist(get_session_id(), processed_todos)
        return self.render()

    def read_current_session(self) -> str:
        """读取当前会话下的 todo JSON。"""
        session_id = get_session_id()
        file_path = self._session_file(session_id)
        if not file_path.exists():
            return f"No todos found for session '{session_id}'."
        return file_path.read_text()

    def render(self) -> str:
        """渲染简洁的 todo 文本摘要。"""
        if not self.todos:
            return "No todos."

        lines = []
        completed_count = 0

        for todo in self.todos:
            if todo["status"] == "completed":
                status_char = "x"
                completed_count += 1
            elif todo["status"] == "in_progress":
                status_char = ">"
            elif todo["status"] == "cancelled":
                status_char = "-"
            else:
                status_char = " "

            lines.append(
                f"[{status_char}] #{todo['id']}: {todo['text']} (priority={todo['priority']})"
            )

        total_count = len(self.todos)
        completion_info = f"({completed_count}/{total_count} completed)"
        lines.append("")
        lines.append(completion_info)
        return "\n".join(lines)
