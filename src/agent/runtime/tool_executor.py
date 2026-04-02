import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, TypedDict

from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..core.context import set_session_id
from ..core.hooks import HookDispatcher
from .compaction import apply_tool_output_truncation

logger = logging.getLogger(__name__)


def _build_lsp_log_fields(metadata: dict[str, Any]) -> dict[str, str] | None:
    diagnostics_status = str(metadata.get("diagnostics_status", "")).strip()
    if not diagnostics_status:
        return None
    return {
        "diagnostics_status": diagnostics_status,
        "diagnostics_total": str(metadata.get("diagnostics_total", "")),
        "raw_diagnostics_total": str(metadata.get("raw_diagnostics_total", "")),
        "diagnostics_sequence": str(metadata.get("diagnostics_sequence", "")),
        "diagnostics_previous_sequence": str(metadata.get("diagnostics_previous_sequence", "")),
        "diagnostics_latest_sequence": str(metadata.get("diagnostics_latest_sequence", "")),
        "diagnostics_wait_rounds": str(metadata.get("diagnostics_wait_rounds", "")),
        "diagnostics_wait_ms": str(metadata.get("diagnostics_wait_ms", "")),
        "diagnostics_settled": str(metadata.get("diagnostics_settled", "")),
        "diagnostics_summary": sanitize_log_text(metadata.get("diagnostics_summary", "")),
        "diagnostics_truncated": str(metadata.get("diagnostics_truncated", "")),
        "lsp_language": sanitize_log_text(metadata.get("lsp_language", "")),
        "lsp_server": sanitize_log_text(metadata.get("lsp_server", "")),
        "lsp_server_pid": str(metadata.get("lsp_server_pid", "")),
        "lsp_workspace_root": sanitize_log_text(metadata.get("lsp_workspace_root", "")),
        "lsp_data_dir": sanitize_log_text(metadata.get("lsp_data_dir", "")),
        "lsp_workspace_selection_reason": sanitize_log_text(metadata.get("lsp_workspace_selection_reason", "")),
        "lsp_server_key": sanitize_log_text(metadata.get("lsp_server_key", "")),
        "lsp_snapshot_uri": sanitize_log_text(metadata.get("lsp_snapshot_uri", "")),
        "recent_status_summary": sanitize_log_text(metadata.get("recent_status_summary", "")),
        "recent_log_summary": sanitize_log_text(metadata.get("recent_log_summary", "")),
        "recent_publish_uris": sanitize_log_text(metadata.get("recent_publish_uris", "")),
        "received_other_file_diagnostics": str(metadata.get("received_other_file_diagnostics", "")),
        "java_project_issue_code": sanitize_log_text(metadata.get("java_project_issue_code", "")),
        "java_project_state": sanitize_log_text(metadata.get("java_project_state", "")),
        "java_maven_profiles": sanitize_log_text(",".join(metadata.get("java_maven_profiles", [])) if isinstance(metadata.get("java_maven_profiles"), list) else metadata.get("java_maven_profiles", "")),
        "java_maven_profiles_source": sanitize_log_text(metadata.get("java_maven_profiles_source", "")),
        "java_maven_local_repository": sanitize_log_text(metadata.get("java_maven_local_repository", "")),
        "java_debug_observation_enabled": str(metadata.get("java_debug_observation_enabled", "")),
        "debug_status_events": sanitize_log_text(metadata.get("debug_status_events", "")),
        "debug_log_events": sanitize_log_text(metadata.get("debug_log_events", "")),
        "debug_publish_events": sanitize_log_text(metadata.get("debug_publish_events", "")),
        "debug_issue_probe": sanitize_log_text(metadata.get("debug_issue_probe", "")),
        "lsp_error": sanitize_log_text(metadata.get("lsp_error", "")),
    }


def _build_lsp_query_log_fields(metadata: dict[str, Any]) -> dict[str, str] | None:
    operation = str(metadata.get("lsp_operation", "")).strip()
    if not operation:
        return None
    result = metadata.get("result")
    if isinstance(result, list):
        result_kind = "list"
        result_count = str(len(result))
    elif isinstance(result, dict):
        result_kind = "object"
        result_count = "1"
    elif result is None:
        result_kind = "null"
        result_count = "0"
    else:
        result_kind = type(result).__name__
        result_count = str(metadata.get("result_count", ""))
    return {
        "lsp_operation": operation,
        "result_kind": result_kind,
        "result_count": result_count,
        "lsp_language": sanitize_log_text(metadata.get("lsp_language", "")),
        "lsp_server": sanitize_log_text(metadata.get("lsp_server", "")),
        "lsp_server_pid": str(metadata.get("lsp_server_pid", "")),
        "lsp_workspace_root": sanitize_log_text(metadata.get("lsp_workspace_root", "")),
        "lsp_data_dir": sanitize_log_text(metadata.get("lsp_data_dir", "")),
        "lsp_workspace_selection_reason": sanitize_log_text(metadata.get("lsp_workspace_selection_reason", "")),
        "lsp_server_key": sanitize_log_text(metadata.get("lsp_server_key", "")),
        "lsp_snapshot_uri": sanitize_log_text(metadata.get("lsp_snapshot_uri", "")),
        "lsp_error": sanitize_log_text(metadata.get("lsp_error", "")),
    }


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
    id: str
    sessionID: str
    messageID: str
    type: str
    mime: str
    url: str


class ToolResult(TypedDict, total=False):
    output: str
    metadata: dict[str, Any]
    attachments: list[FilePart]


