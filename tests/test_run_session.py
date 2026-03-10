from src.main import run_session
from src.message import (
    append_text_part,
    append_tool_call_part,
    create_message,
    get_message_text,
)


def test_run_session_with_tool_call(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1

        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_1",
                name="todo_read",
                arguments="{}",
            )
        else:
            append_text_part(assistant, "最终答案")
        return assistant

    monkeypatch.setattr("src.main.create_chat_completion", fake_chat)

    result = run_session("测试", session_id="s_test")

    assert result["info"]["role"] == "assistant"
    assert get_message_text(result) == "最终答案"


def test_run_session_end_on_failed_message(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="failed")
        append_text_part(assistant, "Error: 模型调用失败")
        return assistant

    monkeypatch.setattr("src.main.create_chat_completion", fake_chat)

    result = run_session("测试失败", session_id="s_fail")

    assert result["info"]["status"] == "failed"
    assert "模型调用失败" in get_message_text(result)
