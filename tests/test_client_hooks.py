from types import SimpleNamespace

import pytest

import agent.adapters.llm.client as client_module
import agent.adapters.llm.vendors as vendors_module
import agent.config.logging_setup as logging_setup_module
from agent.adapters.llm.client import (
    LLMHook,
    LoggingHook,
    _build_openai_client,
    _normalize_responses_tools,
    clear_global_hooks,
    create_chat_completion,
    create_chat_completion_stream,
    register_global_hook,
)
from agent.adapters.llm.protocols import normalize_qwen_responses_tools
from agent.adapters.llm.vendors import (
    KIMI_EXTRACTED_FILE_CONTEXT_PREFIX,
    KimiChatCompletionsAdapter,
    OpenAIResponsesAdapter,
    QwenResponsesAdapter,
    build_provider_adapter,
)
from agent.config.settings import LoggingSettings
from agent.config.settings import ResolvedLLMConfig
from agent.core.message import append_text_part, append_tool_part, create_message
from agent.core.message import append_reasoning_part, append_tool_call_part, extract_reasoning_content
from agent.tools.specs import build_agent_tools


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


def _build_tool_call_response(name: str = "todo_read"):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments="{}"),
    )
    provider_message = SimpleNamespace(content="", tool_calls=[tool_call])
    choice = SimpleNamespace(message=provider_message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_reasoning_tool_call_response(
    *,
    name: str = "todo_read",
    arguments: str = '{"path":"todo.md"}',
    content: str = "我先读取待办。",
    reasoning_content: str = "先确认有哪些待办项。",
):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    provider_message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=[tool_call],
    )
    choice = SimpleNamespace(message=provider_message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_reasoning_only_response(reasoning_content: str = "先确认当前工作目录。"):
    provider_message = SimpleNamespace(content="", reasoning_content=reasoning_content, tool_calls=[])
    choice = SimpleNamespace(message=provider_message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_user_message(session_id: str = "s_hook"):
    msg = create_message("user", session_id)
    append_text_part(msg, "hello")
    return [msg]


def _build_responses_text_response(content: str = "ok", reasoning: str = ""):
    output = []
    if reasoning:
        output.append(
            SimpleNamespace(
                type="reasoning",
                summary=[SimpleNamespace(text=reasoning)],
            )
        )
    output.append(
        SimpleNamespace(
            type="message",
            role="assistant",
            content=[SimpleNamespace(type="output_text", text=content)],
        )
    )
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)
    return SimpleNamespace(output=output, usage=usage, status="completed")


def _build_responses_tool_call_response(name: str = "todo_read", arguments: str = "{}"):
    output = [
        SimpleNamespace(
            type="function_call",
            call_id="call_1",
            name=name,
            arguments=arguments,
        )
    ]
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)
    return SimpleNamespace(output=output, usage=usage, status="completed")


def _build_chat_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        max_tokens=32000,
        api_mode="chat_completions",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )


def _build_qwen_responses_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )


def _build_kimi_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        agent="build",
        provider="kimi",
        vendor="kimi",
        model="kimi-k2.5",
        max_tokens=32000,
        api_mode="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        api_key="test-key",
        timeout_seconds=30,
    )


def _build_tool_followup_messages(session_id: str = "s_hook"):
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "plan_enter工具的描述怎么写的")

    assistant_msg = create_message("assistant", session_id)
    append_text_part(assistant_msg, "我来读取工具描述")

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_1",
        name="read_file",
        status="completed",
        arguments='{"path":"src/agent/tools/plan_enter.txt"}',
        output={"output": "使用这个工具来建议用户切换到 plan agent"},
    )
    return [user_msg, assistant_msg, tool_msg]


def _build_valid_tool_followup_messages(session_id: str = "s_hook"):
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "plan_enter工具的描述怎么写的")

    assistant_msg = create_message("assistant", session_id)
    append_text_part(assistant_msg, "我来读取工具描述")
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_1",
        name="read_file",
        arguments='{"path":"src/agent/tools/plan_enter.txt"}',
    )

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_1",
        name="read_file",
        status="completed",
        arguments='{"path":"src/agent/tools/plan_enter.txt"}',
        output={"output": "使用这个工具来建议用户切换到 plan agent"},
    )
    return [user_msg, assistant_msg, tool_msg]


def _build_pdf_tool_followup_messages(session_id: str = "s_hook"):
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "读取这个 PDF")

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"path":"docs/demo.pdf"}',
        output={
            "output": "PDF read successfully",
            "metadata": {"filename": "demo.pdf", "status": "completed"},
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": session_id,
                    "messageID": tool_msg["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
    )
    return [user_msg, tool_msg]


def _build_valid_pdf_tool_followup_messages(session_id: str = "s_hook"):
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "读取这个 PDF")

    assistant_msg = create_message("assistant", session_id)
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_pdf",
        name="read_file",
        arguments='{"path":"docs/demo.pdf"}',
    )

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"path":"docs/demo.pdf"}',
        output={
            "output": "PDF read successfully",
            "metadata": {"filename": "demo.pdf", "status": "completed"},
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": session_id,
                    "messageID": tool_msg["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
    )
    return [user_msg, assistant_msg, tool_msg]


def _build_system_and_pdf_tool_followup_messages(session_id: str = "s_hook"):
    system_msg = create_message("system", session_id)
    append_text_part(system_msg, "你是一个严格遵守指令的助手")

    messages = _build_pdf_tool_followup_messages(session_id)
    return [system_msg, *messages]


def _patch_openai_client(monkeypatch, create_fn, responses_create_fn=None):
    responses_create = responses_create_fn or create_fn
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create_fn,
            )
        ),
        responses=SimpleNamespace(
            create=responses_create,
        ),
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

    create_chat_completion(_build_user_message(), tools=[], hooks=[local_hook], llm_config=_build_chat_config())

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

    result = create_chat_completion(_build_user_message(), tools=[], llm_config=_build_chat_config())

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
        create_chat_completion(_build_user_message(), tools=[], llm_config=_build_chat_config())

    assert called["provider"] is False


