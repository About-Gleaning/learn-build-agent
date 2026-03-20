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
    extract_tool_calls,
    get_role,
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
        latest_message = _build_latest_message_preview(ctx.get("source_messages", []))
        log_extra = build_log_extra(agent=ctx.get("agent", ""), model=ctx.get("model", ""))
        if latest_message is None:
            logger.info("llm.request api_mode=%s", ctx.get("api_mode", "unknown"), extra=log_extra)
            return
        logger.info(
            "llm.request api_mode=%s latest_message=%s",
            ctx.get("api_mode", "unknown"),
            latest_message,
            extra=log_extra,
        )

    def after_call(self, ctx: HookContext, message: Message) -> None:
        tool_names = [tool_call["name"] for tool_call in extract_tool_calls(message) if tool_call.get("name")]
        if tool_names:
            info_text = f"tool_names={','.join(tool_names)}"
        else:
            info_text = f"message={_build_response_preview(message)}"

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


def _build_latest_message_preview(messages: list[Message]) -> str | None:
    if not messages:
        return None

    latest_message = messages[-1]
    if get_role(latest_message) != "user":
        return None

    for part in reversed(latest_message.get("parts", [])):
        if part.get("type") != "text":
            continue
        content = str(part.get("content", "")).strip()
        if content:
            return sanitize_log_text(content)
    return ""


def _build_response_preview(message: Message) -> str:
    text_parts = [
        str(part.get("content", "")).strip()
        for part in message.get("parts", [])
        if part.get("type") in {"text", "error"} and str(part.get("content", "")).strip()
    ]
    return sanitize_log_text("\n".join(text_parts))


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
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    request_payload = adapter.build_request(messages, tools)
    request_payload[adapter.request_token_key] = max_tokens

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
        "request_payload": request_payload,
        "source_messages": messages,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    start = time.perf_counter()
    ctx["start_time"] = start

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
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = build_provider_adapter(effective_config)
    client = _build_openai_client(effective_config)

    request_payload = adapter.build_request(messages, tools)
    request_payload[adapter.request_token_key] = max_tokens
    request_payload["stream"] = True

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
        "request_payload": request_payload,
        "source_messages": messages,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    start = time.perf_counter()
    ctx["start_time"] = start

    stream_state = adapter.new_stream_state()

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
