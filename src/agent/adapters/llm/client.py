import logging
import json
import time
from collections.abc import Generator
from typing import Any, TypedDict

from openai import OpenAI

from ...config.settings import LOG_LEVEL, LOG_LLM_PROMPT, LOG_LLM_PROMPT_LIMIT, ResolvedLLMConfig, resolve_llm_config
from ...core.hooks import HookDispatcher
from ...core.message import (
    Message,
    append_text_part,
    append_tool_part,
    count_parts,
    create_error_message,
    create_message,
    estimate_message_size,
    mark_message_completed,
    normalize_error,
    parse_provider_response,
    to_provider_messages,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class HookContext(TypedDict, total=False):
    session_id: str
    provider: str
    model: str
    parent_id: str
    max_tokens: int
    message_count: int
    tools_count: int
    request_size: int
    request_payload: dict[str, Any]
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
        logger.info(
            "llm.request session_id=%s provider=%s model=%s message_count=%d tools_count=%d request_size=%d",
            ctx.get("session_id", "default_session"),
            ctx.get("provider", ""),
            ctx.get("model", ""),
            ctx.get("message_count", 0),
            ctx.get("tools_count", 0),
            ctx.get("request_size", 0),
        )
        if LOG_LLM_PROMPT:
            logger.info(
                "llm.prompt session_id=%s provider=%s model=%s prompt=%s",
                ctx.get("session_id", "default_session"),
                ctx.get("provider", ""),
                ctx.get("model", ""),
                _build_prompt_preview(ctx.get("request_payload", {})),
            )

    def after_call(self, ctx: HookContext, message: Message) -> None:
        content_preview = _mask_text(
            "\n".join(part.get("content", "") for part in message["parts"] if part.get("type") in {"text", "error"})
        )
        logger.info(
            "llm.response session_id=%s provider=%s model=%s latency_ms=%d status=%s finish_reason=%s tool_calls=%d preview=%s",
            ctx.get("session_id", "default_session"),
            ctx.get("provider", ""),
            ctx.get("model", ""),
            ctx.get("latency_ms", 0),
            message["info"].get("status", "unknown"),
            message["info"].get("finish_reason", ""),
            count_parts(message, "tool"),
            content_preview,
        )

        usage = message["info"].get("token_usage", {})
        if usage:
            logger.debug(
                "llm.usage session_id=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                ctx.get("session_id", "default_session"),
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
            )

    def on_error(self, ctx: HookContext, error: Exception, normalized_error: dict[str, str]) -> None:
        logger.exception(
            "llm.error session_id=%s provider=%s model=%s latency_ms=%d error_code=%s error_type=%s",
            ctx.get("session_id", "default_session"),
            ctx.get("provider", ""),
            ctx.get("model", ""),
            ctx.get("latency_ms", 0),
            normalized_error.get("code", "api_error"),
            normalized_error.get("details", type(error).__name__),
        )


class OpenAICompatibleAdapter:
    """OpenAI 兼容接口适配层，负责内部 Message 与 provider 协议互转。"""

    def __init__(self, config: ResolvedLLMConfig) -> None:
        self.config = config
        self.model = config.model
        self.provider = config.provider

    def build_request(self, messages: list[Message], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": to_provider_messages(messages),
            "tools": tools,
        }

    def parse_response(self, response: Any, *, session_id: str, parent_id: str = "") -> Message:
        return parse_provider_response(
            response,
            session_id=session_id,
            model=self.model,
            provider=self.provider,
            parent_id=parent_id,
        )


_GLOBAL_HOOKS: list[LLMHook] = []
_DISPATCHER = HookDispatcher[LLMHook, HookContext, dict[str, str]](logger=logger, name="llm")


def _mask_text(text: str, limit: int = 300) -> str:
    cleaned = text.replace("\n", "\\n")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...<truncated>"


def _build_prompt_preview(request_payload: dict[str, Any]) -> str:
    messages = request_payload.get("messages", [])
    try:
        serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(messages)
    return _mask_text(serialized, limit=LOG_LLM_PROMPT_LIMIT)


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
    return OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)


