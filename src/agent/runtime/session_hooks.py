import logging
from typing import Any, Literal, TypedDict

from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..core.hooks import HookDispatcher

logger = logging.getLogger(__name__)

AgentKind = Literal["primary", "subagent"]


class SessionHookContext(TypedDict, total=False):
    session_id: str
    agent: str
    agent_kind: AgentKind
    depth: int
    stream: bool
    delegation_id: str
    parent_tool_call_id: str
    turn_started_at: str
    max_rounds: int
    round_count: int
    status: str
    finish_reason: str
    latency_ms: int
    user_input: str
    mode: str


class SessionNormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class SessionHook:
    """会话生命周期 Hook 基类，支持前后与异常阶段扩展。"""

    def __init__(
        self,
        name: str,
        *,
        fail_fast: bool = False,
        order: int = 1000,
        agent_kinds: set[AgentKind] | None = None,
        agent_names: set[str] | None = None,
    ) -> None:
        self.name = name
        self.fail_fast = fail_fast
        self.order = order
        self.agent_kinds = set(agent_kinds) if agent_kinds else None
        self.agent_names = {item.strip().lower() for item in agent_names if item.strip()} if agent_names else None

    def should_run(self, ctx: SessionHookContext) -> bool:
        ctx_agent_kind = str(ctx.get("agent_kind", "")).strip().lower()
        ctx_agent_name = str(ctx.get("agent", "")).strip().lower()
        if self.agent_kinds is not None and ctx_agent_kind not in self.agent_kinds:
            return False
        if self.agent_names is not None and ctx_agent_name not in self.agent_names:
            return False
        return True

    def before_session(self, ctx: SessionHookContext) -> None:
        """在 session 执行前触发。"""

    def after_session(self, ctx: SessionHookContext, message: dict[str, Any]) -> None:
        """在 session 成功结束后触发。"""

    def on_error(self, ctx: SessionHookContext, error: Exception, normalized_error: SessionNormalizedError) -> None:
        """在 session 抛出异常时触发。"""


class SessionLoggingHook(SessionHook):
    """默认会话日志 Hook，记录会话前后与异常关键信息。"""

    def __init__(self, *, fail_fast: bool = False, order: int = 1000) -> None:
        super().__init__(name="session_logging", fail_fast=fail_fast, order=order)

    def before_session(self, ctx: SessionHookContext) -> None:
        logger.info(
            (
                "session.start session_id=%s agent=%s agent_kind=%s depth=%s stream=%s "
                "delegation_id=%s parent_tool_call_id=%s max_rounds=%s mode=%s user_input=%s"
            ),
            ctx.get("session_id", ""),
            ctx.get("agent", ""),
            ctx.get("agent_kind", ""),
            ctx.get("depth", 0),
            ctx.get("stream", False),
            ctx.get("delegation_id", ""),
            ctx.get("parent_tool_call_id", ""),
            ctx.get("max_rounds", 0),
            ctx.get("mode", ""),
            sanitize_log_text(ctx.get("user_input", "")),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=""),
        )

    def after_session(self, ctx: SessionHookContext, message: dict[str, Any]) -> None:
        logger.info(
            (
                "session.finish session_id=%s agent=%s agent_kind=%s depth=%s stream=%s "
                "delegation_id=%s parent_tool_call_id=%s round_count=%s status=%s "
                "finish_reason=%s latency_ms=%s"
            ),
            ctx.get("session_id", ""),
            ctx.get("agent", ""),
            ctx.get("agent_kind", ""),
            ctx.get("depth", 0),
            ctx.get("stream", False),
            ctx.get("delegation_id", ""),
            ctx.get("parent_tool_call_id", ""),
            ctx.get("round_count", 0),
            ctx.get("status", message.get("info", {}).get("status", "")),
            ctx.get("finish_reason", message.get("info", {}).get("finish_reason", "")),
            ctx.get("latency_ms", 0),
            extra=build_log_extra(
                agent=ctx.get("agent", ""),
                model=str(message.get("info", {}).get("model", "")).strip(),
            ),
        )

    def on_error(self, ctx: SessionHookContext, error: Exception, normalized_error: SessionNormalizedError) -> None:
        logger.exception(
            (
                "session.error session_id=%s agent=%s agent_kind=%s depth=%s stream=%s "
                "delegation_id=%s parent_tool_call_id=%s round_count=%s latency_ms=%s "
                "error_code=%s error_type=%s detail=%s"
            ),
            ctx.get("session_id", ""),
            ctx.get("agent", ""),
            ctx.get("agent_kind", ""),
            ctx.get("depth", 0),
            ctx.get("stream", False),
            ctx.get("delegation_id", ""),
            ctx.get("parent_tool_call_id", ""),
            ctx.get("round_count", 0),
            ctx.get("latency_ms", 0),
            normalized_error.get("code", "session_error"),
            normalized_error.get("details", type(error).__name__),
            sanitize_log_text(normalized_error.get("message", str(error))),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=""),
        )


_GLOBAL_SESSION_HOOKS: list[SessionHook] = []
_DISPATCHER = HookDispatcher[SessionHook, SessionHookContext, SessionNormalizedError](logger=logger, name="session")


def register_global_session_hook(hook: SessionHook) -> None:
    _GLOBAL_SESSION_HOOKS.append(hook)


def clear_global_session_hooks() -> None:
    _GLOBAL_SESSION_HOOKS.clear()


def get_global_session_hooks() -> list[SessionHook]:
    return list(_GLOBAL_SESSION_HOOKS)


def resolve_effective_session_hooks(hooks: list[SessionHook] | None = None) -> list[SessionHook]:
    combined = get_global_session_hooks() + (hooks or [])
    return [item for _, item in sorted(enumerate(combined), key=lambda pair: (pair[1].order, pair[0]))]


def invoke_session_hook(
    hook: SessionHook,
    stage: str,
    *,
    ctx: SessionHookContext,
    message: dict[str, Any] | None = None,
    error: Exception | None = None,
    normalized_error: SessionNormalizedError | None = None,
) -> None:
    if not hook.should_run(ctx):
        return
    _DISPATCHER.dispatch(
        hook,
        stage,
        ctx=ctx,
        result=message,
        error=error,
        normalized_error=normalized_error,
        on_before=lambda h, context: h.before_session(context),
        on_after=lambda h, context, result: h.after_session(context, result),
        on_error=lambda h, context, exc, norm: h.on_error(context, exc, norm),
    )


def _default_session_hooks() -> None:
    if not any(isinstance(hook, SessionLoggingHook) for hook in _GLOBAL_SESSION_HOOKS):
        register_global_session_hook(SessionLoggingHook())


_default_session_hooks()
