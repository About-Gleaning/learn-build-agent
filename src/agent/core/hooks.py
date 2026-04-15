from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Literal, Mapping, Sequence, TypeVar

HookT = TypeVar("HookT")
ContextT = TypeVar("ContextT")
ErrorT = TypeVar("ErrorT")

HookSource = Literal["builtin", "plugin"]
HookRunMode = Literal["sync", "async", "fire_and_forget"]
HookDecision = Literal["allow", "deny", "modify", "pause", "fail"]
HookStage = Literal["before", "after", "error", "finally"]


class HookEvent(dict[str, Any]):
    """Hook 事件标识，描述当前扩展点是什么。"""


class HookContext(dict[str, Any]):
    """统一 Hook 入参。

    结构固定为 event/identity/agent/runtime/data，具体 Hook 点只扩展 data，
    避免每类 Hook 把字段平铺到同一个 context 里。
    """


class HookResult(dict[str, Any]):
    """统一 Hook 返回值。

    decision:
    - allow：放行
    - deny：阻断主流程
    - modify：应用 patch 后继续
    - pause：进入人工确认或等待外部输入
    - fail：Hook 主动声明失败
    """


@dataclass(frozen=True)
class HookFilter:
    """通用 Hook 过滤条件。空集合表示不过滤。"""

    agent_names: frozenset[str] = frozenset()
    agent_kinds: frozenset[str] = frozenset()
    modes: frozenset[str] = frozenset()
    tool_names: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    models: frozenset[str] = frozenset()
    depth_min: int | None = None
    depth_max: int | None = None


@dataclass
class HookSpec:
    """Hook 注册元信息，系统内置 Hook 与插件 Hook 共享。"""

    name: str
    source: HookSource = "builtin"
    order: int = 1000
    fail_fast: bool = False
    enabled: bool = True
    timeout_ms: int | None = None
    run_mode: HookRunMode = "sync"
    filters: HookFilter = field(default_factory=HookFilter)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_hook_context(
    *,
    scope: str,
    name: str,
    stage: str,
    timestamp: str = "",
    identity: Mapping[str, Any] | None = None,
    agent: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
) -> HookContext:
    return HookContext(
        {
            "event": {
                "scope": scope,
                "name": name,
                "stage": stage,
                "timestamp": timestamp,
            },
            "identity": dict(identity or {}),
            "agent": dict(agent or {}),
            "runtime": dict(runtime or {}),
            "data": dict(data or {}),
        }
    )


def allow_hook_result(**metadata: Any) -> HookResult:
    return HookResult({"decision": "allow", "metadata": metadata})


def deny_hook_result(reason: str, **metadata: Any) -> HookResult:
    return HookResult({"decision": "deny", "reason": reason, "metadata": metadata})


def modify_hook_result(patch: Mapping[str, Any], **metadata: Any) -> HookResult:
    return HookResult({"decision": "modify", "patch": dict(patch), "metadata": metadata})


def pause_hook_result(reason: str, **metadata: Any) -> HookResult:
    return HookResult({"decision": "pause", "reason": reason, "metadata": metadata})


def fail_hook_result(reason: str, **metadata: Any) -> HookResult:
    return HookResult({"decision": "fail", "reason": reason, "metadata": metadata})


def _normalized_set(values: frozenset[str]) -> set[str]:
    return {item.strip().lower() for item in values if item.strip()}


def _value_matches(allowed: frozenset[str], value: Any) -> bool:
    normalized_allowed = _normalized_set(allowed)
    if not normalized_allowed:
        return True
    normalized_value = str(value or "").strip().lower()
    return normalized_value in normalized_allowed


def hook_matches_filter(filters: HookFilter, ctx: Mapping[str, Any]) -> bool:
    agent = ctx.get("agent", {}) if isinstance(ctx.get("agent"), Mapping) else {}
    runtime = ctx.get("runtime", {}) if isinstance(ctx.get("runtime"), Mapping) else {}
    data = ctx.get("data", {}) if isinstance(ctx.get("data"), Mapping) else {}

    if not _value_matches(filters.agent_names, agent.get("name", "")):
        return False
    if not _value_matches(filters.agent_kinds, agent.get("kind", "")):
        return False
    if not _value_matches(filters.modes, runtime.get("mode", "")):
        return False
    if not _value_matches(filters.providers, runtime.get("provider", "")):
        return False
    if not _value_matches(filters.models, runtime.get("model", "")):
        return False
    if not _value_matches(filters.tool_names, data.get("tool_name", "")):
        return False

    try:
        depth = int(agent.get("depth", 0) or 0)
    except (TypeError, ValueError):
        depth = 0
    if filters.depth_min is not None and depth < filters.depth_min:
        return False
    if filters.depth_max is not None and depth > filters.depth_max:
        return False
    return True


