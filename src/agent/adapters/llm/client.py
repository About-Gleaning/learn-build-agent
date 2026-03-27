import json
import logging
import time
from collections.abc import Generator
from typing import Any, TypedDict

from openai import OpenAI

from ...config.logging_setup import build_log_extra, sanitize_log_text
from ...config.settings import ResolvedLLMConfig, resolve_llm_config
from ...core.hooks import HookDispatcher
from ...core.message import (
    Message,
    create_error_message,
    estimate_message_size,
    extract_reasoning_content,
    extract_tool_calls,
    normalize_error,
)
from .protocols import ProviderAdapter, normalize_responses_tools
from .vendors import build_provider_adapter
logger = logging.getLogger(__name__)

# 兼容现有测试与内部调用路径，继续从 client 暴露该辅助函数。
_normalize_responses_tools = normalize_responses_tools


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

    def __init__(self, name: str, fail_fast: bool = False) -> None:
        self.name = name
        self.fail_fast = fail_fast

    def before_call(self, ctx: HookContext) -> None:
        """在调用 provider 之前执行。"""

    def after_call(self, ctx: HookContext, message: Message) -> None:
        """在调用 provider 成功后执行。"""

    def on_error(self, ctx: HookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        """在调用 provider 异常后执行。"""


class LoggingHook(LLMHook):
    """默认日志 Hook，记录调用前后与异常关键信息。"""

    def __init__(self, fail_fast: bool = False) -> None:
        super().__init__(name="logging", fail_fast=fail_fast)

    def before_call(self, ctx: HookContext) -> None:
        log_extra = build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", ""))
        fields = [
            f"api_mode={ctx.get('api_mode', 'unknown')}",
            _build_request_messages_log_field(ctx.get("request_payload", {}), ctx.get("api_mode", "")),
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


def _build_request_messages_log_field(request_payload: dict[str, Any], api_mode: str) -> str:
    payload_key = "input" if str(api_mode).strip() == "responses" else "messages"
    payload = request_payload.get(payload_key, [])
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


_GLOBAL_HOOKS: list[LLMHook] = []
_DISPATCHER = HookDispatcher[LLMHook, HookContext, dict[str, str]](logger=logger, name="llm")


def register_global_hook(hook: LLMHook) -> None:
    _GLOBAL_HOOKS.append(hook)


def clear_global_hooks() -> None:
    _GLOBAL_HOOKS.clear()


def get_global_hooks() -> list[LLMHook]:
    return list(_GLOBAL_HOOKS)


def _invoke_hook(
    hook: LLMHook,
    stage: str,
    *,
    ctx: HookContext,
    message: Message | None = None,
    error: Exception | None = None,
    normalized_error: dict[str, str] | None = None,
) -> None:
    _DISPATCHER.dispatch(
        hook,
        stage,
        ctx=ctx,
        result=message,
        error=error,
        normalized_error=normalized_error,
        on_before=lambda h, context: h.before_call(context),
        on_after=lambda h, context, result: h.after_call(context, result),
        on_error=lambda h, context, exc, norm: h.on_error(context, exc, norm),
    )


def _default_hooks() -> None:
    if not any(isinstance(hook, LoggingHook) for hook in _GLOBAL_HOOKS):
        register_global_hook(LoggingHook())


def _resolve_effective_config(llm_config: ResolvedLLMConfig | None) -> ResolvedLLMConfig:
    return llm_config or resolve_llm_config("build")


def _build_openai_client(llm_config: ResolvedLLMConfig) -> OpenAI:
    return OpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=llm_config.timeout_seconds,
    )


def _create_provider_completion(client: OpenAI, request_payload: dict[str, Any], adapter: ProviderAdapter) -> Any:
    if adapter.uses_responses_api:
        return client.responses.create(**request_payload)
    return client.chat.completions.create(**request_payload)


def _create_provider_completion_stream(
    client: OpenAI,
    request_payload: dict[str, Any],
    adapter: ProviderAdapter,
) -> Any:
    if adapter.uses_responses_api:
        return client.responses.create(**request_payload)
    return client.chat.completions.create(**request_payload)


def create_chat_completion(
    messages: list[Message],
    tools: list[dict[str, Any]],
    max_tokens: int = 4096,
    hooks: list[LLMHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    agent: str = "",
) -> Message:
    """统一封装大模型调用入口，返回内部 Message 结构。"""
    if not messages:
        raise ValueError("messages 不能为空，无法解析 session_id")
    session_id = str(messages[-1]["info"].get("session_id", "")).strip()
    if not session_id:
        raise ValueError("messages[-1] 缺少 session_id")
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    ctx: HookContext = {
        "session_id": session_id,
        "agent": agent,
        "provider": adapter.provider,
        "model": adapter.model,
        "api_mode": effective_config.api_mode,
        "parent_id": parent_id,
        "max_tokens": max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "source_messages": messages,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    start = time.perf_counter()
    ctx["start_time"] = start

    try:
        request_payload = adapter.build_request(messages, tools, client=client)
        request_payload[adapter.request_token_key] = max_tokens
        ctx["request_payload"] = request_payload
    except Exception as exc:
        ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
        normalized = normalize_error(exc)
        for hook in effective_hooks:
            _invoke_hook(hook, "error", ctx=ctx, error=exc, normalized_error=normalized)

        return create_error_message(
            session_id=session_id,
            model=adapter.model,
            provider=adapter.provider,
            error=normalized,
            parent_id=parent_id,
        )

    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    try:
        response = _create_provider_completion(client, request_payload, adapter)
        message = adapter.parse_response(response, session_id=session_id, parent_id=parent_id)
    except Exception as exc:
        ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
        normalized = normalize_error(exc)
        for hook in effective_hooks:
            _invoke_hook(hook, "error", ctx=ctx, error=exc, normalized_error=normalized)

        return create_error_message(
            session_id=session_id,
            model=adapter.model,
            provider=adapter.provider,
            error=normalized,
            parent_id=parent_id,
        )

    ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
    for hook in effective_hooks:
        _invoke_hook(hook, "after", ctx=ctx, message=message)

    return message


def create_chat_completion_stream(
    messages: list[Message],
    tools: list[dict[str, Any]],
    max_tokens: int = 4096,
    hooks: list[LLMHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    agent: str = "",
) -> Generator[dict[str, Any], None, Message]:
    """流式调用大模型，逐步产出文本增量并在结束时返回完整 Message。"""
    if not messages:
        raise ValueError("messages 不能为空，无法解析 session_id")
    session_id = str(messages[-1]["info"].get("session_id", "")).strip()
    if not session_id:
        raise ValueError("messages[-1] 缺少 session_id")
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    ctx: HookContext = {
        "session_id": session_id,
        "agent": agent,
        "provider": adapter.provider,
        "model": adapter.model,
        "api_mode": effective_config.api_mode,
        "parent_id": parent_id,
        "max_tokens": max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "source_messages": messages,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    start = time.perf_counter()
    ctx["start_time"] = start

    stream_state = adapter.new_stream_state()

    try:
        request_payload = adapter.build_request(messages, tools, client=client)
        request_payload[adapter.request_token_key] = max_tokens
        request_payload["stream"] = True
        ctx["request_payload"] = request_payload
    except Exception as exc:
        ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
        normalized = normalize_error(exc)
        for hook in effective_hooks:
            _invoke_hook(hook, "error", ctx=ctx, error=exc, normalized_error=normalized)
        return create_error_message(
            session_id=session_id,
            model=adapter.model,
            provider=adapter.provider,
            error=normalized,
            parent_id=parent_id,
        )

    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    try:
        stream = _create_provider_completion_stream(client, request_payload, adapter)
        for chunk in stream:
            try:
                events = adapter.consume_stream_chunk(chunk, stream_state)
            except RuntimeError:
                if adapter.uses_responses_api:
                    log_fields = adapter.get_stream_failure_log_fields(chunk)  # type: ignore[attr-defined]
                    logger.warning(
                        "llm.responses_stream_failure event_type=%s status=%s error_code=%s error_type=%s incomplete_reason=%s detail=%s event_keys=%s response_keys=%s",
                        sanitize_log_text(log_fields["event_type"], limit=80),
                        sanitize_log_text(log_fields["status"], limit=80),
                        sanitize_log_text(log_fields["error_code"], limit=80),
                        sanitize_log_text(log_fields["error_type"], limit=80),
                        sanitize_log_text(log_fields["incomplete_reason"], limit=120),
                        sanitize_log_text(log_fields["detail"], limit=200),
                        sanitize_log_text(log_fields["event_keys"], limit=200),
                        sanitize_log_text(log_fields["response_keys"], limit=200),
                        extra=build_log_extra(agent="", model=effective_config.model),
                    )
                raise
            for event in events:
                yield event
    except Exception as exc:
        ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
        normalized = normalize_error(exc)
        for hook in effective_hooks:
            _invoke_hook(hook, "error", ctx=ctx, error=exc, normalized_error=normalized)
        return create_error_message(
            session_id=session_id,
            model=adapter.model,
            provider=adapter.provider,
            error=normalized,
            parent_id=parent_id,
        )

    assistant = adapter.build_stream_message(stream_state, session_id=session_id, parent_id=parent_id)
    ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
    for hook in effective_hooks:
        _invoke_hook(hook, "after", ctx=ctx, message=assistant)
    return assistant


_default_hooks()
