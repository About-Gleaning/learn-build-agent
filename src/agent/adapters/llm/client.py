import logging
import time
from typing import Any, TypedDict

from openai import OpenAI

from ...config.settings import API_KEY, BASE_URL, LOG_LEVEL, MODEL
from ...core.hooks import HookDispatcher
from ...core.message import (
    Message,
    count_parts,
    create_error_message,
    estimate_message_size,
    normalize_error,
    parse_provider_response,
    to_provider_messages,
)

if not API_KEY:
    raise ValueError("缺少 API_KEY，请在 .env 文件中配置 API_KEY。")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class HookContext(TypedDict, total=False):
    session_id: str
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
            "llm.request session_id=%s model=%s message_count=%d tools_count=%d request_size=%d",
            ctx.get("session_id", "default_session"),
            ctx.get("model", ""),
            ctx.get("message_count", 0),
            ctx.get("tools_count", 0),
            ctx.get("request_size", 0),
        )

    def after_call(self, ctx: HookContext, message: Message) -> None:
        content_preview = _mask_text(
            "\n".join(part.get("content", "") for part in message["parts"] if part.get("type") in {"text", "error"})
        )
        logger.info(
            "llm.response session_id=%s model=%s latency_ms=%d status=%s finish_reason=%s tool_calls=%d preview=%s",
            ctx.get("session_id", "default_session"),
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
            "llm.error session_id=%s model=%s latency_ms=%d error_code=%s error_type=%s",
            ctx.get("session_id", "default_session"),
            ctx.get("model", ""),
            ctx.get("latency_ms", 0),
            normalized_error.get("code", "api_error"),
            normalized_error.get("details", type(error).__name__),
        )


class OpenAICompatibleAdapter:
    """OpenAI 兼容接口适配层，负责内部 Message 与 provider 协议互转。"""

    def __init__(self, model: str) -> None:
        self.model = model

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
            parent_id=parent_id,
        )


client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
adapter = OpenAICompatibleAdapter(MODEL)
_GLOBAL_HOOKS: list[LLMHook] = []
_DISPATCHER = HookDispatcher[LLMHook, HookContext, dict[str, str]](logger=logger, name="llm")


def _mask_text(text: str, limit: int = 300) -> str:
    cleaned = text.replace("\n", "\\n")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...<truncated>"


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


def create_chat_completion(
    messages: list[Message],
    tools: list[dict[str, Any]],
    max_tokens: int = 4096,
    hooks: list[LLMHook] | None = None,
) -> Message:
    """统一封装大模型调用入口，返回内部 Message 结构。"""
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""

    request_payload = adapter.build_request(messages, tools)
    request_payload["max_tokens"] = max_tokens

    ctx: HookContext = {
        "session_id": session_id,
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
            error=normalized,
            parent_id=parent_id,
        )

    ctx["latency_ms"] = int((time.perf_counter() - start) * 1000)
    for hook in effective_hooks:
        _invoke_hook(hook, "after", ctx=ctx, message=message)

    return message


_default_hooks()
