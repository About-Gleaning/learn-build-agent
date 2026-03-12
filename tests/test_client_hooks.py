from types import SimpleNamespace

import pytest

from agent.adapters.llm.client import (
    LLMHook,
    LoggingHook,
    clear_global_hooks,
    create_chat_completion,
    create_chat_completion_stream,
    register_global_hook,
)
from agent.core.message import append_text_part, create_message


@pytest.fixture(autouse=True)
def reset_global_hooks():
    clear_global_hooks()
    register_global_hook(LoggingHook())
    yield
    clear_global_hooks()
    register_global_hook(LoggingHook())


class RecorderHook(LLMHook):
    def __init__(self, name: str, recorder: list[str], fail_fast: bool = False) -> None:
        super().__init__(name=name, fail_fast=fail_fast)
        self.recorder = recorder

    def before_call(self, ctx):
        self.recorder.append(f"{self.name}.before")

    def after_call(self, ctx, message):
        self.recorder.append(f"{self.name}.after")

    def on_error(self, ctx, error, normalized_error):
        self.recorder.append(f"{self.name}.error:{normalized_error.get('code', '')}")


class BrokenHook(LLMHook):
    def __init__(self, fail_fast: bool) -> None:
        super().__init__(name="broken", fail_fast=fail_fast)

    def before_call(self, ctx):
        raise RuntimeError("before hook failed")


class ErrorCaptureHook(LLMHook):
    def __init__(self, recorder: list[str]) -> None:
        super().__init__(name="error_capture", fail_fast=False)
        self.recorder = recorder

    def on_error(self, ctx, error, normalized_error):
        self.recorder.append(normalized_error.get("code", ""))


def _build_success_response(content: str = "ok"):
    provider_message = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=provider_message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_user_message(session_id: str = "s_hook"):
    msg = create_message("user", session_id)
    append_text_part(msg, "hello")
    return [msg]


def _patch_openai_client(monkeypatch, create_fn):
    import agent.adapters.llm.client as client_module

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create_fn,
            )
        )
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)


def test_hooks_execute_in_order_global_then_local(monkeypatch):
    import agent.adapters.llm.client as client_module

    clear_global_hooks()
    recorder: list[str] = []
    register_global_hook(RecorderHook("g1", recorder))
    register_global_hook(RecorderHook("g2", recorder))
    local_hook = RecorderHook("l1", recorder)

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("done"))

    create_chat_completion(_build_user_message(), tools=[], hooks=[local_hook])

    assert recorder == [
        "g1.before",
        "g2.before",
        "l1.before",
        "g1.after",
        "g2.after",
        "l1.after",
    ]


def test_hook_fail_open_should_continue(monkeypatch):
    import agent.adapters.llm.client as client_module

    clear_global_hooks()
    register_global_hook(BrokenHook(fail_fast=False))

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("continue"))

    result = create_chat_completion(_build_user_message(), tools=[])

    assert result["info"]["status"] == "completed"


def test_hook_fail_fast_should_raise(monkeypatch):
    import agent.adapters.llm.client as client_module

    called = {"provider": False}

    def _provider_call(**kwargs):
        called["provider"] = True
        return _build_success_response("should-not-run")

    clear_global_hooks()
    register_global_hook(BrokenHook(fail_fast=True))
    _patch_openai_client(monkeypatch, _provider_call)

    with pytest.raises(RuntimeError, match="Hook 'broken' failed"):
        create_chat_completion(_build_user_message(), tools=[])

    assert called["provider"] is False


def test_on_error_hook_called_when_provider_fails(monkeypatch):
    import agent.adapters.llm.client as client_module

    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _raise_timeout(**kwargs):
        raise TimeoutError("request timeout")

    _patch_openai_client(monkeypatch, _raise_timeout)

    result = create_chat_completion(_build_user_message(), tools=[])

    assert result["info"]["status"] == "failed"
    assert recorder == ["timeout"]


def test_stream_should_yield_delta_and_return_message(monkeypatch):
    import agent.adapters.llm.client as client_module

    class Delta:
        def __init__(self, content):
            self.content = content
            self.tool_calls = []

    class Choice:
        def __init__(self, content, finish_reason=""):
            self.delta = Delta(content)
            self.finish_reason = finish_reason

    class Chunk:
        def __init__(self, content, finish_reason=""):
            self.choices = [Choice(content, finish_reason=finish_reason)]
            self.usage = None

    def _fake_stream(**kwargs):
        yield Chunk("流")
        yield Chunk("式")
        yield Chunk("", finish_reason="stop")

    _patch_openai_client(monkeypatch, _fake_stream)
    stream = create_chat_completion_stream(_build_user_message(), tools=[])

    deltas: list[str] = []
    while True:
        try:
            item = next(stream)
            deltas.append(str(item.get("delta", "")))
        except StopIteration as stop:
            final_message = stop.value
            break

    assert "".join(deltas) == "流式"
    assert final_message["info"]["status"] == "completed"
