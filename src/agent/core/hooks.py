from __future__ import annotations

import logging
from typing import Any, Callable, Generic, TypeVar

HookT = TypeVar("HookT")
ContextT = TypeVar("ContextT")
ErrorT = TypeVar("ErrorT")


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
                raise RuntimeError(
                    f"Hook '{hook_name}' failed at stage '{stage}': {hook_exc}"
                ) from hook_exc