def test_on_error_hook_called_when_provider_fails(monkeypatch):
    import agent.adapters.llm.client as client_module

    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _raise_timeout(**kwargs):
        raise TimeoutError("request timeout")

    _patch_openai_client(monkeypatch, _raise_timeout)

    result = create_chat_completion(_build_user_message(), tools=[], llm_config=_build_chat_config())

    assert result["info"]["status"] == "failed"
    assert recorder == ["timeout"]


def test_build_openai_client_should_pass_timeout_seconds(monkeypatch):
    captured: dict[str, object] = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(client_module, "OpenAI", fake_openai)

    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        max_tokens=32000,
        api_mode="chat_completions",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=12.5,
    )

    _build_openai_client(config)

    assert captured["timeout"] == 12.5
    assert captured["base_url"] == "https://example.com/v1"


def test_create_chat_completion_stream_should_return_timeout_error_message(monkeypatch):
    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _raise_timeout(**kwargs):
        raise TimeoutError("stream request timeout")

    _patch_openai_client(monkeypatch, _raise_timeout)

    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=_build_chat_config())
    with pytest.raises(StopIteration) as stop:
        next(stream)

    final_message = stop.value.value
    assert final_message["info"]["status"] == "failed"
    assert recorder == ["timeout"]


def test_create_chat_completion_should_call_responses_api_when_api_mode_is_responses(monkeypatch):
    captured_payload: dict[str, object] = {}
    call_recorder: list[str] = []

    def _capture_chat_create(**kwargs):
        call_recorder.append("chat")
        return _build_success_response("unexpected")

    def _capture_responses_create(**kwargs):
        call_recorder.append("responses")
        captured_payload.update(kwargs)
        return _build_responses_text_response("done")

    _patch_openai_client(monkeypatch, _capture_chat_create, responses_create_fn=_capture_responses_create)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=12345,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    result = create_chat_completion(_build_user_message(), tools=[], llm_config=config)

    assert result["info"]["status"] == "completed"
    assert call_recorder == ["responses"]
    assert captured_payload["model"] == "gpt-4.1"
    assert captured_payload["max_output_tokens"] == 12345
    assert captured_payload["store"] is False
    assert captured_payload["input"] == [{"role": "user", "content": "hello"}]


