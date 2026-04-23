import logging
import time
from collections.abc import Generator
from typing import Any

from openai import OpenAI

from ...config.logging_setup import build_log_extra, sanitize_log_text
from ...config.settings import ResolvedLLMConfig, resolve_llm_config
from ...core.hooks import HookDispatcher, ordered_hooks
from ...core.message import (
    Message,
    create_error_message,
    estimate_message_size,
    normalize_error,
)
from .hooks import HookContext, LLMHook, LoggingHook
from .protocols import ProviderAdapter, normalize_responses_tools
from .vendors import build_provider_adapter
logger = logging.getLogger(__name__)

# 兼容现有测试与内部调用路径，继续从 client 暴露该辅助函数。
_normalize_responses_tools = normalize_responses_tools


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
    if not hook.should_run(ctx):
        return
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


def _resolve_effective_hooks(hooks: list[LLMHook] | None = None) -> list[LLMHook]:
    return ordered_hooks(get_global_hooks() + (hooks or []))


def _resolve_effective_config(llm_config: ResolvedLLMConfig | None) -> ResolvedLLMConfig:
    return llm_config or resolve_llm_config("build")


def _resolve_request_max_tokens(max_tokens: int | None, llm_config: ResolvedLLMConfig) -> int:
    if max_tokens is None:
        return llm_config.max_tokens
    if isinstance(max_tokens, bool) or max_tokens <= 0:
        raise ValueError("max_tokens 必须是大于 0 的整数。")
    return max_tokens


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
    max_tokens: int | None = None,
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
    effective_max_tokens = _resolve_request_max_tokens(max_tokens, effective_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    ctx: HookContext = {
        "session_id": session_id,
        "agent": agent,
        "provider": adapter.provider,
        "model": adapter.model,
        "api_mode": effective_config.api_mode,
        "parent_id": parent_id,
        "max_tokens": effective_max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "source_messages": messages,
    }

    effective_hooks = _resolve_effective_hooks(hooks)
    start = time.perf_counter()
    ctx["start_time"] = start

    try:
        request_payload = adapter.build_request(messages, tools, client=client)
        request_payload[adapter.request_token_key] = effective_max_tokens
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
    max_tokens: int | None = None,
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
    effective_max_tokens = _resolve_request_max_tokens(max_tokens, effective_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    ctx: HookContext = {
        "session_id": session_id,
        "agent": agent,
        "provider": adapter.provider,
        "model": adapter.model,
        "api_mode": effective_config.api_mode,
        "parent_id": parent_id,
        "max_tokens": effective_max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "source_messages": messages,
    }

    effective_hooks = _resolve_effective_hooks(hooks)
    start = time.perf_counter()
    ctx["start_time"] = start

    stream_state = adapter.new_stream_state()

    try:
        request_payload = adapter.build_request(messages, tools, client=client)
        request_payload[adapter.request_token_key] = effective_max_tokens
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


create_chat_completion_stream.supports_artifact_ingest = True  # type: ignore[attr-defined]


_default_hooks()
