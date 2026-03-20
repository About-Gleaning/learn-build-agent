from __future__ import annotations

from typing import Any

from ...config.settings import ResolvedLLMConfig
from .protocols import (
    ChatCompletionsAdapter,
    ProviderAdapter,
    ResponsesAdapter,
    normalize_qwen_responses_tools,
)


class OpenAIChatCompletionsAdapter(ChatCompletionsAdapter):
    """OpenAI 风格 chat.completions 默认方言。"""


class KimiChatCompletionsAdapter(ChatCompletionsAdapter):
    """Kimi 独立方言占位，后续特殊字段在这里扩展。"""


class OpenAIResponsesAdapter(ResponsesAdapter):
    """OpenAI 风格 responses 默认方言。"""


class QwenResponsesAdapter(ResponsesAdapter):
    """Qwen 独立方言占位，后续兼容差异在这里扩展。"""

    def normalize_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Qwen Responses 兼容层对自定义 function 的 JSON Schema 更严格，这里下发保守子集。
        return normalize_qwen_responses_tools(tools)


def build_provider_adapter(config: ResolvedLLMConfig) -> ProviderAdapter:
    if config.api_mode == "responses":
        if config.vendor == "qwen":
            return QwenResponsesAdapter(config)
        return OpenAIResponsesAdapter(config)

    if config.vendor == "kimi":
        return KimiChatCompletionsAdapter(config)
    return OpenAIChatCompletionsAdapter(config)