def test_create_chat_completion_should_use_configured_max_tokens_for_chat_completions(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_chat_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    _patch_openai_client(monkeypatch, _capture_chat_create)
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        max_tokens=23456,
        api_mode="chat_completions",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    create_chat_completion(_build_user_message(), tools=[], llm_config=config)

    assert captured_payload["max_tokens"] == 23456


def test_build_provider_adapter_should_choose_qwen_responses_adapter():
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    adapter = build_provider_adapter(config)

    assert isinstance(adapter, QwenResponsesAdapter)


def test_build_provider_adapter_should_choose_kimi_chat_adapter():
    config = ResolvedLLMConfig(
        agent="build",
        provider="kimi",
        vendor="kimi",
        model="kimi-k2.5",
        max_tokens=32000,
        api_mode="chat_completions",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    adapter = build_provider_adapter(config)

    assert isinstance(adapter, KimiChatCompletionsAdapter)


def test_build_provider_adapter_should_fallback_to_openai_responses_adapter():
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    adapter = build_provider_adapter(config)

    assert isinstance(adapter, OpenAIResponsesAdapter)


def test_create_chat_completion_should_convert_tool_history_for_responses_input(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_responses_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_responses_text_response("done")

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_capture_responses_create)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    create_chat_completion(_build_tool_followup_messages(), tools=[], llm_config=config)

    assert captured_payload["input"] == [
        {"role": "user", "content": "plan_enter工具的描述怎么写的"},
        {"role": "assistant", "content": "我来读取工具描述"},
        {"type": "function_call_output", "call_id": "call_1", "output": "使用这个工具来建议用户切换到 plan agent"},
    ]


def test_create_chat_completion_should_convert_pdf_attachment_for_responses_input(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_responses_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_responses_text_response("done")

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_capture_responses_create)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    create_chat_completion(_build_pdf_tool_followup_messages(), tools=[], llm_config=config)

    assert captured_payload["input"] == [
        {"role": "user", "content": "读取这个 PDF"},
        {
            "type": "function_call_output",
            "call_id": "call_pdf",
            "output": [
                {"type": "input_text", "text": "PDF read successfully"},
                {"type": "input_file", "file_data": "QUJDRA==", "filename": "demo.pdf"},
            ],
        },
    ]


def test_create_chat_completion_should_reject_file_attachment_for_qwen_responses(monkeypatch):
    called = False

    def _capture_responses_create(**kwargs):
        nonlocal called
        called = True
        return _build_responses_text_response("unexpected")

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_capture_responses_create)

    result = create_chat_completion(_build_pdf_tool_followup_messages(), tools=[], llm_config=_build_qwen_responses_config())

    assert called is False
    assert result["info"]["status"] == "failed"
    assert result["parts"][0]["type"] == "error"
    assert result["parts"][0]["meta"]["code"] == "unsupported_file_input"
    assert result["info"]["error"]["code"] == "unsupported_file_input"
    assert "qwen responses 暂不支持文件附件输入" in result["parts"][0]["content"]


def test_create_chat_completion_should_inject_kimi_pdf_context_before_chat_request(monkeypatch):
    captured_payload: dict[str, object] = {}
    cleanup_calls: list[dict[str, str]] = []
    messages = _build_valid_pdf_tool_followup_messages()

    def _capture_chat_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_capture_chat_create),
        ),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(id="file_pdf_1"),
            content=lambda **kwargs: SimpleNamespace(text="这是从 PDF 抽取出来的内容"),
            delete=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)
    monkeypatch.setattr(
        vendors_module,
        "_spawn_kimi_cleanup",
        lambda client, *, file_id, filename, cleanup_mode: cleanup_calls.append(
            {"file_id": file_id, "filename": filename, "cleanup_mode": cleanup_mode}
        ),
    )

    message = create_chat_completion(messages, tools=[], llm_config=_build_kimi_config())

    assert message["info"]["status"] == "completed"
    assert captured_payload["messages"] == [
        {"role": "user", "content": "读取这个 PDF"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_pdf",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"docs/demo.pdf"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "PDF read successfully",
            "tool_call_id": "call_pdf",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": "s_hook",
                    "messageID": messages[2]["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
        {
            "role": "user",
            "content": KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "这是从 PDF 抽取出来的内容",
        },
    ]
    metadata = messages[2]["parts"][0]["state"]["output"]["metadata"]
    assert metadata["extracted_file_contexts"] == [
        {
            "attachment_key": "att_1",
            "vendor": "kimi",
            "mime": "application/pdf",
            "filename": "demo.pdf",
            "content": "这是从 PDF 抽取出来的内容",
        }
    ]
    assert cleanup_calls == [
        {
            "file_id": "file_pdf_1",
            "filename": "demo.pdf",
            "cleanup_mode": "async_delete",
        }
    ]


def test_create_chat_completion_should_preserve_system_message_order_when_injecting_kimi_pdf_context(monkeypatch):
    captured_payload: dict[str, object] = {}
    messages = [
        create_message("system", "s_hook"),
        *_build_valid_pdf_tool_followup_messages(),
    ]
    append_text_part(messages[0], "你是一个严格遵守指令的助手")

    def _capture_chat_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_capture_chat_create),
        ),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(id="file_pdf_1"),
            content=lambda **kwargs: SimpleNamespace(text="这是从 PDF 抽取出来的内容"),
            delete=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)
    monkeypatch.setattr(vendors_module, "_spawn_kimi_cleanup", lambda *args, **kwargs: None)

    message = create_chat_completion(messages, tools=[], llm_config=_build_kimi_config())

    assert message["info"]["status"] == "completed"
    assert captured_payload["messages"] == [
        {"role": "system", "content": "你是一个严格遵守指令的助手"},
        {"role": "user", "content": "读取这个 PDF"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_pdf",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"docs/demo.pdf"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "PDF read successfully",
            "tool_call_id": "call_pdf",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": "s_hook",
                    "messageID": messages[3]["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
        {
            "role": "user",
            "content": KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "这是从 PDF 抽取出来的内容",
        },
    ]


def test_create_chat_completion_should_reuse_cached_kimi_pdf_context_without_reupload(monkeypatch):
    captured_payloads: list[list[dict[str, object]]] = []
    upload_calls: list[str] = []
    content_calls: list[str] = []
    cleanup_calls: list[dict[str, str]] = []
    messages = _build_valid_pdf_tool_followup_messages()

    def _capture_chat_create(**kwargs):
        captured_payloads.append(kwargs["messages"])
        return _build_success_response("done")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_capture_chat_create),
        ),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: upload_calls.append(kwargs["file"].name) or SimpleNamespace(id="file_pdf_1"),
            content=lambda **kwargs: content_calls.append(kwargs["file_id"]) or SimpleNamespace(text="这是从 PDF 抽取出来的内容"),
            delete=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)
    monkeypatch.setattr(
        vendors_module,
        "_spawn_kimi_cleanup",
        lambda client, *, file_id, filename, cleanup_mode: cleanup_calls.append(
            {"file_id": file_id, "filename": filename, "cleanup_mode": cleanup_mode}
        ),
    )

    first_message = create_chat_completion(messages, tools=[], llm_config=_build_kimi_config())

    followup_user = create_message("user", "s_hook")
    append_text_part(followup_user, "继续总结这个 PDF")
    second_message = create_chat_completion([*messages, followup_user], tools=[], llm_config=_build_kimi_config())

    assert first_message["info"]["status"] == "completed"
    assert second_message["info"]["status"] == "completed"
    assert upload_calls == ["demo.pdf"]
    assert content_calls == ["file_pdf_1"]
    assert cleanup_calls == [
        {
            "file_id": "file_pdf_1",
            "filename": "demo.pdf",
            "cleanup_mode": "async_delete",
        }
    ]
    assert captured_payloads[0] == [
        {"role": "user", "content": "读取这个 PDF"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_pdf",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"docs/demo.pdf"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "PDF read successfully",
            "tool_call_id": "call_pdf",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": "s_hook",
                    "messageID": messages[2]["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
        {
            "role": "user",
            "content": KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "这是从 PDF 抽取出来的内容",
        },
    ]
    assert captured_payloads[1] == [
        {"role": "user", "content": "读取这个 PDF"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_pdf",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"docs/demo.pdf"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "PDF read successfully",
            "tool_call_id": "call_pdf",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": "s_hook",
                    "messageID": messages[2]["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
        {
            "role": "user",
            "content": KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "这是从 PDF 抽取出来的内容",
        },
        {"role": "user", "content": "继续总结这个 PDF"},
    ]


def test_create_chat_completion_should_fail_when_kimi_file_extract_fails(monkeypatch):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: _build_success_response("unexpected"),
            )
        ),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("upload failed")),
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)

    result = create_chat_completion(_build_pdf_tool_followup_messages(), tools=[], llm_config=_build_kimi_config())

    assert result["info"]["status"] == "failed"
    assert result["parts"][0]["type"] == "error"
    assert result["parts"][0]["meta"]["code"] == "kimi_file_extract_failed"
    assert "Moonshot PDF 抽取失败" in result["parts"][0]["content"]