class ToolExecutionOptions(TypedDict, total=False):
    task_available: bool
    workdir: str
    vendor: str


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
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        lsp_log_fields = _build_lsp_log_fields(metadata)
        if lsp_log_fields is not None:
            logger.info(
                (
                    "tool.lsp_result tool=%s diagnostics_status=%s diagnostics_total=%s "
                    "raw_diagnostics_total=%s diagnostics_sequence=%s diagnostics_previous_sequence=%s "
                    "diagnostics_latest_sequence=%s diagnostics_wait_rounds=%s diagnostics_wait_ms=%s "
                    "diagnostics_settled=%s diagnostics_summary=%s diagnostics_truncated=%s "
                    "lsp_language=%s lsp_server=%s lsp_server_pid=%s lsp_workspace_root=%s "
                    "lsp_data_dir=%s "
                    "lsp_workspace_selection_reason=%s "
                    "lsp_server_key=%s lsp_snapshot_uri=%s recent_status_summary=%s recent_log_summary=%s "
                    "recent_publish_uris=%s received_other_file_diagnostics=%s "
                    "java_project_issue_code=%s java_project_state=%s java_maven_profiles=%s "
                    "java_maven_profiles_source=%s "
                    "java_maven_local_repository=%s "
                    "java_debug_observation_enabled=%s debug_status_events=%s debug_log_events=%s "
                    "debug_publish_events=%s debug_issue_probe=%s lsp_error=%s"
                ),
                ctx.get("tool_name", ""),
                lsp_log_fields["diagnostics_status"],
                lsp_log_fields["diagnostics_total"],
                lsp_log_fields["raw_diagnostics_total"],
                lsp_log_fields["diagnostics_sequence"],
                lsp_log_fields["diagnostics_previous_sequence"],
                lsp_log_fields["diagnostics_latest_sequence"],
                lsp_log_fields["diagnostics_wait_rounds"],
                lsp_log_fields["diagnostics_wait_ms"],
                lsp_log_fields["diagnostics_settled"],
                lsp_log_fields["diagnostics_summary"],
                lsp_log_fields["diagnostics_truncated"],
                lsp_log_fields["lsp_language"],
                lsp_log_fields["lsp_server"],
                lsp_log_fields["lsp_server_pid"],
                lsp_log_fields["lsp_workspace_root"],
                lsp_log_fields["lsp_data_dir"],
                lsp_log_fields["lsp_workspace_selection_reason"],
                lsp_log_fields["lsp_server_key"],
                lsp_log_fields["lsp_snapshot_uri"],
                lsp_log_fields["recent_status_summary"],
                lsp_log_fields["recent_log_summary"],
                lsp_log_fields["recent_publish_uris"],
                lsp_log_fields["received_other_file_diagnostics"],
                lsp_log_fields["java_project_issue_code"],
                lsp_log_fields["java_project_state"],
                lsp_log_fields["java_maven_profiles"],
                lsp_log_fields["java_maven_profiles_source"],
                lsp_log_fields["java_maven_local_repository"],
                lsp_log_fields["java_debug_observation_enabled"],
                lsp_log_fields["debug_status_events"],
                lsp_log_fields["debug_log_events"],
                lsp_log_fields["debug_publish_events"],
                lsp_log_fields["debug_issue_probe"],
                lsp_log_fields["lsp_error"],
                extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
            )
        lsp_query_log_fields = _build_lsp_query_log_fields(metadata)
        if lsp_query_log_fields is not None:
            logger.info(
                (
                    "tool.lsp_query_result tool=%s lsp_operation=%s result_kind=%s result_count=%s "
                    "lsp_language=%s lsp_server=%s lsp_server_pid=%s lsp_workspace_root=%s "
                    "lsp_data_dir=%s lsp_workspace_selection_reason=%s lsp_server_key=%s "
                    "lsp_snapshot_uri=%s lsp_error=%s"
                ),
                ctx.get("tool_name", ""),
                lsp_query_log_fields["lsp_operation"],
                lsp_query_log_fields["result_kind"],
                lsp_query_log_fields["result_count"],
                lsp_query_log_fields["lsp_language"],
                lsp_query_log_fields["lsp_server"],
                lsp_query_log_fields["lsp_server_pid"],
                lsp_query_log_fields["lsp_workspace_root"],
                lsp_query_log_fields["lsp_data_dir"],
                lsp_query_log_fields["lsp_workspace_selection_reason"],
                lsp_query_log_fields["lsp_server_key"],
                lsp_query_log_fields["lsp_snapshot_uri"],
                lsp_query_log_fields["lsp_error"],
                extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
            )
        if metadata.get("truncated") is True:
            logger.info(
                (
                    "tool.output_truncated tool=%s session_id=%s tool_call_id=%s "
                    "full_output_path=%s write_error=%s original_lines=%s "
                    "original_bytes=%s preview_lines=%s preview_bytes=%s"
                ),
                ctx.get("tool_name", ""),
                ctx.get("session_id", ""),
                ctx.get("tool_call_id", ""),
                sanitize_log_text(metadata.get("full_output_path", "")),
                sanitize_log_text(metadata.get("full_output_write_error", "")),
                metadata.get("original_lines", ""),
                metadata.get("original_bytes", ""),
                metadata.get("preview_lines", ""),
                metadata.get("preview_bytes", ""),
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
        session_id=str(ctx["session_id"]),
        tool_name=ctx.get("tool_name", "tool"),
        tool_call_id=ctx.get("tool_call_id", "call"),
        workdir=Path(options.get("workdir", Path.cwd())),
        task_available=bool(options.get("task_available", False)),
        vendor=str(options.get("vendor", "")).strip() or None,
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
        vendor: str = "",
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
            # 工具层仍有部分逻辑通过 contextvar 读取 session_id，这里在调用前显式同步。
            set_session_id(session_id)
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
            "vendor": vendor,
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
