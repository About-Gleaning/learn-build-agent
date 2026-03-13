from __future__ import annotations

import os

import pytest

from agent.tools.websearch import WebSearchTool


class DummyContext:
    def __init__(self) -> None:
        self.payload = None

    def ask(self, payload) -> None:
        self.payload = payload


class FakeClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_websearch_should_require_query():
    with pytest.raises(ValueError, match="query is required"):
        WebSearchTool(api_key="k").execute({})


def test_websearch_should_validate_num_results():
    with pytest.raises(ValueError, match="numResults must be an integer"):
        WebSearchTool(api_key="k").execute({"query": "python", "numResults": "3"})  # type: ignore[arg-type]


def test_websearch_should_validate_context_max_characters():
    with pytest.raises(ValueError, match="contextMaxCharacters must be greater than 0"):
        WebSearchTool(api_key="k").execute({"query": "python", "contextMaxCharacters": 0})


def test_websearch_should_validate_livecrawl():
    with pytest.raises(ValueError, match="livecrawl must be one of"):
        WebSearchTool(api_key="k").execute({"query": "python", "livecrawl": "always"})  # type: ignore[arg-type]


def test_websearch_should_validate_type():
    with pytest.raises(ValueError, match="type must be one of"):
        WebSearchTool(api_key="k").execute({"query": "python", "type": "full"})  # type: ignore[arg-type]


def test_websearch_should_require_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    with pytest.raises(ValueError, match="EXA_API_KEY is required"):
        WebSearchTool().execute({"query": "python"})


def test_websearch_should_raise_when_exa_dependency_missing(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "env-key")

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "exa_py":
            raise ModuleNotFoundError("No module named 'exa_py'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(ModuleNotFoundError, match="Missing dependency 'exa-py'"):
        WebSearchTool().execute({"query": "python"})


def test_websearch_should_return_empty_results_message(monkeypatch):
    client = FakeClient(response={"results": []})
    monkeypatch.setattr("agent.tools.websearch._create_client", lambda api_key: client)

    result = WebSearchTool(api_key="k").execute({"query": "python"})

    assert result["title"] == "Web search: python"
    assert "No search results found" in result["output"]
    assert client.calls[0]["query"] == "python"


def test_websearch_should_format_text_results(monkeypatch):
    client = FakeClient(
        response={
            "results": [
                {
                    "title": "Python",
                    "url": "https://python.org",
                    "score": 0.99,
                    "text": "Python language",
                }
            ]
        }
    )
    monkeypatch.setattr("agent.tools.websearch._create_client", lambda api_key: client)

    result = WebSearchTool(api_key="k").execute({"query": "python", "numResults": 3})

    assert "1. Python" in result["output"]
    assert "URL: https://python.org" in result["output"]
    assert "Score: 0.99" in result["output"]
    assert "Python language" in result["output"]
    assert client.calls[0]["num_results"] == 3


def test_websearch_should_fallback_to_summary_and_highlights(monkeypatch):
    client = FakeClient(
        response={
            "results": [
                {"title": "One", "summary": "summary text"},
                {"title": "Two", "highlights": [" first ", "", "second"]},
            ]
        }
    )
    monkeypatch.setattr("agent.tools.websearch._create_client", lambda api_key: client)

    result = WebSearchTool(api_key="k").execute({"query": "python"})

    assert "summary text" in result["output"]
    assert "first\nsecond" in result["output"]


def test_websearch_should_use_explicit_api_key_before_env(monkeypatch):
    client = FakeClient(response={"results": []})
    captured: dict[str, str] = {}

    def fake_create_client(api_key: str):
        captured["api_key"] = api_key
        return client

    monkeypatch.setenv("EXA_API_KEY", "env-key")
    monkeypatch.setattr("agent.tools.websearch._create_client", fake_create_client)

    WebSearchTool().execute({"query": "python", "api_key": "param-key"})

    assert captured["api_key"] == "param-key"


def test_websearch_should_send_permission_payload(monkeypatch):
    client = FakeClient(response={"results": []})
    context = DummyContext()
    monkeypatch.setattr("agent.tools.websearch._create_client", lambda api_key: client)

    WebSearchTool(api_key="k").execute(
        {
            "query": "python",
            "numResults": 5,
            "livecrawl": "preferred",
            "type": "fast",
            "contextMaxCharacters": 2000,
        },
        context,
    )

    assert context.payload["permission"] == "websearch"
    assert context.payload["metadata"]["query"] == "python"
    assert context.payload["metadata"]["numResults"] == 5
    assert context.payload["metadata"]["livecrawl"] == "preferred"
    assert context.payload["metadata"]["type"] == "fast"
    assert context.payload["metadata"]["contextMaxCharacters"] == 2000


def test_websearch_should_pass_livecrawl_and_content_limits(monkeypatch):
    client = FakeClient(response={"results": []})
    monkeypatch.setattr("agent.tools.websearch._create_client", lambda api_key: client)

    WebSearchTool(api_key="k").execute(
        {
            "query": "python",
            "numResults": 2,
            "livecrawl": "fallback",
            "type": "deep",
            "contextMaxCharacters": 1234,
        }
    )

    assert client.calls[0]["livecrawl"] == "fallback"
    assert client.calls[0]["type"] == "deep"
    assert client.calls[0]["contents"] == {"text": {"max_characters": 1234}}