def create_chat_completion(
    messages: list[Message],
    tools: list[dict[str, Any]],
    max_tokens: int = 4096,
    hooks: list[LLMHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
) -> Message:
    """统一封装大模型调用入口，返回内部 Message 结构。"""
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = OpenAICompatibleAdapter(effective_config)
    client = _build_openai_client(effective_config)

    request_payload = adapter.build_request(messages, tools)
    request_payload["max_tokens"] = max_tokens

    ctx: HookContext = {
        "session_id": session_id,
        "provider": adapter.provider,
        "model": adapter.model,
        "parent_id": parent_id,
        "max_tokens": max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "request_payload": request_payload,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    start = time.perf_counter()
    ctx["start_time"] = start

    try:
        response = client.chat.completions.create(**request_payload)
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
) -> Generator[dict[str, Any], None, Message]:
    """流式调用大模型，逐步产出文本增量并在结束时返回完整 Message。"""
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""
    effective_config = _resolve_effective_config(llm_config)
    adapter = OpenAICompatibleAdapter(effective_config)
    client = _build_openai_client(effective_config)

    request_payload = adapter.build_request(messages, tools)
    request_payload["max_tokens"] = max_tokens
    request_payload["stream"] = True

    ctx: HookContext = {
        "session_id": session_id,
        "provider": adapter.provider,
        "model": adapter.model,
        "parent_id": parent_id,
        "max_tokens": max_tokens,
        "message_count": len(messages),
        "tools_count": len(tools),
        "request_size": sum(estimate_message_size(msg) for msg in messages),
        "request_payload": request_payload,
    }

    effective_hooks = get_global_hooks() + (hooks or [])
    for hook in effective_hooks:
        _invoke_hook(hook, "before", ctx=ctx)

    start = time.perf_counter()
    ctx["start_time"] = start

    finish_reason = "stop"
    text_buffer: list[str] = []
    tool_call_map: dict[int, dict[str, str]] = {}
    usage_payload: dict[str, int] | None = None

    try:
        stream = client.chat.completions.create(**request_payload)
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
                usage_payload = {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            chunk_finish_reason = str(getattr(choice, "finish_reason", "") or "").strip()
            if chunk_finish_reason:
                finish_reason = chunk_finish_reason

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            delta_content = getattr(delta, "content", None)
            if delta_content:
                delta_text = str(delta_content)
                text_buffer.append(delta_text)
                yield {"type": "text_delta", "delta": delta_text}

            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_call, "index", 0) or 0)
                state = tool_call_map.setdefault(index, {"id": "", "name": "", "arguments": ""})
                tc_id = str(getattr(tool_call, "id", "") or "")
                if tc_id:
                    state["id"] = tc_id
                function_obj = getattr(tool_call, "function", None)
                if function_obj is None:
                    continue
                tc_name = str(getattr(function_obj, "name", "") or "")
                if tc_name:
                    state["name"] = tc_name
                tc_arguments = str(getattr(function_obj, "arguments", "") or "")
                if tc_arguments:
                    state["arguments"] += tc_arguments
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

    assistant = create_message(
        "assistant",
        session_id,
        model=adapter.model,
        provider=adapter.provider,
        status="running",
        finish_reason=finish_reason,
        parent_id=parent_id,
    )

    if text_buffer:
        append_text_part(assistant, "".join(text_buffer))

    for index in sorted(tool_call_map.keys()):
        tool_call = tool_call_map[index]
        if not tool_call["id"] or not tool_call["name"]:
            continue
        append_tool_part(
            assistant,
            tool_call_id=tool_call["id"],
            name=tool_call["name"],
            status="requested",
            arguments=tool_call["arguments"] or "{}",
        )

    if usage_payload is not None:
        assistant["info"]["token_usage"] = usage_payload

    mark_message_completed(assistant, finish_reason=assistant["info"].get("finish_reason", "stop") or "stop")
    ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
    for hook in effective_hooks:
        _invoke_hook(hook, "after", ctx=ctx, message=assistant)
    return assistant


_default_hooks()
