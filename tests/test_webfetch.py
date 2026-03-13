from __future__ import annotations

import pytest

from agent.tools.webfetch import MAX_RESPONSE_SIZE, WebFetchTool


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body_chunks: list[bytes] | None = None,
        encoding: str | None = "utf-8",
        apparent_encoding: str | None = "utf-8",
    ) -> None:
        self.status_code = status_code
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        self._body_chunks = body_chunks or []
        self.encoding = encoding
        self.apparent_encoding = apparent_encoding
        self.charset = encoding or apparent_encoding
        self.body = b"".join(self._body_chunks)
        self.closed = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        for chunk in self._body_chunks:
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeHeaders(dict):
    def get_content_charset(self):
        content_type = str(self.get("content-type", ""))
        marker = "charset="
        if marker not in content_type:
            return None
        return content_type.split(marker, 1)[1].split(";", 1)[0].strip()


def test_webfetch_should_keep_http_scheme(monkeypatch):
    captured: dict[str, object] = {}

    def fake_request(url, headers, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse(
            headers={"content-type": "text/plain"},
            body_chunks=[b"hello"],
        )

    monkeypatch.setattr("agent.tools.webfetch._request", fake_request)

    result = WebFetchTool().execute({"url": "http://example.com", "format": "text"})

    assert captured["url"] == "http://example.com"
    assert result["output"] == "hello"
    assert result["title"] == "http://example.com (text/plain)"


def test_webfetch_should_support_https(monkeypatch):
    def fake_request(url, headers, timeout):
        del headers, timeout
        return FakeResponse(
            headers={"content-type": "text/plain"},
            body_chunks=[url.encode("utf-8")],
        )

    monkeypatch.setattr("agent.tools.webfetch._request", fake_request)

    result = WebFetchTool().execute({"url": "https://example.com"})

    assert result["output"] == "https://example.com"


def test_webfetch_should_reject_url_without_http_scheme():
    with pytest.raises(ValueError, match="URL must start with http:// or https://"):
        WebFetchTool().execute({"url": "example.com"})


def test_webfetch_should_reject_invalid_format():
    with pytest.raises(ValueError, match="format must be one of"):
        WebFetchTool().execute({"url": "https://example.com", "format": "json"})  # type: ignore[arg-type]


def test_webfetch_should_reject_large_content_length(monkeypatch):
    def fake_request(url, headers, timeout):
        del url, headers, timeout
        return FakeResponse(
            headers={
                "content-type": "text/plain",
                "content-length": str(MAX_RESPONSE_SIZE + 1),
            },
            body_chunks=[b"x"],
        )

    monkeypatch.setattr("agent.tools.webfetch._request", fake_request)

    with pytest.raises(RuntimeError, match="Response too large"):
        WebFetchTool().execute({"url": "https://example.com"})


def test_webfetch_should_convert_html_to_text(monkeypatch):
    html = b"<html><body><script>x</script><h1>Title</h1><p>Hello</p></body></html>"

    def fake_request(url, headers, timeout):
        del url, headers, timeout
        return FakeResponse(
            headers={"content-type": "text/html; charset=utf-8"},
            body_chunks=[html],
        )

    monkeypatch.setattr("agent.tools.webfetch._request", fake_request)

    result = WebFetchTool().execute({"url": "https://example.com", "format": "text"})

    assert result["output"] == "TitleHello"


def test_webfetch_should_convert_html_to_markdown(monkeypatch):
    html = b"<html><head><meta charset='utf-8'></head><body><h1>Title</h1><p>Hello</p></body></html>"

    def fake_request(url, headers, timeout):
        del url, headers, timeout
        return FakeResponse(
            headers={"content-type": "text/html; charset=utf-8"},
            body_chunks=[html],
        )

    monkeypatch.setattr("agent.tools.webfetch._request", fake_request)

    result = WebFetchTool().execute({"url": "https://example.com", "format": "markdown"})

    assert "# Title" in result["output"]
    assert "Hello" in result["output"]


def test_request_should_enforce_size_limit_when_using_stdlib_stream(monkeypatch):
    class FakeStdlibResponse:
        status = 200
        headers = FakeHeaders({"content-type": "text/plain"})

        def __init__(self):
            self._chunks = [b"a" * MAX_RESPONSE_SIZE, b"b"]

        def read(self, size=-1):
            del size
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

    def fake_urlopen(req, timeout):
        del req, timeout
        return FakeStdlibResponse()

    monkeypatch.setattr("agent.tools.webfetch.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Response too large"):
        WebFetchTool().execute({"url": "https://example.com"})
