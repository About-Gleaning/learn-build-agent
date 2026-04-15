import json

import pytest

import agent.runtime.session as session_module
from agent.core.message import append_text_part, append_tool_call_part, create_message, get_message_text
from agent.runtime.conversation import ConversationMessage, ConversationPersistenceError, Session
from agent.runtime.conversation_hooks import LoopJsonlPersistenceHook
from agent.runtime.loop_hooks import LoopPersistenceHook
from agent.runtime.session import configure_session_memory_store, run_session
from agent.runtime.session_memory import FileSessionMemoryStore
from agent.runtime.workspace import configure_workspace, get_workspace


def _jsonl_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_session_push_user_text_should_create_jsonl_and_load(tmp_path):
    path = tmp_path / "s_1.jsonl"
    session = Session.new("s_1").with_persistence_path(path)

    session.push_user_text("你好")

    loaded = Session.load_from_path(path)
    assert loaded.session_id == "s_1"
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].blocks == [{"type": "text", "text": "你好"}]


def test_assistant_text_message_should_persist_and_restore(tmp_path):
    path = tmp_path / "s_assistant.jsonl"
    session = Session.new("s_assistant").with_persistence_path(path)

    session.push_message(ConversationMessage.assistant([{"type": "text", "text": "完成"}]))

    loaded = Session.load_from_path(path)
    assert loaded.messages[0].role == "assistant"
    assert loaded.messages[0].blocks[0]["text"] == "完成"


def test_assistant_tool_use_message_should_persist_and_restore(tmp_path):
    path = tmp_path / "s_tool_use.jsonl"
    session = Session.new("s_tool_use").with_persistence_path(path)

    session.push_message(
        ConversationMessage.assistant(
            [
                {"type": "tool_use", "id": "call_1", "name": "bash", "input": '{"command":"pwd"}'},
            ]
        )
    )

    loaded = Session.load_from_path(path)
    block = loaded.messages[0].blocks[0]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_1"
    assert block["name"] == "bash"


def test_tool_result_success_and_failure_should_persist_and_restore(tmp_path):
    path = tmp_path / "s_tool_result.jsonl"
    session = Session.new("s_tool_result").with_persistence_path(path)

    session.push_message(ConversationMessage.tool_result("call_1", "bash", "ok", False))
    session.push_message(ConversationMessage.tool_result("call_2", "bash", "failed", True))

    loaded = Session.load_from_path(path)
    assert loaded.messages[0].blocks[0]["is_error"] is False
    assert loaded.messages[1].blocks[0]["is_error"] is True


def test_push_message_should_rollback_memory_when_append_failed(tmp_path):
    bad_path = tmp_path / "as_dir.jsonl"
    bad_path.mkdir()
    session = Session.new("s_bad").with_persistence_path(bad_path)
    old_updated_at_ms = session.updated_at_ms

    with pytest.raises(Exception):
        session.push_message(ConversationMessage.user_text("不会成功"))

    assert session.messages == []
    assert session.updated_at_ms == old_updated_at_ms


def test_save_to_path_should_write_session_meta_and_messages(tmp_path):
    path = tmp_path / "nested" / "s_save.jsonl"
    session = Session.new("s_save")
    session.messages.append(ConversationMessage.user_text("第一条"))

    session.save_to_path(path)

    records = _jsonl_records(path)
    assert records[0]["type"] == "session_meta"
    assert records[1]["type"] == "message"
    assert records[1]["message"]["role"] == "user"


def test_compaction_snapshot_should_rewrite_main_file(tmp_path):
    path = tmp_path / "s_compact.jsonl"
    session = Session.new("s_compact").with_persistence_path(path)
    session.push_message(ConversationMessage.user_text("旧消息 1"))
    session.push_message(ConversationMessage.assistant([{"type": "text", "text": "旧回答 1"}]))
    original_line_count = len(_jsonl_records(path))

    session.messages = [
        ConversationMessage.system_text("压缩摘要"),
        ConversationMessage.user_text("最近消息"),
    ]
    session.record_compaction("压缩摘要", removed_message_count=2)
    session.save_to_path(path)

    records = _jsonl_records(path)
    assert len(records) < original_line_count + 3
    assert records[1]["type"] == "compaction"
    assert records[2]["message"]["role"] == "system"
    assert records[2]["message"]["blocks"][0]["text"] == "压缩摘要"


