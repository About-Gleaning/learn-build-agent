import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from .message import (
    Message,
    count_parts,
    create_error_message,
    estimate_message_size,
    normalize_error,
    parse_provider_response,
    to_provider_messages,
)

# 环境加载
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("MODEL", "qwen3-max")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

if not API_KEY:
    raise ValueError("缺少 API_KEY，请在 .env 文件中配置 API_KEY。")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


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


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)
adapter = OpenAICompatibleAdapter(MODEL)


def _mask_text(text: str, limit: int = 300) -> str:
    cleaned = text.replace("\n", "\\n")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...<truncated>"


def create_chat_completion(
    messages: list[Message],
    tools: list[dict[str, Any]],
    max_tokens: int = 4096,
) -> Message:
    """统一封装大模型调用入口，返回内部 Message 结构。"""
    session_id = messages[-1]["info"].get("session_id", "default_session") if messages else "default_session"
    parent_id = messages[-1]["info"].get("message_id", "") if messages else ""

    request_payload = adapter.build_request(messages, tools)
    request_payload["max_tokens"] = max_tokens

    request_size = sum(estimate_message_size(msg) for msg in messages)
    logger.info(
        "llm.request session_id=%s model=%s message_count=%d tools_count=%d request_size=%d",
        session_id,
        adapter.model,
        len(messages),
        len(tools),
        request_size,
    )

    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**request_payload)
        message = adapter.parse_response(response, session_id=session_id, parent_id=parent_id)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        normalized = normalize_error(exc)
        logger.exception(
            "llm.error session_id=%s model=%s latency_ms=%d error_code=%s error_type=%s",
            session_id,
            adapter.model,
            elapsed_ms,
            normalized.get("code", "api_error"),
            normalized.get("details", "Exception"),
        )
        return create_error_message(
            session_id=session_id,
            model=adapter.model,
            error=normalized,
            parent_id=parent_id,
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    content_preview = _mask_text(
        "\n".join(part.get("content", "") for part in message["parts"] if part.get("type") in {"text", "error"})
    )
    logger.info(
        "llm.response session_id=%s model=%s latency_ms=%d status=%s finish_reason=%s tool_calls=%d preview=%s",
        session_id,
        adapter.model,
        elapsed_ms,
        message["info"].get("status", "unknown"),
        message["info"].get("finish_reason", ""),
        count_parts(message, "tool_call"),
        content_preview,
    )

    usage = message["info"].get("token_usage", {})
    if usage:
        logger.debug(
            "llm.usage session_id=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            session_id,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0),
        )

    return message