def hook_order_key(index_and_hook: tuple[int, Any]) -> tuple[int, int]:
    index, hook = index_and_hook
    return int(getattr(hook, "order", 1000)), index


def ordered_hooks(hooks: Sequence[HookT], *, reverse: bool = False) -> list[HookT]:
    ordered = [item for _, item in sorted(enumerate(hooks), key=hook_order_key)]
    return list(reversed(ordered)) if reverse else ordered


class HookExecutionError(RuntimeError):
    """Hook 执行失败或主动阻断时抛出的统一异常。"""


class BaseHook:
    """统一 Hook 基类，适用于系统内置 Hook 与插件 Hook。"""

    def __init__(
        self,
        name: str,
        *,
        source: HookSource = "builtin",
        order: int = 1000,
        fail_fast: bool = False,
        enabled: bool = True,
        timeout_ms: int | None = None,
        run_mode: HookRunMode = "sync",
        filters: HookFilter | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.source = source
        self.order = order
        self.fail_fast = fail_fast
        self.enabled = enabled
        self.timeout_ms = timeout_ms
        self.run_mode = run_mode
        self.filters = filters or HookFilter()
        self.metadata = dict(metadata or {})

    def should_run(self, ctx: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return False
        return hook_matches_filter(self.filters, ctx)

    def handle(self, ctx: HookContext) -> HookResult | None:
        return allow_hook_result()


class HookDispatcher(Generic[HookT, ContextT, ErrorT]):
    """通用 Hook 分发器，统一 fail-open/fail-fast 行为。"""

    def __init__(self, logger: logging.Logger, name: str) -> None:
        self._logger = logger
        self._name = name

    def dispatch(
        self,
        hook: HookT,
        stage: str,
        *,
        ctx: ContextT,
        on_before: Callable[[HookT, ContextT], None],
        on_after: Callable[[HookT, ContextT, Any], None],
        on_error: Callable[[HookT, ContextT, Exception, ErrorT], None],
        result: Any | None = None,
        error: Exception | None = None,
        normalized_error: ErrorT | None = None,
    ) -> None:
        try:
            if stage == "before":
                on_before(hook, ctx)
            elif stage == "after" and result is not None:
                on_after(hook, ctx, result)
            elif stage == "error" and error is not None and normalized_error is not None:
                on_error(hook, ctx, error, normalized_error)
        except Exception as hook_exc:
            hook_name = getattr(hook, "name", "unknown")
            fail_fast = bool(getattr(hook, "fail_fast", False))
            self._logger.warning(
                "%s.hook_failed hook=%s stage=%s fail_fast=%s error=%s",
                self._name,
                hook_name,
                stage,
                fail_fast,
                f"{type(hook_exc).__name__}: {hook_exc}",
                exc_info=True,
            )
            if fail_fast:
                raise HookExecutionError(
                    f"Hook '{hook_name}' failed at stage '{stage}': {hook_exc}"
                ) from hook_exc

    def dispatch_event(
        self,
        hooks: Sequence[BaseHook],
        *,
        ctx: HookContext,
        reverse: bool = False,
    ) -> list[HookResult]:
        """分发统一 Hook 事件，并聚合 HookResult。

        这个方法服务新的插件/内置 Hook 协议；旧的 dispatch(...) 继续保留，
        用于兼容 SessionHook/LoopHook/ToolHook/LLMHook 现有调用方式。
        """
        results: list[HookResult] = []
        for hook in ordered_hooks(hooks, reverse=reverse):
            if not hook.should_run(ctx):
                continue
            try:
                result = hook.handle(ctx) or allow_hook_result()
                decision = str(result.get("decision", "allow")).strip().lower()
                if decision not in {"allow", "deny", "modify", "pause", "fail"}:
                    result = fail_hook_result(f"Hook '{hook.name}' returned invalid decision: {decision}")
                    decision = "fail"
                results.append(result)
                if decision in {"deny", "pause"}:
                    break
                if decision == "fail" and hook.fail_fast:
                    raise HookExecutionError(str(result.get("reason", "hook failed")))
            except Exception as hook_exc:
                self._logger.warning(
                    "%s.hook_failed hook=%s stage=%s fail_fast=%s error=%s",
                    self._name,
                    hook.name,
                    ctx.get("event", {}).get("stage", ""),
                    hook.fail_fast,
                    f"{type(hook_exc).__name__}: {hook_exc}",
                    exc_info=True,
                )
                if hook.fail_fast:
                    raise HookExecutionError(
                        f"Hook '{hook.name}' failed: {hook_exc}"
                    ) from hook_exc
                results.append(fail_hook_result(str(hook_exc), hook=hook.name))
        return results
