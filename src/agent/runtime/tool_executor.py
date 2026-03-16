import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, TypedDict

from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..core.hooks import HookDispatcher
from .compaction import apply_tool_output_truncation

logger = logging.getLogger(__name__)


class ToolHookContext(TypedDict, total=False):
    session_id: str
    agent: str
    model: str
    tool_name: str
    tool_call_id: str
    arguments: str
    parsed_args: dict[str, Any]
    round_no: int
    started_at: float
    duration_ms: int
    result_size: int
    task_available: bool


class ToolNormalizedError(TypedDict, total=False):
    code: str
    message: str
    details: str


class FilePart(TypedDict):
    name: str
    path: str
    mime_type: str


class ToolResult(TypedDict, total=False):
    output: str
    metadata: dict[str, Any]
    attachments: list[FilePart]


class ToolExecutionOptions(TypedDict, total=False):
    task_available: bool
    workdir: str


ToolOutputProcessor = Callable[[ToolResult, ToolHookContext, ToolExecutionOptions], ToolResult]


class ToolHook:
    """工具调用 Hook 基类，支持调用前后与异常阶段扩展。"""

    def __init__(self, name: str, fail_fast: bool = False) -> None:
        self.name = name
        self.fail_fast = fail_fast

    def before_call(self, ctx: ToolHookContext) -> None:
        """在工具调用前执行。"""

    def after_call(self, ctx: ToolHookContext, result: ToolResult) -> None:
        """在工具调用成功后执行。"""

    def on_error(self, ctx: ToolHookContext, error: Exception, normalized_error: ToolNormalizedError) -> None:
        """在工具调用异常后执行。"""


class ToolLoggingHook(ToolHook):
    """默认工具日志 Hook，记录调用前后与异常关键信息。"""

    def __init__(self, fail_fast: bool = False) -> None:
        super().__init__(name="tool_logging", fail_fast=fail_fast)

    def before_call(self, ctx: ToolHookContext) -> None:
        logger.info(
            "tool.request tool=%s args=%s",
            ctx.get("tool_name", ""),
            sanitize_log_text(ctx.get("arguments", "")),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
        )

    def after_call(self, ctx: ToolHookContext, result: ToolResult) -> None:
        logger.info(
            "tool.response tool=%s result=%s",
            ctx.get("tool_name", ""),
            sanitize_log_text(result.get("output", "")),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
        )

    def on_error(self, ctx: ToolHookContext, error: Exception, normalized_error: ToolNormalizedError) -> None:
        logger.exception(
            "tool.error tool=%s args=%s error_code=%s error_type=%s detail=%s",
            ctx.get("tool_name", ""),
            sanitize_log_text(ctx.get("arguments", "")),
            normalized_error.get("code", "execution_error"),
            normalized_error.get("details", type(error).__name__),
            sanitize_log_text(normalized_error.get("message", str(error))),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
        )


_GLOBAL_TOOL_HOOKS: list[ToolHook] = []
_DISPATCHER = HookDispatcher[ToolHook, ToolHookContext, ToolNormalizedError](logger=logger, name="tool")


def register_global_tool_hook(hook: ToolHook) -> None:
    _GLOBAL_TOOL_HOOKS.append(hook)


def clear_global_tool_hooks() -> None:
    _GLOBAL_TOOL_HOOKS.clear()


def get_global_tool_hooks() -> list[ToolHook]:
    return list(_GLOBAL_TOOL_HOOKS)


def normalize_tool_error(exc: Exception, code: str = "execution_error") -> ToolNormalizedError:
    return {
        "code": code,
        "message": str(exc)[:300],
        "details": type(exc).__name__,
    }


def invoke_tool_hook(
    hook: ToolHook,
    stage: str,
    *,
    ctx: ToolHookContext,
    result: ToolResult | None = None,
    error: Exception | None = None,
    normalized_error: ToolNormalizedError | None = None,
) -> None:
    _DISPATCHER.dispatch(
        hook,
        stage,
        ctx=ctx,
        result=result,
        error=error,
        normalized_error=normalized_error,
        on_before=lambda h, context: h.before_call(context),
        on_after=lambda h, context, data: h.after_call(context, data),
        on_error=lambda h, context, exc, norm: h.on_error(context, exc, norm),
    )


def normalize_tool_text(result: object) -> str:
    """将工具返回值规范为字符串，避免非法结构导致模型接口报错。"""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def normalize_tool_result(result: object) -> ToolResult:
    if isinstance(result, dict):
        output = normalize_tool_text(result.get("output", ""))
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        attachments = result.get("attachments")
        normalized: ToolResult = {
            "output": output,
            "metadata": metadata,
        }
        if isinstance(attachments, list):
            valid_attachments = [item for item in attachments if isinstance(item, dict)]
            if valid_attachments:
                normalized["attachments"] = valid_attachments  # type: ignore[assignment]
        return normalized

    return {
        "output": normalize_tool_text(result),
        "metadata": {},
    }