def test_create_chat_completion_should_flatten_function_tools_for_responses(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_responses_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_responses_text_response("done")

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_capture_responses_create)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    create_chat_completion(
        _build_user_message(),
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "todo_read",
                    "description": "读取 todo",
                    "parameters": {"type": "object"},
                },
            }
        ],
        llm_config=config,
    )

    assert captured_payload["tools"] == [
        {
            "type": "function",
            "name": "todo_read",
            "description": "读取 todo",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]


def test_normalize_responses_tools_should_remove_default_and_lock_object_boundaries():
    normalized = _normalize_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "task",
                    "description": "委派子代理",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "agent": {
                                "type": "string",
                                "enum": ["explorer", "worker"],
                                "default": "explorer",
                            },
                        },
                        "required": ["prompt"],
                    },
                },
            }
        ]
    )

    parameters = normalized[0]["parameters"]
    assert parameters["additionalProperties"] is False
    assert "default" not in parameters["properties"]["agent"]


def test_normalize_responses_tools_should_recurse_nested_object_items():
    normalized = _normalize_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "todo_write",
                    "description": "写入 todo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "todo_list": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "status": {"type": "string", "default": "pending"},
                                    },
                                    "required": ["text"],
                                },
                            }
                        },
                        "required": ["todo_list"],
                    },
                },
            }
        ]
    )

    item_schema = normalized[0]["parameters"]["properties"]["todo_list"]["items"]
    assert normalized[0]["parameters"]["additionalProperties"] is False
    assert item_schema["additionalProperties"] is False
    assert "default" not in item_schema["properties"]["status"]


def test_normalize_responses_tools_should_not_affect_chat_completion_payload(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    _patch_openai_client(monkeypatch, _capture_create)

    create_chat_completion(
        _build_user_message(),
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "task",
                    "description": "委派子代理",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent": {"type": "string", "default": "explorer"},
                        },
                    },
                },
            }
        ],
        llm_config=_build_chat_config(),
    )

    assert captured_payload["tools"][0]["function"]["parameters"]["properties"]["agent"]["default"] == "explorer"


def test_normalize_qwen_responses_tools_should_remove_non_core_schema_keywords():
    normalized = normalize_qwen_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "task",
                    "description": "委派子代理",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "任务说明"},
                            "agent": {
                                "type": "string",
                                "enum": ["explore", "worker"],
                                "default": "explore",
                            },
                        },
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
    )

    tool = normalized[0]
    parameters = tool["parameters"]
    assert "strict" not in tool
    assert parameters["required"] == ["prompt"]
    assert parameters["properties"]["agent"]["enum"] == ["explore", "worker"]
    assert "default" not in parameters["properties"]["agent"]
    assert parameters["properties"]["prompt"]["description"] == "任务说明"


def test_normalize_qwen_responses_tools_should_recurse_nested_items_and_keep_minimal_schema():
    normalized = normalize_qwen_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "todo_write",
                    "description": "写入 todo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "todo_list": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "status": {"type": "string", "default": "pending"},
                                    },
                                    "required": ["text"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["todo_list"],
                    },
                },
            }
        ]
    )

    item_schema = normalized[0]["parameters"]["properties"]["todo_list"]["items"]
    assert item_schema["required"] == ["text"]
    assert "default" not in item_schema["properties"]["status"]
    assert item_schema["additionalProperties"] is False


def test_normalize_qwen_responses_tools_should_collapse_empty_object_parameters():
    normalized = normalize_qwen_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "todo_read",
                    "description": "读取 todo",
                    "parameters": {"type": "object"},
                },
            }
        ]
    )

    assert normalized[0]["parameters"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }


