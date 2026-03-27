import agent.runtime.compaction as compaction_module
from agent.config.settings import CompactionSettings, clear_runtime_settings_cache
from agent.core.message import (
    append_compaction_part,
    append_text_part,
    append_tool_call_part,
    append_tool_result_part,
    create_message,
    get_message_text,
    trim_messages_by_compaction_checkpoint,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore, normalize_history_prefix


def _tool_result_content(message):
    for part in message["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        return str(output.get("output", ""))
    return ""


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

def test_compaction_summary_should_build_checkpoint_pair(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "summary_trigger_threshold": 1
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed", finish_reason="stop")
        append_text_part(assistant, "压缩后的摘要")
        return assistant

    monkeypatch.setattr(compaction_module, "create_chat_completion", fake_chat)

    system_message = create_message("system", "s_compaction", status="completed")
    append_text_part(system_message, "system")
    user_message = create_message("user", "s_compaction", status="completed")
    append_text_part(user_message, "原始上下文")

    try:
        compacted = compaction_module.compaction_summary([system_message, user_message])
    finally:
        clear_runtime_settings_cache()

    assert len(compacted) == 3
    assert get_message_text(compacted[1]) == "以下历史消息已完成压缩总结，请结合下一条摘要继续当前任务。\n以下是历史对话摘要请求，请参考下一条 summary assistant。"
    assert compacted[2]["info"]["summary"] is True
    assert compacted[2]["info"]["parent_id"] == compacted[1]["info"]["message_id"]
    assert get_message_text(compacted[2]) == "压缩后的摘要"


def test_prune_should_skip_when_tool_result_prune_disabled():
    session_id = "s_prune_disabled"
    tool_1 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_1, tool_call_id="call_1", name="read_file", content="a" * 120)
    tool_2 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_2, tool_call_id="call_2", name="read_file", content="b" * 120)

    messages = [tool_1, tool_2]
    pruned = compaction_module.prune(
        messages,
        settings=CompactionSettings(tool_result_prune_enabled=False, tool_result_keep_recent=0),
    )

    assert _tool_result_content(pruned[0]) == "a" * 120
    assert _tool_result_content(pruned[1]) == "b" * 120


def test_prune_should_keep_latest_tool_messages_by_config():
    session_id = "s_prune_keep_recent"
    tool_1 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_1, tool_call_id="call_1", name="read_file", content="a" * 120)
    tool_2 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_2, tool_call_id="call_2", name="read_file", content="b" * 120)
    tool_3 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_3, tool_call_id="call_3", name="read_file", content="c" * 120)

    pruned = compaction_module.prune(
        [tool_1, tool_2, tool_3],
        settings=CompactionSettings(tool_result_prune_enabled=True, tool_result_keep_recent=1),
    )

    assert _tool_result_content(pruned[0]) == "[Old tool result content cleared]"
    assert _tool_result_content(pruned[1]) == "[Old tool result content cleared]"
    assert _tool_result_content(pruned[2]) == "c" * 120


def test_prune_should_support_keep_recent_zero():
    session_id = "s_prune_zero"
    tool_1 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_1, tool_call_id="call_1", name="read_file", content="a" * 120)
    tool_2 = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_2, tool_call_id="call_2", name="read_file", content="b" * 120)

    pruned = compaction_module.prune(
        [tool_1, tool_2],
        settings=CompactionSettings(tool_result_prune_enabled=True, tool_result_keep_recent=0),
    )

    assert _tool_result_content(pruned[0]) == "[Old tool result content cleared]"
    assert _tool_result_content(pruned[1]) == "[Old tool result content cleared]"


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


def test_inmemory_session_memory_store_should_trim_by_max_messages():
    session_id = "s_memory_trim"
    store = InMemorySessionMemoryStore(max_messages=2)

    first_user = create_message("user", session_id, status="completed")
    append_text_part(first_user, "第一问")
    second_user = create_message("user", session_id, status="completed")
    append_text_part(second_user, "第二问")
    third_user = create_message("user", session_id, status="completed")
    append_text_part(third_user, "第三问")

    store.save(session_id, [first_user, second_user, third_user])
    loaded = store.load(session_id)

    assert [get_message_text(message) for message in loaded] == ["第二问", "第三问"]


def test_inmemory_session_memory_store_should_not_trim_when_disabled():
    session_id = "s_memory_no_trim"
    store = InMemorySessionMemoryStore(max_messages=2, trim_enabled=False)

    first_user = create_message("user", session_id, status="completed")
    append_text_part(first_user, "第一问")
    second_user = create_message("user", session_id, status="completed")
    append_text_part(second_user, "第二问")
    third_user = create_message("user", session_id, status="completed")
    append_text_part(third_user, "第三问")

    store.save(session_id, [first_user, second_user, third_user])
    loaded = store.load(session_id)

    assert [get_message_text(message) for message in loaded] == ["第一问", "第二问", "第三问"]


def test_normalize_history_prefix_should_prepend_synthetic_user_for_tool_prefix():
    session_id = "s_prefix_tool"
    tool_message = create_message("tool", session_id, status="completed")
    append_tool_result_part(tool_message, tool_call_id="call_1", name="read_file", content="hello")

    normalized = normalize_history_prefix([tool_message])

    assert [message["info"]["role"] for message in normalized] == ["user", "tool"]
    assert "系统恢复提示" in get_message_text(normalized[0])


def test_normalize_history_prefix_should_prepend_synthetic_user_for_assistant_tool_calls_prefix():
    session_id = "s_prefix_assistant"
    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="tool_calls")
    append_tool_call_part(assistant_message, tool_call_id="call_1", name="read_file", arguments='{"path":"a.txt"}')

    normalized = normalize_history_prefix([assistant_message])

    assert [message["info"]["role"] for message in normalized] == ["user", "assistant"]
    assert "系统恢复提示" in get_message_text(normalized[0])
