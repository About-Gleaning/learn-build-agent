from types import SimpleNamespace

from agent.core.message import (
    append_reasoning_part,
    append_text_part,
    append_tool_part,
    append_tool_call_part,
    create_message,
    extract_reasoning_content,
    extract_tool_calls,
    parse_provider_response,
    to_provider_messages,
)


def test_to_provider_messages_with_tool_result_and_text():
    session_id = "s1"

    system_msg = create_message("system", session_id)
    append_text_part(system_msg, "你是助手")

    user_msg = create_message("user", session_id)
    append_text_part(user_msg, "你好")

    provider_messages = to_provider_messages([system_msg, user_msg])

    assert provider_messages[0]["role"] == "system"
    assert provider_messages[0]["content"] == "你是助手"
    assert provider_messages[1]["role"] == "user"
    assert provider_messages[1]["content"] == "你好"


def test_parse_provider_response_extract_tool_calls():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.txt"}'),
    )
    provider_message = SimpleNamespace(content="", tool_calls=[tool_call])
    choice = SimpleNamespace(message=provider_message, finish_reason="tool_calls")
    response = SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))

    message = parse_provider_response(response, session_id="s1", model="m1")
    calls = extract_tool_calls(message)

    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "read_file"
    assert calls[0]["arguments"] == '{"path":"a.txt"}'
    assert message["info"]["finish_reason"] == "tool-calls"
    assert message["info"]["provider_finish_reason"] == "tool_calls"
    assert message["info"]["token_usage"]["total_tokens"] == 15


def test_parse_provider_response_should_extract_reasoning_content():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.txt"}'),
    )
    provider_message = SimpleNamespace(
        content="",
        reasoning_content="先确认路径，再读取文件。",
        tool_calls=[tool_call],
    )
    choice = SimpleNamespace(message=provider_message, finish_reason="tool_calls")
    response = SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))

    message = parse_provider_response(response, session_id="s1", model="m1")

    assert extract_reasoning_content(message) == "先确认路径，再读取文件。"
    assert message["info"]["finish_reason"] == "tool-calls"


def test_parse_provider_response_should_map_reasoning_only_to_unknown():
    provider_message = SimpleNamespace(
        content="",
        reasoning_content="先确认当前工作目录。",
        tool_calls=[],
    )
    choice = SimpleNamespace(message=provider_message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))

    message = parse_provider_response(response, session_id="s1", model="m1")

    assert extract_reasoning_content(message) == "先确认当前工作目录。"
    assert message["info"]["finish_reason"] == "unknown"


def test_to_provider_messages_should_include_reasoning_content_for_assistant_tool_call():
    session_id = "s_reasoning"
    assistant_msg = create_message("assistant", session_id)
    append_reasoning_part(assistant_msg, "先分析用户意图。")
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_1",
        name="read_file",
        arguments='{"path":"a.txt"}',
    )

    provider_messages = to_provider_messages([assistant_msg])

    assert provider_messages[0]["role"] == "assistant"
    assert provider_messages[0]["content"] == ""
    assert provider_messages[0]["reasoning_content"] == "先分析用户意图。"
    assert provider_messages[0]["tool_calls"][0]["function"]["name"] == "read_file"


def test_to_provider_messages_should_keep_old_assistant_message_shape_without_reasoning():
    session_id = "s_legacy"
    assistant_msg = create_message("assistant", session_id)
    append_text_part(assistant_msg, "我来读取文件。")
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_1",
        name="read_file",
        arguments='{"path":"a.txt"}',
    )

    provider_messages = to_provider_messages([assistant_msg])

    assert "reasoning_content" not in provider_messages[0]
    assert provider_messages[0]["content"] == "我来读取文件。"


def test_to_provider_messages_should_include_reasoning_content_without_tool_call():
    session_id = "s_reasoning_only"
    assistant_msg = create_message("assistant", session_id)
    append_reasoning_part(assistant_msg, "先确认环境。")

    provider_messages = to_provider_messages([assistant_msg])

    assert provider_messages[0]["role"] == "assistant"
    assert provider_messages[0]["content"] == ""
    assert provider_messages[0]["reasoning_content"] == "先确认环境。"


def test_to_provider_messages_should_include_tool_attachments_without_changing_content():
    session_id = "s_tool_attachment"
    tool_msg = create_message("tool", session_id)
    append_tool_part(
        tool_msg,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"path":"demo.pdf"}',
        output={
            "output": "PDF read successfully",
            "attachments": [
                {
                    "id": "att_1",
                    "sessionID": session_id,
                    "messageID": tool_msg["info"]["message_id"],
                    "type": "file",
                    "mime": "application/pdf",
                    "url": "data:application/pdf;base64,QUJDRA==",
                }
            ],
        },
    )

    provider_messages = to_provider_messages([tool_msg])

    assert provider_messages[0]["role"] == "tool"
    assert provider_messages[0]["content"] == "PDF read successfully"
    assert provider_messages[0]["attachments"][0]["mime"] == "application/pdf"