def test_qwen_responses_adapter_should_use_vendor_specific_tool_normalization():
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )
    adapter = QwenResponsesAdapter(config)

    request = adapter.build_request(
        _build_user_message(),
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "todo_read",
                    "description": "读取 todo",
                    "strict": True,
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert request["tools"] == [
        {
            "type": "function",
            "name": "todo_read",
            "description": "读取 todo",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }
    ]


def test_qwen_responses_adapter_should_omit_parameters_for_empty_object_tools_in_build_agent():
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )
    adapter = QwenResponsesAdapter(config)

    request = adapter.build_request(
        _build_user_message(),
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "todo_read",
                    "description": "读取 todo",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "task",
                    "description": "委派子代理",
                    "parameters": {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                    },
                },
            },
        ],
    )

    assert request["tools"][0] == {
        "type": "function",
        "name": "todo_read",
        "description": "读取 todo",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }
    assert request["tools"][1]["parameters"]["required"] == ["prompt"]


def test_qwen_responses_adapter_should_omit_parameters_for_no_arg_agent_tools():
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )
    adapter = QwenResponsesAdapter(config)

    request = adapter.build_request(_build_user_message(), tools=build_agent_tools("build", skills=[]))
    tools_by_name = {tool["name"]: tool for tool in request["tools"]}

    assert tools_by_name["todo_read"]["parameters"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }
    assert tools_by_name["plan_enter"]["parameters"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }
    assert tools_by_name["glob"]["parameters"]["required"] == ["pattern"]
    assert "path" in tools_by_name["glob"]["parameters"]["properties"]
    assert tools_by_name["grep"]["parameters"]["required"] == ["pattern"]
    assert "include" in tools_by_name["grep"]["parameters"]["properties"]
    assert tools_by_name["read_file"]["parameters"]["required"] == ["file_path"]
    assert "file_path" in tools_by_name["read_file"]["parameters"]["properties"]
    assert tools_by_name["task"]["parameters"]["required"] == ["prompt"]


def test_normalize_qwen_responses_tools_should_keep_boolean_and_additional_properties():
    normalized = normalize_qwen_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "toggle",
                    "description": "布尔参数",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                        },
                        "required": ["enabled"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
    )

    parameters = normalized[0]["parameters"]
    assert parameters["properties"]["enabled"]["type"] == "boolean"
    assert parameters["additionalProperties"] is False


def test_normalize_qwen_responses_tools_should_filter_required_fields_not_in_properties():
    normalized = normalize_qwen_responses_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "task",
                    "description": "required 过滤",
                    "parameters": {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt", "missing"],
                    },
                },
            }
        ]
    )

    assert normalized[0]["parameters"]["required"] == ["prompt"]


def test_stream_should_yield_delta_and_return_message(monkeypatch):
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
    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=_build_chat_config())

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


def test_stream_should_persist_reasoning_content(monkeypatch):
    class Delta:
        def __init__(self, content="", reasoning_content="", tool_calls=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = tool_calls or []

    class Choice:
        def __init__(self, delta, finish_reason=""):
            self.delta = delta
            self.finish_reason = finish_reason

    class Chunk:
        def __init__(self, delta, finish_reason=""):
            self.choices = [Choice(delta, finish_reason=finish_reason)]
            self.usage = None

    def _fake_stream(**kwargs):
        yield Chunk(Delta(reasoning_content="先"), "")
        yield Chunk(Delta(reasoning_content="分析"), "")
        yield Chunk(Delta(content="结果"), "stop")

    _patch_openai_client(monkeypatch, _fake_stream)
    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=_build_chat_config())

    while True:
        try:
            next(stream)
        except StopIteration as stop:
            final_message = stop.value
            break

    assert extract_reasoning_content(final_message) == "先分析"
    assert final_message["parts"][0]["type"] == "text"
    assert final_message["parts"][0]["content"] == "结果"


def test_logging_hook_should_log_stream_response_with_reasoning_and_tool_calls(monkeypatch, caplog):
    class Delta:
        def __init__(self, content="", reasoning_content="", tool_calls=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = tool_calls or []

    class ToolFunction:
        def __init__(self, name="", arguments=""):
            self.name = name
            self.arguments = arguments

    class ToolCall:
        def __init__(self, index=0, tool_call_id="", name="", arguments=""):
            self.index = index
            self.id = tool_call_id
            self.function = ToolFunction(name=name, arguments=arguments)

    class Choice:
        def __init__(self, delta, finish_reason=""):
            self.delta = delta
            self.finish_reason = finish_reason

    class Chunk:
        def __init__(self, delta, finish_reason=""):
            self.choices = [Choice(delta, finish_reason=finish_reason)]
            self.usage = None

    def _fake_stream(**kwargs):
        yield Chunk(Delta(reasoning_content="先分析"), "")
        yield Chunk(Delta(content="我来处理"), "")
        yield Chunk(Delta(tool_calls=[ToolCall(index=0, tool_call_id="call_1", name="todo_read", arguments='{"path":"todo.md"}')]), "")
        yield Chunk(Delta(), "tool_calls")

    _patch_openai_client(monkeypatch, _fake_stream)

    with caplog.at_level("INFO"):
        stream = create_chat_completion_stream(
            _build_user_message(),
            tools=[{"type": "function"}],
            agent="build",
            llm_config=_build_chat_config(),
        )
        while True:
            try:
                next(stream)
            except StopIteration:
                break

    assert "llm.response finish_reason=tool-calls" in caplog.text
    assert "message=我来处理" in caplog.text
    assert "reasoning=先分析" in caplog.text
    assert "tool_names=todo_read" in caplog.text
    assert 'tool_calls=todo_read[call_1] args={"path":"todo.md"}' in caplog.text


def test_responses_stream_should_yield_delta_and_return_message(monkeypatch):
    def _fake_responses_stream(**kwargs):
        yield SimpleNamespace(type="response.output_text.delta", delta="流")
        yield SimpleNamespace(type="response.output_text.delta", delta="式")
        yield SimpleNamespace(type="response.completed", response=_build_responses_text_response("流式", reasoning="先分析"))

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_fake_responses_stream)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=config)

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
    text_part = next(part for part in final_message["parts"] if part["type"] == "text")
    assert text_part["content"] == "流式"
    assert extract_reasoning_content(final_message) == "先分析"


