import logging
from typing import Any, TypedDict

from ..core.hooks import HookDispatcher, HookFilter, hook_matches_filter, ordered_hooks

logger = logging.getLogger(__name__)


class DelegationHookContext(TypedDict, total=False):
    session_id: str
    turn_id: str
    round_id: str
    parent_message_id: str
    delegation_id: str
    parent_tool_call_id: str
    parent_agent: str
    agent: str
    agent_kind: str
    depth: int
    mode: str
    provider: str
    model: str
    prompt: str
    status: str
    finish_reason: str
    result_message_id: str
    output_preview: str
    metadata: dict[str, Any]


class DelegationNormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class DelegationHook:
    """Subagent 委派生命周期 Hook。

    task 虽然以工具形式暴露，但它会启动子 agent session。这个 Hook 专门表达
    父子 agent 的委派关系，避免把 delegation 生命周期硬塞进普通 ToolHook。
    """

    def __init__(
        self,
        name: str,
        *,
        fail_fast: bool = False,
        order: int = 1000,
        enabled: bool = True,
        filters: HookFilter | None = None,
    ) -> None:
        self.name = name
        self.fail_fast = fail_fast
        self.order = order
        self.enabled = enabled
        self.filters = filters or HookFilter()

    def should_run(self, ctx: DelegationHookContext) -> bool:
        if not self.enabled:
            return False
        unified_ctx = {
            "agent": {
                "name": ctx.get("agent", ""),
                "kind": ctx.get("agent_kind", ""),
                "depth": ctx.get("depth", 0),
            },
            "runtime": {
                "mode": ctx.get("mode", ""),
                "provider": ctx.get("provider", ""),
                "model": ctx.get("model", ""),
            },
            "data": {},
        }
        return hook_matches_filter(self.filters, unified_ctx)

    def on_delegation_requested(self, ctx: DelegationHookContext) -> None:
        """父 agent 决定发起 task 委派时触发。"""

    def on_delegation_started(self, ctx: DelegationHookContext) -> None:
        """子 agent session 即将开始时触发。"""

    def on_delegation_completed(self, ctx: DelegationHookContext) -> None:
        """子 agent session 成功完成时触发。"""

    def on_delegation_failed(
        self,
        ctx: DelegationHookContext,
        error: Exception,
        normalized_error: DelegationNormalizedError,
    ) -> None:
        """子 agent session 异常失败时触发。"""

    def on_delegation_interrupted(self, ctx: DelegationHookContext) -> None:
        """子 agent session 因 question/confirmation/stop 等受控中断时触发。"""

    def on_delegation_finally(self, ctx: DelegationHookContext) -> None:
        """委派流程无论成功失败都触发，用于清理与 flush。"""


_GLOBAL_DELEGATION_HOOKS: list[DelegationHook] = []
_DISPATCHER = HookDispatcher[DelegationHook, DelegationHookContext, DelegationNormalizedError](
    logger=logger,
    name="delegation",
)


def register_global_delegation_hook(hook: DelegationHook) -> None:
    _GLOBAL_DELEGATION_HOOKS.append(hook)


def clear_global_delegation_hooks() -> None:
    _GLOBAL_DELEGATION_HOOKS.clear()


def get_global_delegation_hooks() -> list[DelegationHook]:
    return list(_GLOBAL_DELEGATION_HOOKS)


def resolve_effective_delegation_hooks(hooks: list[DelegationHook] | None = None) -> list[DelegationHook]:
    return ordered_hooks(get_global_delegation_hooks() + (hooks or []))


def normalize_delegation_error(exc: Exception, code: str = "delegation_error") -> DelegationNormalizedError:
    return {
        "code": code,
        "message": str(exc)[:300],
        "details": type(exc).__name__,
    }


def invoke_delegation_hook(
    hook: DelegationHook,
    stage: str,
    *,
    ctx: DelegationHookContext,
    error: Exception | None = None,
    normalized_error: DelegationNormalizedError | None = None,
) -> None:
    if not hook.should_run(ctx):
        return
    def _run_requested(h: DelegationHook, context: DelegationHookContext) -> None:
        h.on_delegation_requested(context)

    def _run_started(h: DelegationHook, context: DelegationHookContext) -> None:
        h.on_delegation_started(context)

    def _run_completed(h: DelegationHook, context: DelegationHookContext, _result: Any) -> None:
        h.on_delegation_completed(context)

    def _run_failed(
        h: DelegationHook,
        context: DelegationHookContext,
        exc: Exception,
        norm: DelegationNormalizedError,
    ) -> None:
        h.on_delegation_failed(context, exc, norm)

    def _run_interrupted(h: DelegationHook, context: DelegationHookContext) -> None:
        h.on_delegation_interrupted(context)

    def _run_finally(h: DelegationHook, context: DelegationHookContext) -> None:
        h.on_delegation_finally(context)

    dispatch_stage = stage
    on_before = _run_started
    on_after = _run_completed
    on_error = _run_failed
    if stage == "requested":
        dispatch_stage = "before"
        on_before = _run_requested
    elif stage == "interrupted":
        dispatch_stage = "before"
        on_before = _run_interrupted
    elif stage == "finally":
        dispatch_stage = "before"
        on_before = _run_finally
    _DISPATCHER.dispatch(
        hook,
        dispatch_stage,
        ctx=ctx,
        result={},
        error=error,
        normalized_error=normalized_error,
        on_before=on_before,
        on_after=on_after,
        on_error=on_error,
    )


def run_delegation_hooks(
    hooks: list[DelegationHook],
    stage: str,
    *,
    ctx: DelegationHookContext,
    error: Exception | None = None,
    normalized_error: DelegationNormalizedError | None = None,
) -> None:
    reverse = stage in {"after", "error", "finally"}
    for hook in ordered_hooks(hooks, reverse=reverse):
        invoke_delegation_hook(
            hook,
            "before" if stage == "started" else stage,
            ctx=ctx,
            error=error,
            normalized_error=normalized_error,
        )
