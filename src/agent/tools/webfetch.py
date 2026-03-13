from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal, Protocol, TypedDict
from urllib import error, request

MAX_RESPONSE_SIZE = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
__all__ = ["WebFetchTool", "webfetch", "WebFetchParams", "ToolContext", "ToolResult"]


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


class WebFetchParams(TypedDict, total=False):
    url: str
    format: Literal["text", "markdown", "html"]
    timeout: float


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    charset: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def close(self) -> None:
        return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "iframe", "object", "embed"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "iframe", "object", "embed"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = _normalize_whitespace(data)
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


class _MarkdownExtractor(HTMLParser):
    _BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "aside"}
    _SKIP_TAGS = {"script", "style", "meta", "link", "noscript"}
    _VOID_SKIP_TAGS = {"meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._list_stack: list[str] = []
        self._pending_link: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in self._SKIP_TAGS:
            if tag in self._VOID_SKIP_TAGS:
                return
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if tag in {"br", "hr"}:
            self._append("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._append(f"\n{'#' * level} ")
        elif tag in self._BLOCK_TAGS:
            self._append("\n\n")
        elif tag == "li":
            indent = "  " * max(len(self._list_stack) - 1, 0)
            bullet = "- " if (not self._list_stack or self._list_stack[-1] == "ul") else "1. "
            self._append(f"\n{indent}{bullet}")
        elif tag in {"ul", "ol"}:
            self._list_stack.append(tag)
            self._append("\n")
        elif tag == "a":
            self._pending_link = attrs_dict.get("href")
            self._link_text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return

        if tag in {"ul", "ol"} and self._list_stack:
            self._list_stack.pop()
            self._append("\n")
        elif tag == "a":
            text = _normalize_whitespace("".join(self._link_text))
            if text:
                if self._pending_link:
                    self._append(f"[{text}]({self._pending_link})")
                else:
                    self._append(text)
            self._pending_link = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = _normalize_whitespace(data)
        if not text:
            return
        if self._pending_link is not None:
            self._link_text.append(text)
            return
        self._append(text)

    def get_markdown(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _append(self, text: str) -> None:
        self._parts.append(text)


@dataclass(frozen=True)
class WebFetchTool:
    """
    与 `packages/opencode/src/tool/webfetch.ts` 对齐的 Python 版本。

    这个实现设计成独立模块，方便直接复制到其他 agent 项目中使用。
    """

    def execute(self, params: WebFetchParams, ctx: ToolContext | None = None) -> ToolResult:
        url = params.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("url is required")

        if not url.startswith("http://") and not url.startswith("https://"):
            raise ValueError("URL must start with http:// or https://")

        target = url
        fmt = _normalize_format(params.get("format"))
        timeout = min(float(params.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT)

        if ctx and hasattr(ctx, "ask"):
            ctx.ask(
                {
                    "permission": "webfetch",
                    "patterns": [target],
                    "always": ["*"],
                    "metadata": {
                        "url": target,
                        "format": fmt,
                        "timeout": params.get("timeout"),
                    },
                },
            )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
            "Accept": _build_accept_header(fmt),
            "Accept-Language": "en-US,en;q=0.9",
        }

        initial = _request(target, headers, timeout)

        retry = initial.status_code == 403 and initial.headers.get("cf-mitigated") == "challenge"
        if retry:
            initial.close()
        response = _request(target, {**headers, "User-Agent": "opencode"}, timeout) if retry else initial

        if not response.ok:
            raise RuntimeError(f"Request failed with status code: {response.status_code}")

        _ensure_size_from_header(response.headers.get("content-length"))
        content = _decode_body(response)
        content_type = response.headers.get("content-type", "")
        title = f"{target} ({content_type})"

        if fmt == "markdown":
            output = _convert_html_to_markdown(content) if "text/html" in content_type else content
            return {"output": output, "title": title, "metadata": {}}

        if fmt == "text":
            output = _extract_text_from_html(content) if "text/html" in content_type else content
            return {"output": output, "title": title, "metadata": {}}

        return {"output": content, "title": title, "metadata": {}}


def webfetch(params: WebFetchParams, ctx: ToolContext | None = None) -> ToolResult:
    return WebFetchTool().execute(params, ctx)


def _normalize_format(value: Any) -> Literal["text", "markdown", "html"]:
    fmt = value or "markdown"
    if fmt in {"text", "markdown", "html"}:
        return fmt
    raise ValueError("format must be one of: text, markdown, html")


def _build_accept_header(fmt: Literal["text", "markdown", "html"]) -> str:
    if fmt == "markdown":
        return "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
    if fmt == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    if fmt == "html":
        return "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
    return "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"


def _request(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
    req = request.Request(url=url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return _build_http_response(resp.status, resp.headers, resp)
    except error.HTTPError as exc:
        return _build_http_response(exc.code, exc.headers, exc)
    except error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc


def _build_http_response(status_code: int, headers: Any, stream: Any) -> HttpResponse:
    header_map = {str(key).lower(): str(value) for key, value in headers.items()}
    _ensure_size_from_header(header_map.get("content-length"))
    body = _read_body(stream)
    charset = None
    if hasattr(headers, "get_content_charset"):
        charset = headers.get_content_charset()
    if not charset:
        charset = _extract_charset(header_map.get("content-type", ""))
    return HttpResponse(
        status_code=status_code,
        headers=header_map,
        body=body,
        charset=charset,
    )


def _ensure_size_from_header(content_length: str | None) -> None:
    if content_length is None:
        return

    if not content_length.isdigit():
        return

    if int(content_length) > MAX_RESPONSE_SIZE:
        raise RuntimeError("Response too large (exceeds 5MB limit)")


def _read_body(stream: Any) -> bytes:
    total = 0
    chunks: list[bytes] = []

    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_SIZE:
            raise RuntimeError("Response too large (exceeds 5MB limit)")
        chunks.append(chunk)

    return b"".join(chunks)


def _decode_body(response: HttpResponse) -> str:
    encoding = response.charset or "utf-8"
    return response.body.decode(encoding, errors="replace")


def _extract_charset(content_type: str) -> str | None:
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _extract_text_from_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def _convert_html_to_markdown(html: str) -> str:
    parser = _MarkdownExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_markdown()
