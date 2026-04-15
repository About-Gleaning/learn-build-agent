from __future__ import annotations

from typing import Any, Callable

from ..core.message import Message
from .loop_hooks import LoopHook, LoopHookContext
from .session_hooks import SessionHook, SessionHookContext
from .tool_executor import ToolHook, ToolHookContext, ToolResult


class SessionJsonlPersistenceHook(SessionHook):
    """会话级 JSONL 落库实现：负责 user 追加与 turn 结束快照。"""

    def __init__(self, *, order: int = 1200) -> None:
        super().__init__(name="session_jsonl_persistence", order=order)

    def before_session(self, ctx: SessionHookContext) -> None:
        if not bool(ctx.get("persistence_enabled", False)):
            return
        append_callback = ctx.get("append_callback")
        user_message = ctx.get("user_message")
        if callable(append_callback) and isinstance(user_message, dict):
            append_callback(user_message)

    def after_session(self, ctx: SessionHookContext, message: dict[str, Any]) -> None:
        if not bool(ctx.get("persistence_enabled", False)):
            return
        save_callback = ctx.get("save_callback")
        if callable(save_callback):
            save_callback()

    def on_error(self, ctx: SessionHookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        del error, normalized_error
        if not bool(ctx.get("persistence_enabled", False)):
            return
        save_callback = ctx.get("save_callback")
        if callable(save_callback):
            save_callback()


class LoopJsonlPersistenceHook(LoopHook):
    """assistant 级 JSONL 落库实现：只处理完整 assistant 消息。"""

    def __init__(self, *, order: int = 1200) -> None:
        super().__init__(name="loop_jsonl_persistence", order=order)

    def after_loop(self, ctx: LoopHookContext) -> None:
        if not bool(ctx.get("save_enabled", False)):
            return
        # 旧的 LoopPersistenceHook 已经在更早顺序执行快照保存；此处再追加会制造短暂重复记录。
        if bool(ctx.get("snapshot_saved", False)):
            return
        assistant_message = ctx.get("assistant_message")
        append_callback = ctx.get("append_callback")
        if callable(append_callback) and isinstance(assistant_message, dict):
            append_callback(assistant_message)


class ToolJsonlPersistenceHook(ToolHook):
    """工具级 JSONL 落库实现：工具结果形成后追加 role=tool 消息。"""

    def __init__(self, *, order: int = 1200) -> None:
        super().__init__(name="tool_jsonl_persistence", order=order)

    def after_call(self, ctx: ToolHookContext, result: ToolResult) -> None:
        if not bool(ctx.get("persistence_enabled", False)):
            return
        append_callback = ctx.get("append_callback")
        if not callable(append_callback):
            return
        message_factory = ctx.get("message_factory")
        if not callable(message_factory):
            return
        message = message_factory(ctx, result)
        if isinstance(message, dict):
            append_callback(message)

    def on_error(self, ctx: ToolHookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        if not bool(ctx.get("persistence_enabled", False)):
            return
        append_callback = ctx.get("append_callback")
        message_factory = ctx.get("message_factory")
        if not callable(append_callback) or not callable(message_factory):
            return
        result: ToolResult = {
            "output": f"Error: {normalized_error.get('message', str(error))}",
            "metadata": {
                "status": "failed",
                "error_code": normalized_error.get("code", "execution_error"),
                "error_type": normalized_error.get("details", type(error).__name__),
            },
        }
        message = message_factory(ctx, result)
        if isinstance(message, dict):
            append_callback(message)