def test_create_chat_completion_stream_should_reject_file_attachment_for_qwen_responses(monkeypatch):
    called = False

    def _capture_responses_create(**kwargs):
        nonlocal called
        called = True
        return iter(())

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_capture_responses_create)

    stream = create_chat_completion_stream(
        _build_pdf_tool_followup_messages(),
        tools=[],
        llm_config=_build_qwen_responses_config(),
    )
    with pytest.raises(StopIteration) as stop:
        next(stream)

    final_message = stop.value.value
    assert called is False
    assert final_message["info"]["status"] == "failed"
    assert final_message["parts"][0]["meta"]["code"] == "unsupported_file_input"
    assert final_message["info"]["error"]["code"] == "unsupported_file_input"
    assert "qwen responses 暂不支持文件附件输入" in final_message["parts"][0]["content"]


def test_responses_stream_should_aggregate_function_call_arguments(monkeypatch):
    def _fake_responses_stream(**kwargs):
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(type="function_call", call_id="call_1", name="todo_read", arguments=""),
        )
        yield SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='{"path":"')
        yield SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='todo.md"}')

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_fake_responses_stream)
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=config)

    while True:
        try:
            next(stream)
        except StopIteration as stop:
            final_message = stop.value
            break

    tool_part = next(part for part in final_message["parts"] if part["type"] == "tool")
    assert tool_part["name"] == "todo_read"
    assert tool_part["state"]["input"]["arguments"] == '{"path":"todo.md"}'


def test_responses_stream_should_surface_nested_failed_error_message(monkeypatch, caplog):
    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _fake_responses_stream(**kwargs):
        yield SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(code="bad_request", type="invalid_request_error", message="provider rejected request"),
            ),
        )

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_fake_responses_stream)
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    with caplog.at_level("WARNING"):
        stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=config)
        with pytest.raises(StopIteration) as stop:
            next(stream)

    final_message = stop.value.value
    assert final_message["info"]["status"] == "failed"
    assert final_message["info"]["error"]["message"] == "provider rejected request"
    assert "llm.responses_stream_failure event_type=response.failed" in caplog.text
    assert "error_code=bad_request" in caplog.text
    assert recorder == ["api_error"]


def test_responses_stream_should_surface_incomplete_reason(monkeypatch, caplog):
    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _fake_responses_stream(**kwargs):
        yield SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
        )

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_fake_responses_stream)
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    with caplog.at_level("WARNING"):
        stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=config)
        with pytest.raises(StopIteration) as stop:
            next(stream)

    final_message = stop.value.value
    assert final_message["info"]["status"] == "failed"
    assert final_message["info"]["error"]["message"] == "max_output_tokens"
    assert "llm.responses_stream_failure event_type=response.incomplete" in caplog.text
    assert "incomplete_reason=max_output_tokens" in caplog.text
    assert recorder == ["api_error"]


def test_responses_stream_should_fallback_to_event_type_when_no_detail_exists(monkeypatch):
    recorder: list[str] = []
    clear_global_hooks()
    register_global_hook(ErrorCaptureHook(recorder))

    def _fake_responses_stream(**kwargs):
        yield SimpleNamespace(type="response.failed", response=SimpleNamespace(status="failed"))

    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("unused"), responses_create_fn=_fake_responses_stream)
    config = ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3.5-flash",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )

    stream = create_chat_completion_stream(_build_user_message(), tools=[], llm_config=config)
    with pytest.raises(StopIteration) as stop:
        next(stream)

    final_message = stop.value.value
    assert final_message["info"]["status"] == "failed"
    assert final_message["info"]["error"]["message"] == "failed"
    assert recorder == ["api_error"]


def test_create_chat_completion_should_replay_reasoning_content_in_request(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    _patch_openai_client(monkeypatch, _capture_create)

    session_id = "s_reasoning_replay"
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "帮我看一下文件")

    assistant_msg = create_message("assistant", session_id)
    append_reasoning_part(assistant_msg, "先定位文件。")
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_1",
        name="read_file",
        arguments='{"path":"a.txt"}',
    )

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_1",
        name="read_file",
        status="completed",
        arguments='{"path":"a.txt"}',
        output={"output": "file-content"},
    )

    create_chat_completion([user_msg, assistant_msg, tool_msg], tools=[], llm_config=_build_chat_config())

    provider_messages = captured_payload["messages"]
    assert provider_messages[1]["role"] == "assistant"
    assert provider_messages[1]["reasoning_content"] == "先定位文件。"
    assert provider_messages[1]["tool_calls"][0]["function"]["name"] == "read_file"