def default_tool_output_processor(
    result: ToolResult,
    ctx: ToolHookContext,
    options: ToolExecutionOptions,
) -> ToolResult:
    metadata = dict(result.get("metadata", {}))
    truncated = apply_tool_output_truncation(
        text=result.get("output", ""),
        session_id=ctx.get("session_id", "default_session"),
        tool_name=ctx.get("tool_name", "tool"),
        tool_call_id=ctx.get("tool_call_id", "call"),
        workdir=Path(options.get("workdir", Path.cwd())),
        task_available=bool(options.get("task_available", False)),
        metadata=metadata,
    )
    processed: ToolResult = {
        "output": truncated["output"],
        "metadata": truncated["metadata"],
    }
    attachments = result.get("attachments")
    if isinstance(attachments, list):
        processed["attachments"] = attachments
    return processed


class ToolExecutor:
    """执行工具调用并分发 Hook。"""

    def __init__(
        self,
        handlers: dict[str, Callable[..., object]],
        *,
        output_processors: dict[str, ToolOutputProcessor] | None = None,
        default_output_processor: ToolOutputProcessor | None = None,
    ) -> None:
        self.handlers = handlers
        self.output_processors = output_processors or {}
        self.default_output_processor = default_output_processor or default_tool_output_processor

    def _run_tool_hooks(
        self,
        hooks: list[ToolHook],
        stage: str,
        *,
        ctx: ToolHookContext,
        result: ToolResult | None = None,
        error: Exception | None = None,
        error_code: str = "execution_error",
    ) -> None:
        normalized = normalize_tool_error(error, code=error_code) if error is not None else None
        for hook in hooks:
            invoke_tool_hook(
                hook,
                stage,
                ctx=ctx,
                result=result,
                error=error,
                normalized_error=normalized,
            )

    def execute(
        self,
        tool_name: str,
        arguments: str,
        *,
        session_id: str,
        tool_call_id: str,
        round_no: int,
        hooks: list[ToolHook],
        agent: str = "",
        model: str = "",
        task_available: bool = False,
        workdir: str | None = None,
    ) -> ToolResult:
        ctx: ToolHookContext = {
            "session_id": session_id,
            "agent": agent,
            "model": model,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "round_no": round_no,
            "task_available": task_available,
        }

        started = time.perf_counter()
        ctx["started_at"] = started
        self._run_tool_hooks(hooks, "before", ctx=ctx)

        handler = self.handlers.get(tool_name)
        if handler is None:
            ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
            err = ValueError("Unknown tool")
            self._run_tool_hooks(hooks, "error", ctx=ctx, error=err, error_code="unknown_tool")
            return {
                "output": "Error: Unknown tool",
                "metadata": {
                    "status": "failed",
                    "error_code": "unknown_tool",
                    "error_type": type(err).__name__,
                },
            }

        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be a JSON object")
            ctx["parsed_args"] = args
        except Exception as exc:
            ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
            self._run_tool_hooks(hooks, "error", ctx=ctx, error=exc, error_code="invalid_arguments")
            return {
                "output": f"Error: Invalid tool arguments: {type(exc).__name__}: {exc}",
                "metadata": {
                    "status": "failed",
                    "error_code": "invalid_arguments",
                    "error_type": type(exc).__name__,
                },
            }

        try:
            result = normalize_tool_result(handler(**args))
        except Exception as exc:
            ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
            self._run_tool_hooks(hooks, "error", ctx=ctx, error=exc, error_code="execution_error")
            return {
                "output": f"Error: Tool execution failed: {type(exc).__name__}: {exc}",
                "metadata": {
                    "status": "failed",
                    "error_code": "execution_error",
                    "error_type": type(exc).__name__,
                },
            }

        result["metadata"] = dict(result.get("metadata", {}))
        result["metadata"].setdefault("status", "completed")
        processor = self.output_processors.get(tool_name, self.default_output_processor)
        options: ToolExecutionOptions = {
            "task_available": task_available,
            "workdir": workdir or str(Path.cwd()),
        }
        result = processor(result, ctx, options)
        ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
        ctx["result_size"] = len(result.get("output", ""))
        self._run_tool_hooks(hooks, "after", ctx=ctx, result=result)
        return result


def _default_tool_hooks() -> None:
    if not any(isinstance(h, ToolLoggingHook) for h in _GLOBAL_TOOL_HOOKS):
        register_global_tool_hook(ToolLoggingHook())


_default_tool_hooks()
