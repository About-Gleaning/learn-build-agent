import pytest

from agent.config.settings import ResolvedLLMConfig
from agent.core.message import append_text_part, create_message, get_message_text
from agent.runtime.prompt_optimizer import optimize_prompt


def _build_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=4096,
        api_mode="chat_completions",
        base_url="https://example.test/v1",
        api_key="test-key",
        timeout_seconds=30,
    )


def test_optimize_prompt_should_call_llm_without_tools(monkeypatch):
    config = _build_config()
    captured = {}

    def fake_resolve_llm_config(mode, provider_name=None, model_name=None):
        captured["mode"] = mode
        captured["provider_name"] = provider_name
        captured["model_name"] = model_name
        return config

    def fake_create_chat_completion(messages, tools, llm_config=None, agent="", **kwargs):
        captured["messages"] = messages
        captured["tools"] = tools
        captured["llm_config"] = llm_config
        captured["agent"] = agent
        captured["kwargs"] = kwargs
        response = create_message(
            "assistant",
            session_id=messages[-1]["info"]["session_id"],
            provider=config.provider,
            model=config.model,
            status="completed",
            finish_reason="stop",
        )
        append_text_part(response, "优化后的 prompt")
        return response

    monkeypatch.setattr("agent.runtime.prompt_optimizer.resolve_llm_config", fake_resolve_llm_config)
    monkeypatch.setattr("agent.runtime.prompt_optimizer.create_chat_completion", fake_create_chat_completion)

    optimized_prompt, provider, model = optimize_prompt(
        "写一个接口",
        mode="plan",
        provider="gpt",
        model="gpt-4.1",
    )

    assert optimized_prompt == "优化后的 prompt"
    assert provider == "gpt"
    assert model == "gpt-4.1"
    assert captured["mode"] == "plan"
    assert captured["provider_name"] == "gpt"
    assert captured["model_name"] == "gpt-4.1"
    assert captured["tools"] == []
    assert captured["llm_config"] is config
    assert captured["agent"] == "plan"
    assert "写一个接口" in get_message_text(captured["messages"][-1])


def test_optimize_prompt_should_reject_empty_prompt():
    with pytest.raises(ValueError, match="prompt 不能为空"):
        optimize_prompt("   ")


def test_optimize_prompt_should_raise_when_llm_returns_empty(monkeypatch):
    config = _build_config()

    def fake_create_chat_completion(messages, tools, llm_config=None, agent="", **kwargs):
        return create_message(
            "assistant",
            session_id=messages[-1]["info"]["session_id"],
            provider=config.provider,
            model=config.model,
            status="completed",
            finish_reason="stop",
        )

    monkeypatch.setattr("agent.runtime.prompt_optimizer.resolve_llm_config", lambda *args, **kwargs: config)
    monkeypatch.setattr("agent.runtime.prompt_optimizer.create_chat_completion", fake_create_chat_completion)

    with pytest.raises(RuntimeError, match="空的优化结果"):
        optimize_prompt("写一个接口")