def test_create_chat_completion_should_replay_reasoning_only_assistant_in_request(monkeypatch):
    captured_payload: dict[str, object] = {}

    def _capture_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    _patch_openai_client(monkeypatch, _capture_create)

    session_id = "s_reasoning_only_replay"
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "当前工作目录是多少")

    assistant_msg = create_message("assistant", session_id)
    append_reasoning_part(assistant_msg, "先确认当前工作目录。")

    create_chat_completion([user_msg, assistant_msg], tools=[], llm_config=_build_chat_config())

    provider_messages = captured_payload["messages"]
    assert provider_messages[1]["role"] == "assistant"
    assert provider_messages[1]["reasoning_content"] == "先确认当前工作目录。"


def test_create_chat_completion_should_reject_missing_tool_response_before_non_tool_message(monkeypatch):
    session_id = "s_invalid_tool_sequence"
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "读取 PDF")

    assistant_msg = create_message("assistant", session_id)
    append_tool_call_part(assistant_msg, tool_call_id="call_pdf", name="read_file", arguments='{"file_path":"a.pdf"}')
    append_tool_call_part(assistant_msg, tool_call_id="call_rule", name="read_file", arguments='{"file_path":"b.md"}')

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"file_path":"a.pdf"}',
        output={
            "output": "PDF read successfully",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": session_id,
                    "messageID": tool_msg["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
    )

    synthetic_user = create_message("user", session_id)
    append_text_part(synthetic_user, KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "抽取内容")

    message = create_chat_completion(
        [user_msg, assistant_msg, tool_msg, synthetic_user],
        tools=[],
        llm_config=_build_kimi_config(),
    )

    assert message["info"]["status"] == "failed"
    assert message["info"]["error"]["code"] == "api_error"
    assert "invalid_tool_message_sequence" in message["info"]["error"]["message"]


def test_create_chat_completion_should_reject_orphan_tool_message(monkeypatch):
    session_id = "s_invalid_orphan_tool"
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "读取文件")

    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_orphan",
        name="read_file",
        status="completed",
        arguments='{"file_path":"demo.txt"}',
        output={"output": "hello"},
    )

    message = create_chat_completion(
        [user_msg, tool_msg],
        tools=[],
        llm_config=_build_chat_config(),
    )

    assert message["info"]["status"] == "failed"
    assert message["info"]["error"]["code"] == "api_error"
    assert "invalid_tool_message_sequence" in message["info"]["error"]["message"]
    assert "孤儿 tool 响应" in message["info"]["error"]["message"]


def test_kimi_build_messages_should_defer_pdf_context_until_all_active_tool_results_finish(monkeypatch):
    captured_payload: dict[str, object] = {}
    session_id = "s_kimi_multi_tool"
    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "处理这个 PDF 和模板")

    assistant_msg = create_message("assistant", session_id)
    append_tool_call_part(assistant_msg, tool_call_id="call_pdf", name="read_file", arguments='{"file_path":"demo.pdf"}')
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_template",
        name="read_file",
        arguments='{"file_path":"rule.md"}',
    )

    pdf_tool_msg = create_message("tool", session_id)
    append_tool_part(
        pdf_tool_msg,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"file_path":"demo.pdf"}',
        output={
            "output": "PDF read successfully",
            "attachments": [
                {
                    "id": "att_pdf",
                    "sessionID": session_id,
                    "messageID": pdf_tool_msg["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "filename": "demo.pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
    )

    failed_tool_msg = create_message("tool", session_id)
    append_tool_part(
        failed_tool_msg,
        tool_call_id="call_template",
        name="read_file",
        status="failed",
        arguments='{"file_path":"rule.md"}',
        output={
            "output": "Error: read_file 路径超出允许范围: /tmp/rule.md",
            "metadata": {"status": "failed", "error_code": "read_path_forbidden"},
        },
    )

    def _capture_chat_create(**kwargs):
        captured_payload.update(kwargs)
        return _build_success_response("done")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_capture_chat_create)),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(id="file_pdf_1"),
            content=lambda **kwargs: SimpleNamespace(text="这是从 PDF 抽取出来的内容"),
            delete=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)
    monkeypatch.setattr(vendors_module, "_spawn_kimi_cleanup", lambda *args, **kwargs: None)

    message = create_chat_completion(
        [user_msg, assistant_msg, pdf_tool_msg, failed_tool_msg],
        tools=[],
        llm_config=_build_kimi_config(),
    )

    assert message["info"]["status"] == "completed"
    provider_messages = captured_payload["messages"]
    assert provider_messages[1]["role"] == "assistant"
    assert provider_messages[2]["role"] == "tool"
    assert provider_messages[2]["tool_call_id"] == "call_pdf"
    assert provider_messages[3]["role"] == "tool"
    assert provider_messages[3]["tool_call_id"] == "call_template"
    assert provider_messages[4]["role"] == "user"
    assert provider_messages[4]["content"] == KIMI_EXTRACTED_FILE_CONTEXT_PREFIX + "这是从 PDF 抽取出来的内容"


def test_logging_hook_should_log_chat_request_messages(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("done"))

    with caplog.at_level("INFO"):
        create_chat_completion(_build_user_message(), tools=[], agent="build", llm_config=_build_chat_config())

    assert 'llm.request api_mode=chat_completions messages=[{"role":"user","content":"hello"}]' in caplog.text
    assert all(record.agent == "build" for record in caplog.records)
    assert all(record.model == "unknown" or isinstance(record.model, str) for record in caplog.records)


