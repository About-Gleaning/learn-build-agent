from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from ...config.logging_setup import build_log_extra, sanitize_log_text
from ...config.settings import resolve_logging_settings
from ...core.hooks import HookFilter, hook_matches_filter
from ...core.message import Message, extract_reasoning_content, extract_tool_calls

logger = logging.getLogger(__name__)


class HookContext(TypedDict, total=False):
    session_id: str
    agent: str
    provider: str
    model: str
    api_mode: str
    parent_id: str
    max_tokens: int
    message_count: int
    tools_count: int
    request_size: int
    request_payload: dict[str, Any]
    source_messages: list[Message]
    start_time: float
    latency_ms: int


class LLMHook:
    """LLM 调用 Hook 基类，支持调用前后与错误阶段扩展。"""

    def __init__(
        self,
        name: str,
        fail_fast: bool = False,
        *,
        order: int = 1000,
        enabled: bool = True,
        filters: HookFilter | None = None,
    ) -> None:
        self.name = name
        self.fail_fast = fail_fast
        self.order = order
        self.enabled = enabled
        self.filters = filters or HookFilter()

    def should_run(self, ctx: HookContext) -> bool:
        if not self.enabled:
            return False
        unified_ctx = {
            "agent": {"name": ctx.get("agent", "")},
            "runtime": {
                "provider": ctx.get("provider", ""),
                "model": ctx.get("model", ""),
            },
            "data": {},
        }
        return hook_matches_filter(self.filters, unified_ctx)

    def before_call(self, ctx: HookContext) -> None:
        """在调用 provider 之前执行。"""

    def after_call(self, ctx: HookContext, message: Message) -> None:
        """在调用 provider 成功后执行。"""

    def on_error(self, ctx: HookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        """在调用 provider 异常后执行。"""


class LoggingHook(LLMHook):
    """默认日志 Hook，记录调用前后与异常关键信息。"""

    def __init__(self, fail_fast: bool = False, *, order: int = 1000) -> None:
        super().__init__(name="logging", fail_fast=fail_fast, order=order)

    def before_call(self, ctx: HookContext) -> None:
        log_extra = build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", ""))
        fields = [
            f"api_mode={ctx.get('api_mode', 'unknown')}",
            _build_request_messages_log_field(
                ctx.get("request_payload", {}),
                ctx.get("api_mode", ""),
                resolve_logging_settings().llm_request_messages_mode,
            ),
        ]
        logger.info("llm.request %s", " ".join(fields), extra=log_extra)

    def after_call(self, ctx: HookContext, message: Message) -> None:
        info_text = " ".join(_build_response_log_fields(ctx, message))

        logger.info(
            "llm.response %s",
            info_text,
            extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
        )

    def on_error(self, ctx: HookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        logger.exception(
            "llm.error error_code=%s error_type=%s detail=%s",
            normalized_error.get("code", "api_error"),
            normalized_error.get("details", type(error).__name__),
            sanitize_log_text(normalized_error.get("message", str(error))),
            extra=build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", "")),
        )


def _sanitize_request_log_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key == "file_data" and isinstance(item, str):
                sanitized[normalized_key] = f"[omitted_file_data length={len(item)}]"
                continue
            if normalized_key == "url" and isinstance(item, str) and item.startswith("data:"):
                mime = item.split(";", 1)[0][5:] or "unknown"
                sanitized[normalized_key] = f"[omitted_data_url mime={mime} length={len(item)}]"
                continue
            sanitized[normalized_key] = _sanitize_request_log_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_request_log_payload(item) for item in value]
    return value


def _build_request_messages_log_field(
    request_payload: dict[str, Any],
    api_mode: str,
    messages_mode: str = "full",
) -> str:
    payload_key = "input" if str(api_mode).strip() == "responses" else "messages"
    payload = request_payload.get(payload_key, [])
    if str(messages_mode).strip().lower() == "latest" and isinstance(payload, list):
        payload = payload[-1:]
    serialized = json.dumps(
        _sanitize_request_log_payload(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"{payload_key}={sanitize_log_text(serialized)}"


def _build_response_preview(message: Message) -> str:
    text_parts = [
        str(part.get("content", "")).strip()
        for part in message.get("parts", [])
        if part.get("type") in {"text", "error"} and str(part.get("content", "")).strip()
    ]
    return sanitize_log_text("\n".join(text_parts))


def _build_reasoning_preview(message: Message) -> str:
    return sanitize_log_text(extract_reasoning_content(message))


def _build_tool_names_preview(message: Message) -> str:
    tool_names = [tool_call["name"] for tool_call in extract_tool_calls(message) if tool_call.get("name")]
    return sanitize_log_text(",".join(tool_names))


def _build_tool_calls_preview(message: Message) -> str:
    previews: list[str] = []
    for tool_call in extract_tool_calls(message):
        name = str(tool_call.get("name", "")).strip() or "unknown"
        tool_call_id = str(tool_call.get("id", "")).strip() or "unknown"
        arguments = str(tool_call.get("arguments", "")).strip() or "{}"
        previews.append(f"{name}[{tool_call_id}] args={arguments}")
    return sanitize_log_text("; ".join(previews), limit=1000)


def _build_response_log_fields(ctx: HookContext, message: Message) -> list[str]:
    finish_reason = sanitize_log_text(message.get("info", {}).get("finish_reason", "unknown"), limit=80)
    latency_ms = int(ctx.get("latency_ms", 0) or 0)
    fields = [
        f"finish_reason={finish_reason}",
        f"latency_ms={latency_ms}",
        f"message={_build_response_preview(message)}",
    ]

    reasoning_preview = _build_reasoning_preview(message)
    if reasoning_preview:
        fields.append(f"reasoning={reasoning_preview}")

    tool_names_preview = _build_tool_names_preview(message)
    if tool_names_preview:
        fields.append(f"tool_names={tool_names_preview}")

    tool_calls_preview = _build_tool_calls_preview(message)
    if tool_calls_preview:
        fields.append(f"tool_calls={tool_calls_preview}")

    return fields
