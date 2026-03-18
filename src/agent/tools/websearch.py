from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from dotenv import load_dotenv

__all__ = ["WebSearchTool", "websearch", "WebSearchParams", "ToolContext", "ToolResult"]

DEFAULT_NUM_RESULTS = 8
DEFAULT_CONTEXT_MAX_CHARACTERS = 10000

load_dotenv()


class AskPayload(TypedDict):
    permission: str
    patterns: list[str]
    always: list[str]
    metadata: dict[str, Any]


class ToolResult(TypedDict):
    output: str
    title: str
    metadata: dict[str, Any]


class ToolContext(Protocol):
    def ask(self, payload: AskPayload) -> None: ...


class WebSearchParams(TypedDict, total=False):
    query: str
    numResults: int
    livecrawl: Literal["fallback", "preferred"]
    type: Literal["auto", "fast", "deep"]
    contextMaxCharacters: int
    api_key: str


@dataclass(frozen=True)
class WebSearchTool:
    """
    基于 Exa Python SDK 的 websearch 工具。

    这个实现保持与仓库中工具相近的输入输出语义，但底层改为使用官方 Python SDK，
    方便直接复制到其他 Python agent 项目中使用。
    """

    api_key: str | None = None

    def execute(self, params: WebSearchParams, ctx: ToolContext | None = None) -> ToolResult:
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")

        num_results = _normalize_int(params.get("numResults"), "numResults", DEFAULT_NUM_RESULTS)
        context_max_characters = _normalize_int(
            params.get("contextMaxCharacters"),
            "contextMaxCharacters",
            DEFAULT_CONTEXT_MAX_CHARACTERS,
        )
        livecrawl = _normalize_optional_enum(params.get("livecrawl"), "livecrawl", {"fallback", "preferred"})
        search_type = _normalize_optional_enum(params.get("type"), "type", {"auto", "fast", "deep"}) or "auto"
        api_key = params.get("api_key") or self.api_key or os.getenv("EXA_API_KEY")

        if not api_key:
            raise ValueError("EXA_API_KEY is required")

        if ctx and hasattr(ctx, "ask"):
            ctx.ask(
                {
                    "permission": "websearch",
                    "patterns": [query],
                    "always": ["*"],
                    "metadata": {
                        "query": query,
                        "numResults": params.get("numResults"),
                        "livecrawl": livecrawl,
                        "type": search_type,
                        "contextMaxCharacters": params.get("contextMaxCharacters"),
                    },
                },
            )

        client = _create_client(api_key)
        call: dict[str, Any] = {
            "query": query,
            "type": search_type,
            "num_results": num_results,
            # 新版 Exa SDK 通过 search_and_contents + text 参数返回正文内容。
            "text": {"max_characters": context_max_characters},
        }

        # livecrawl 在 Exa 的搜索与内容检索能力中是有效概念；仅在调用方传入时透传。
        if livecrawl:
            call["livecrawl"] = livecrawl

        response = client.search_and_contents(**call)
        items = _extract_items(response)

        if not items:
            return {
                "output": "No search results found. Please try a different query.",
                "title": f"Web search: {query}",
                "metadata": {},
            }

        output = "\n\n".join(items)
        return {
            "output": output,
            "title": f"Web search: {query}",
            "metadata": {},
        }


def websearch(params: WebSearchParams, ctx: ToolContext | None = None) -> ToolResult:
    return WebSearchTool().execute(params, ctx)


def _create_client(api_key: str):
    try:
        from exa_py import Exa
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("Missing dependency 'exa-py'. Install it with: pip install exa-py") from exc

    return Exa(api_key=api_key)


def _normalize_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _normalize_optional_enum(value: Any, name: str, allowed: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        members = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {members}")
    return value


def _extract_items(response: Any) -> list[str]:
    results = _get_value(response, "results")
    if not isinstance(results, list):
        return []

    items: list[str] = []
    for index, result in enumerate(results, start=1):
        text = _extract_text(result)
        if not text:
            continue

        title = _string_value(result, "title") or "Untitled"
        url = _string_value(result, "url") or _string_value(result, "id") or ""
        score = _get_value(result, "score")

        # 拼成稳定的纯文本块，便于 agent 直接阅读或继续总结。
        lines = [f"{index}. {title}"]
        if url:
            lines.append(f"URL: {url}")
        if isinstance(score, (int, float)):
            lines.append(f"Score: {score}")
        lines.append("")
        lines.append(text.strip())
        items.append("\n".join(lines).strip())

    return items


def _extract_text(result: Any) -> str:
    text = _string_value(result, "text")
    if text:
        return text

    summary = _string_value(result, "summary")
    if summary:
        return summary

    highlights = _get_value(result, "highlights")
    if isinstance(highlights, list):
        parts = [item.strip() for item in highlights if isinstance(item, str) and item.strip()]
        if parts:
            return "\n".join(parts)

    return ""


def _string_value(data: Any, key: str) -> str:
    value = _get_value(data, key)
    return value if isinstance(value, str) else ""


def _get_value(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return getattr(data, key, None)