def test_logging_hook_should_log_response_text(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("done"))

    with caplog.at_level("INFO"):
        create_chat_completion(_build_user_message(), tools=[], agent="plan", llm_config=_build_chat_config())

    assert "llm.response finish_reason=stop" in caplog.text
    assert "message=done" in caplog.text
    assert any(record.agent == "plan" for record in caplog.records)


def test_logging_hook_should_log_tool_name_when_model_requests_tool(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_tool_call_response("todo_read"))

    with caplog.at_level("INFO"):
        create_chat_completion(
            _build_user_message(),
            tools=[{"type": "function"}],
            agent="build",
            llm_config=_build_chat_config(),
        )

    assert "llm.response finish_reason=tool-calls" in caplog.text
    assert "tool_names=todo_read" in caplog.text
    assert "tool_calls=todo_read[call_1] args={}" in caplog.text


def test_logging_hook_should_log_text_reasoning_and_tool_calls_together(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_reasoning_tool_call_response())

    with caplog.at_level("INFO"):
        create_chat_completion(
            _build_user_message(),
            tools=[{"type": "function"}],
            agent="build",
            llm_config=_build_chat_config(),
        )

    assert "message=我先读取待办。" in caplog.text
    assert "reasoning=先确认有哪些待办项。" in caplog.text
    assert "tool_names=todo_read" in caplog.text
    assert 'tool_calls=todo_read[call_1] args={"path":"todo.md"}' in caplog.text


def test_logging_hook_should_not_truncate_tool_calls_when_disabled(monkeypatch, caplog):
    long_arguments = '{"content":"' + ("x" * 1200) + '"}'
    monkeypatch.setattr(
        logging_setup_module,
        "resolve_logging_settings",
        lambda: LoggingSettings(truncate_enabled=False, truncate_limit=500),
    )
    _patch_openai_client(
        monkeypatch,
        lambda **kwargs: _build_reasoning_tool_call_response(arguments=long_arguments),
    )

    with caplog.at_level("INFO"):
        create_chat_completion(
            _build_user_message(),
            tools=[{"type": "function"}],
            agent="build",
            llm_config=_build_chat_config(),
        )

    assert "...<truncated>" not in caplog.text
    assert f'tool_calls=todo_read[call_1] args={long_arguments}' in caplog.text


def test_logging_hook_should_log_reasoning_only_response(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_reasoning_only_response("先确认当前工作目录。"))

    with caplog.at_level("INFO"):
        create_chat_completion(_build_user_message(), tools=[], agent="build", llm_config=_build_chat_config())

    assert "finish_reason=unknown" in caplog.text
    assert "message=" in caplog.text
    assert "reasoning=先确认当前工作目录。" in caplog.text


def test_logging_hook_should_log_full_request_messages_on_tool_followup(monkeypatch, caplog):
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_success_response("done"))

    with caplog.at_level("INFO"):
        create_chat_completion(_build_valid_tool_followup_messages(), tools=[], agent="build", llm_config=_build_chat_config())

    assert 'messages=[{"role":"user","content":"plan_enter工具的描述怎么写的"},{"role":"assistant","content":"我来读取工具描述","tool_calls":[{"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\\"path\\":\\"src/agent/tools/plan_enter.txt\\"}"}}]},{"role":"tool","content":"使用这个工具来建议用户切换到 plan agent","tool_call_id":"call_1"}]' in caplog.text


def test_logging_hook_should_log_responses_input_structure_on_tool_followup(monkeypatch, caplog):
    config = ResolvedLLMConfig(
        agent="build",
        provider="gpt",
        vendor="openai",
        model="gpt-4.1",
        max_tokens=32000,
        api_mode="responses",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )
    _patch_openai_client(monkeypatch, lambda **kwargs: _build_responses_text_response("done"))

    with caplog.at_level("INFO"):
        create_chat_completion(_build_pdf_tool_followup_messages(), tools=[], agent="build", llm_config=config)

    assert "llm.request api_mode=responses" in caplog.text
    assert '[{"role":"user","content":"读取这个 PDF"},{"type":"function_call_output","call_id":"call_pdf","output":[{"type":"input_text","text":"PDF read successfully"},{"type":"input_file","file_data":"[omitted_file_data length=8]","filename":"demo.pdf"}]}]' in caplog.text
    assert "data:application/pdf;base64" not in caplog.text


def test_logging_hook_should_redact_data_url_in_chat_request_messages(monkeypatch, caplog):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: _build_success_response("done"))),
        responses=SimpleNamespace(create=lambda **kwargs: _build_responses_text_response("unused")),
        files=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(id="file_pdf_1"),
            content=lambda **kwargs: SimpleNamespace(text="这是从 PDF 抽取出来的内容"),
            delete=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(client_module, "_build_openai_client", lambda _config: fake_client)
    monkeypatch.setattr(vendors_module, "_spawn_kimi_cleanup", lambda *args, **kwargs: None)

    with caplog.at_level("INFO"):
        create_chat_completion(_build_valid_pdf_tool_followup_messages(), tools=[], agent="build", llm_config=_build_kimi_config())

    assert "llm.request api_mode=chat_completions" in caplog.text
    assert "[omitted_data_url mime=application/pdf length=" in caplog.text
    assert "data:application/pdf;base64" not in caplog.text
