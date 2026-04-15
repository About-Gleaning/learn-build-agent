import logging
from typing import Any, Callable, TypedDict

from ..config.logging_setup import build_log_extra
from ..core.hooks import HookDispatcher
from ..core.message import DisplayPart, Message, ProcessItem, ResponseMeta
from .stream_display import _attach_response_summary

logger = logging.getLogger(__name__)


class LoopHookContext(TypedDict, total=False):
    session_id: str
    agent: str
    agent_kind: str
    depth: int
    stream: bool
    delegation_id: str
    parent_tool_call_id: str
    turn_started_at: str
    turn_completed_at: str
    round_no: int
    mode: str
    tool_call_owner_map: dict[str, str]
    assistant_message: Message
    process_items: list[ProcessItem]
    display_parts: list[DisplayPart]
    response_meta: ResponseMeta
    save_enabled: bool
    save_callback: Callable[[], None]
    append_callback: Callable[[Message], None]
    messages_ref: list[Message]


class LoopNormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class LoopHook:
    """单轮 assistant 投影 Hook，负责把已归属的数据做旁路处理。"""

    def __init__(self, name: str, *, fail_fast: bool = False, order: int = 1000) -> None:
        self.name = name
        self.fail_fast = fail_fast
        self.order = order

    def before_loop(self, ctx: LoopHookContext) -> None:
        """在每轮 loop 开始时触发。"""

    def after_loop(self, ctx: LoopHookContext) -> None:
        """在每轮 loop 结束并已确定 assistant 归属后触发。"""

    def on_loop_error(self, ctx: LoopHookContext, error: Exception, normalized_error: LoopNormalizedError) -> None:
        """在每轮 loop 执行异常时触发。"""


class LoopPersistenceHook(LoopHook):
    """默认 loop 持久化 Hook，负责把 assistant 级投影落回消息并保存。"""

    def __init__(self, *, fail_fast: bool = False, order: int = 1000) -> None:
        super().__init__(name="loop_persistence", fail_fast=fail_fast, order=order)

    def after_loop(self, ctx: LoopHookContext) -> None:
        assistant_message = ctx.get("assistant_message")
        if not isinstance(assistant_message, dict):
            return
        completed_at = str(ctx.get("turn_completed_at", "")).strip() or str(
            assistant_message.get("info", {}).get("turn_completed_at", "")
        ).strip()
        response_meta = _attach_response_summary(
            assistant_message,
            process_items=list(ctx.get("process_items", [])),
            display_parts=list(ctx.get("display_parts", [])),
            turn_started_at=str(ctx.get("turn_started_at", "")).strip(),
            turn_completed_at=completed_at,
        )
        ctx["response_meta"] = response_meta
        save_callback = ctx.get("save_callback")
        if callable(save_callback) and bool(ctx.get("save_enabled", False)):
            save_callback()


class LoopLoggingHook(LoopHook):
    """记录 loop 级 assistant 投影完成情况，便于排查归属问题。"""

    def __init__(self, *, fail_fast: bool = False, order: int = 1100) -> None:
        super().__init__(name="loop_logging", fail_fast=fail_fast, order=order)

    def after_loop(self, ctx: LoopHookContext) -> None:
        assistant_message = ctx.get("assistant_message")
        if not isinstance(assistant_message, dict):
            return
        info = assistant_message.get("info", {})
        logger.info(
            (
                "loop.finish session_id=%s round=%s agent=%s depth=%s message_id=%s "
                "process_items=%s display_parts=%s status=%s finish_reason=%s"
            ),
            ctx.get("session_id", ""),
            ctx.get("round_no", 0),
            ctx.get("agent", ""),
            ctx.get("depth", 0),
            info.get("message_id", ""),
            len(ctx.get("process_items", [])),
            len(ctx.get("display_parts", [])),
            info.get("status", ""),
            info.get("finish_reason", ""),
            extra=build_log_extra(
                agent=str(info.get("agent", "")).strip(),
                model=str(info.get("model", "")).strip(),
            ),
        )


_GLOBAL_LOOP_HOOKS: list[LoopHook] = []
_DISPATCHER = HookDispatcher[LoopHook, LoopHookContext, LoopNormalizedError](logger=logger, name="loop")


def register_global_loop_hook(hook: LoopHook) -> None:
    _GLOBAL_LOOP_HOOKS.append(hook)


def clear_global_loop_hooks() -> None:
    _GLOBAL_LOOP_HOOKS.clear()


def get_global_loop_hooks() -> list[LoopHook]:
    return list(_GLOBAL_LOOP_HOOKS)


def resolve_effective_loop_hooks(hooks: list[LoopHook] | None = None) -> list[LoopHook]:
    combined = get_global_loop_hooks() + (hooks or [])
    return [item for _, item in sorted(enumerate(combined), key=lambda pair: (pair[1].order, pair[0]))]


def invoke_loop_hook(
    hook: LoopHook,
    stage: str,
    *,
    ctx: LoopHookContext,
    error: Exception | None = None,
    normalized_error: LoopNormalizedError | None = None,
) -> None:
    result = ctx.get("assistant_message") if stage == "after" else None
    _DISPATCHER.dispatch(
        hook,
        stage,
        ctx=ctx,
        result=result,
        error=error,
        normalized_error=normalized_error,
        on_before=lambda h, context: h.before_loop(context),
        on_after=lambda h, context, result: h.after_loop(context),
        on_error=lambda h, context, exc, norm: h.on_loop_error(context, exc, norm),
    )


def _default_loop_hooks() -> None:
    if not any(isinstance(hook, LoopPersistenceHook) for hook in _GLOBAL_LOOP_HOOKS):
        register_global_loop_hook(LoopPersistenceHook())
    if not any(isinstance(hook, LoopLoggingHook) for hook in _GLOBAL_LOOP_HOOKS):
        register_global_loop_hook(LoopLoggingHook())


_default_loop_hooks()
