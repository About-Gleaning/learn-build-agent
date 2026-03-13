import agent.runtime.compaction as compaction_module
from agent.core.message import (
    append_compaction_part,
    append_text_part,
    append_tool_call_part,
    append_tool_result_part,
    create_message,
    get_message_text,
    trim_messages_by_compaction_checkpoint,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore


def test_trim_messages_by_compaction_checkpoint_should_keep_latest_completed_suffix():
    session_id = "s_checkpoint"

    user_1 = create_message("user", session_id, status="completed")
    append_text_part(user_1, "U1")
    assistant_1 = create_message("assistant", session_id, status="completed", finish_reason="stop")
    append_text_part(assistant_1, "A1")

    user_2 = create_message("user", session_id, status="completed")
    append_compaction_part(user_2, "compaction")
    assistant_2 = create_message(
        "assistant",
        session_id,
        status="completed",
        finish_reason="stop",
        parent_id=str(user_2["info"]["message_id"]),
    )
    assistant_2["info"]["summary"] = True
    append_text_part(assistant_2, "A2")

    user_3 = create_message("user", session_id, status="completed")
    append_text_part(user_3, "U3")
    assistant_3 = create_message("assistant", session_id, status="completed", finish_reason="stop")
    append_text_part(assistant_3, "A3")
    user_4 = create_message("user", session_id, status="completed")
    append_text_part(user_4, "U4")

    trimmed = trim_messages_by_compaction_checkpoint([user_1, assistant_1, user_2, assistant_2, user_3, assistant_3, user_4])

    assert [get_message_text(message) for message in trimmed] == ["compaction", "A2", "U3", "A3", "U4"]


def test_trim_messages_by_compaction_checkpoint_should_ignore_incomplete_summary():
    session_id = "s_checkpoint_incomplete"

    user_1 = create_message("user", session_id, status="completed")
    append_text_part(user_1, "U1")
    user_2 = create_message("user", session_id, status="completed")
    append_compaction_part(user_2, "compaction")
    assistant_2 = create_message(
        "assistant",
        session_id,
        status="completed",
        parent_id=str(user_2["info"]["message_id"]),
    )
    assistant_2["info"]["summary"] = True
    append_text_part(assistant_2, "A2")

    original = [user_1, user_2, assistant_2]

    assert trim_messages_by_compaction_checkpoint(original) == original


def test_compaction_summary_should_build_checkpoint_pair(monkeypatch):
    monkeypatch.setattr(compaction_module, "THRESHOLD", 1)

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed", finish_reason="stop")
        append_text_part(assistant, "压缩后的摘要")
        return assistant

    monkeypatch.setattr(compaction_module, "create_chat_completion", fake_chat)

    system_message = create_message("system", "s_compaction", status="completed")
    append_text_part(system_message, "system")
    user_message = create_message("user", "s_compaction", status="completed")
    append_text_part(user_message, "原始上下文")

    compacted = compaction_module.compaction_summary([system_message, user_message])

    assert len(compacted) == 3
    assert get_message_text(compacted[1]) == "以下历史消息已完成压缩总结，请结合下一条摘要继续当前任务。\n以下是历史对话摘要请求，请参考下一条 summary assistant。"
    assert compacted[2]["info"]["summary"] is True
    assert compacted[2]["info"]["parent_id"] == compacted[1]["info"]["message_id"]
    assert get_message_text(compacted[2]) == "压缩后的摘要"


def test_inmemory_session_memory_store_should_not_split_tool_chain():
    session_id = "s_memory_tool_chain"
    store = InMemorySessionMemoryStore(max_messages=2)

    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "读取文件")

    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="tool_calls")
    append_tool_call_part(assistant_message, tool_call_id="call_1", name="read_file", arguments='{"path":"a.txt"}')

    tool_message = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_message, tool_call_id="call_1", name="read_file", content="hello")

    store.save(session_id, [user_message, assistant_message, tool_message])
    loaded = store.load(session_id)

    assert [message["info"]["role"] for message in loaded] == ["user", "assistant", "tool"]