def test_invalid_jsonl_should_raise_clear_error(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"type":"session_meta","version":1,"session_id":"s","created_at_ms":1,"updated_at_ms":1}\n{bad', encoding="utf-8")

    with pytest.raises(ConversationPersistenceError) as exc:
        Session.load_from_path(path)

    assert "非法 JSON" in str(exc.value)
    assert "broken.jsonl:2" in str(exc.value)


def test_missing_message_fields_should_raise_clear_error(tmp_path):
    path = tmp_path / "missing.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session_meta","version":1,"session_id":"s","created_at_ms":1,"updated_at_ms":1}',
                '{"type":"message","message":{"role":"user"}}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConversationPersistenceError) as exc:
        Session.load_from_path(path)

    assert "message.blocks" in str(exc.value)


def test_loop_jsonl_hook_should_not_append_assistant_after_snapshot_save(tmp_path):
    session_id = "s_no_duplicate_assistant"
    store = FileSessionMemoryStore(base_dir=tmp_path, max_messages=24)
    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "你好")
    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="stop")
    append_text_part(assistant_message, "完成")
    messages = [user_message, assistant_message]

    def save_messages():
        store.save(session_id, messages)

    def append_message(message):
        store.append(session_id, message)

    ctx = {
        "session_id": session_id,
        "assistant_message": assistant_message,
        "process_items": [],
        "display_parts": [],
        "turn_started_at": "",
        "turn_completed_at": "",
        "save_enabled": True,
        "save_callback": save_messages,
        "append_callback": append_message,
        "messages_ref": messages,
    }

    LoopPersistenceHook().after_loop(ctx)
    LoopJsonlPersistenceHook().after_loop(ctx)

    records = _jsonl_records(tmp_path / "s_no_duplicate_assistant.jsonl")
    message_records = [record for record in records if record["type"] == "message"]
    assert [record["message"]["role"] for record in message_records] == ["user", "assistant"]
    assert [record["message"]["meta"]["message_id"] for record in message_records].count(assistant_message["info"]["message_id"]) == 1


def test_persisted_meta_should_normalize_completed_message_status(tmp_path):
    session_id = "s_status_normalized"
    store = FileSessionMemoryStore(base_dir=tmp_path, max_messages=24)

    user_message = create_message("user", session_id)
    append_text_part(user_message, "用户消息")
    assistant_message = create_message("assistant", session_id, status="running")
    append_text_part(assistant_message, "助手回复")
    tool_message = create_message("tool", session_id)
    append_text_part(tool_message, "工具结果")
    failed_message = create_message("assistant", session_id, status="failed")
    append_text_part(failed_message, "失败回复")

    store.save(session_id, [user_message, assistant_message, tool_message, failed_message])

    records = _jsonl_records(tmp_path / "s_status_normalized.jsonl")
    statuses = [record["message"]["meta"]["status"] for record in records if record["type"] == "message"]
    assert statuses == ["completed", "completed", "completed", "failed"]


def test_run_session_should_persist_message_level_jsonl(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    configure_session_memory_store(FileSessionMemoryStore(max_messages=24))
    call_state = {"count": 0}
    monkeypatch.setattr(session_module, "list_mcp_tools", lambda mode=None: ([], []))
    monkeypatch.setattr(session_module, "describe_mcp_runtime_alerts_for_mode", lambda mode=None: [])

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(assistant, tool_call_id="call_1", name="todo_read", arguments="{}")
        else:
            append_text_part(assistant, "最终答案")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("测试", session_id="s_jsonl_integration")

    assert get_message_text(result) == "最终答案"
    path = get_workspace().sessions_dir / "s_jsonl_integration.jsonl"
    records = _jsonl_records(path)
    message_records = [record for record in records if record["type"] == "message"]
    assert [record["message"]["role"] for record in message_records] == ["user", "assistant", "tool", "assistant"]
    assert message_records[1]["message"]["blocks"][0]["type"] == "tool_use"
    assert message_records[2]["message"]["blocks"][0]["type"] == "tool_result"
    assistant_meta = message_records[1]["message"]["meta"]
    assert "response_meta" not in assistant_meta
    assert "process_items" not in assistant_meta
    assert "display_parts" not in assistant_meta
    assert assistant_meta["round_count"] == 1
    assert assistant_meta["tool_call_count"] == 1
    assert assistant_meta["tool_names"] == ["todo_read"]
    assert {record["type"] for record in records} <= {"session_meta", "message", "compaction"}
