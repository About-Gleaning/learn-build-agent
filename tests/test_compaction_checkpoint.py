import json
from pathlib import Path
from typing import Any

import agent.runtime.compaction as compaction_module
from agent.adapters.llm.protocols import ChatCompletionsAdapter
from agent.config.settings import CompactionSettings, ResolvedLLMConfig, clear_runtime_settings_cache
from agent.core.message import (
    Message,
    append_compaction_part,
    append_text_part,
    append_tool_call_part,
    append_tool_result_part,
    create_message,
    get_message_text,
    trim_messages_by_compaction_checkpoint,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore, normalize_history_prefix

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _tool_result_content(message):
    for part in message["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        return str(output.get("output", ""))
    return ""


def _build_chat_config() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        agent="build",
        provider="qwen",
        vendor="qwen",
        model="kimi-k2.5",
        max_tokens=32000,
        api_mode="chat_completions",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout_seconds=30,
    )


def _provider_payload_to_runtime_messages(path: Path, *, session_id: str) -> list[Message]:
    """把事故现场的 provider payload 还原成运行时消息，便于复现压缩链路。"""

    provider_messages = json.loads(path.read_text(encoding="utf-8"))
    tool_names_by_call_id: dict[str, str] = {}
    runtime_messages: list[Message] = []

    for provider_message in provider_messages:
        role = str(provider_message.get("role", "")).strip()
        content = str(provider_message.get("content") or "")
        if role in {"system", "user"}:
            message = create_message(role, session_id, status="completed")
            append_text_part(message, content)
            runtime_messages.append(message)
            continue

        if role == "assistant":
            message = create_message("assistant", session_id, status="completed")
            if content:
                append_text_part(message, content)
            for tool_call in provider_message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                tool_call_id = str(tool_call.get("id", "")).strip()
                tool_name = str(function.get("name", "")).strip()
                arguments = str(function.get("arguments") or "{}")
                if not tool_call_id or not tool_name:
                    continue
                tool_names_by_call_id[tool_call_id] = tool_name
                append_tool_call_part(message, tool_call_id=tool_call_id, name=tool_name, arguments=arguments)
            runtime_messages.append(message)
            continue

        if role == "tool":
            tool_call_id = str(provider_message.get("tool_call_id", "")).strip()
            tool_name = tool_names_by_call_id.get(tool_call_id, "unknown")
            message = create_message("tool", session_id, status="completed")
            append_tool_result_part(message, tool_call_id=tool_call_id, name=tool_name, content=content)
            runtime_messages.append(message)

    return runtime_messages


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


def test_compaction_summary_should_compact_session_1_fixture_without_empty_tools(monkeypatch):
    session_id = "s_session_1_fixture"
    messages = _provider_payload_to_runtime_messages(FIXTURES_DIR / "session-1.txt", session_id=session_id)
    seen: dict[str, Any] = {"calls": 0}
    adapter = ChatCompletionsAdapter(_build_chat_config())

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del hooks, llm_config, agent
        seen["calls"] += 1
        seen["max_tokens"] = max_tokens
        seen["tools"] = tools
        request = adapter.build_request(messages, tools=tools)
        seen["request"] = request

        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed", finish_reason="stop")
        append_text_part(assistant, "session-1 已压缩为摘要")
        return assistant

    monkeypatch.setattr(compaction_module, "create_chat_completion", fake_chat)

    compacted = compaction_module.compaction_summary(
        messages,
        llm_config=_build_chat_config(),
        agent="build",
        settings=CompactionSettings(summary_trigger_threshold=40000, summary_max_tokens=12000),
    )

    assert len(messages) == 247
    assert seen["calls"] == 1
    assert seen["max_tokens"] == 12000
    assert seen["tools"] == []
    assert "tools" not in seen["request"]
    assert len(compacted) < 10
    assert any(bool(message.get("info", {}).get("summary")) for message in compacted)
    assert "以下历史消息已完成压缩总结" in get_message_text(compacted[-2])
    assert get_message_text(compacted[-1]) == "session-1 已压缩为摘要"
    assert get_message_text(compacted[-1]) != get_message_text(messages[-1])


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
