from types import SimpleNamespace

from src.message import (
    append_text_part,
    create_message,
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
    assert message["info"]["token_usage"]["total_tokens"] == 15
