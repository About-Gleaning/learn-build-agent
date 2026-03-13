from pathlib import Path

import agent.runtime.session as session_module
from agent.tools.handlers import run_read
from agent.tools.specs import build_task_tool
from agent.runtime.session import (
    build_system_prompt,
    clear_session_memory,
    configure_session_memory_store,
    run_session,
    run_session_stream_events,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from agent.core.message import (
    append_text_part,
    append_tool_call_part,
    create_message,
    get_message_text,
)


def _last_tool_result_content(messages):
    for part in messages[-1]["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        return str(output.get("output", ""))
    return ""


def _last_user_agent(messages):
    for msg in reversed(messages):
        if msg["info"].get("role") != "user":
            continue
        for part in msg["parts"]:
            if part.get("type") != "text":
                continue
            meta = part.get("meta") or {}
            if isinstance(meta, dict) and "agent" in meta:
                return str(meta["agent"])
    return ""


def _last_tool_result_metadata(messages):
    for part in messages[-1]["parts"]:
        if part.get("type") != "tool":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        metadata = output.get("metadata")
        if isinstance(metadata, dict):
            return metadata
    return {}


def _tool_names(tools):
    return [tool["function"]["name"] for tool in tools]


def test_run_session_with_tool_call(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
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

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试", session_id="s_test")

    assert result["info"]["role"] == "assistant"
    assert get_message_text(result) == "最终答案"
    assert result["info"]["agent"] == "build"
    assert "turn_started_at" in result["info"]
    assert "turn_completed_at" in result["info"]


def test_run_session_end_on_failed_message(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="failed")
        append_text_part(assistant, "Error: 模型调用失败")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试失败", session_id="s_fail")

    assert result["info"]["status"] == "failed"
    assert "模型调用失败" in get_message_text(result)


def test_plan_enter_should_interrupt_on_confirmation_required(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_tool_call_part(
            assistant,
            tool_call_id="call_plan_enter",
            name="plan_enter",
            arguments="{}",
        )
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("切到 plan", session_id="s_plan_confirm")
    assert result["info"]["status"] == "interrupted"
    text = get_message_text(result)
    assert "请确认是否切换到 plan 模式" in text


def test_plan_enter_confirmed_should_switch_by_synthetic_user_message(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")

        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_plan_enter_yes",
                name="plan_enter",
                arguments='{"confirmed": true}',
            )
        else:
            last_agent = _last_user_agent(messages)
            provider = str(messages[-1]["info"].get("provider", ""))
            final_text = "ok" if (last_agent == "plan" and provider) else "bad"
            append_text_part(assistant, final_text)
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("进入 plan", session_id="s_plan_yes")
    assert get_message_text(result) == "ok"


def test_plan_exit_confirmed_should_append_plan_path_when_file_exists(monkeypatch, tmp_path):
    placeholder = tmp_path / "s_plan_exit_yes.md"
    placeholder.write_text("# plan")

    monkeypatch.setattr("agent.runtime.session.build_plan_placeholder_path", lambda _sid: placeholder)

    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")

        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_plan_exit_yes",
                name="plan_exit",
                arguments='{"confirmed": true}',
            )
        else:
            last_user_text = get_message_text(messages[-1])
            append_text_part(assistant, "ok" if str(placeholder) in last_user_text else "bad")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("退出 plan", session_id="s_plan_exit_yes", mode="plan")
    assert get_message_text(result) == "ok"


def test_plan_mode_write_should_be_limited_to_src_plan(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_write",
                name="write_file",
                arguments='{"path":"src/main.py","content":"x"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("在 plan 模式写文件", session_id="s_plan_write", mode="plan")
    assert "仅允许写入 src/plan" in get_message_text(result)


def test_plan_mode_bash_should_block_redirection(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_bash",
                name="bash",
                arguments='{"command":"echo hello > /tmp/a.txt"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("在 plan 模式执行 bash", session_id="s_plan_bash", mode="plan")
    assert "仅允许单条只读命令" in get_message_text(result)


def test_task_with_unknown_subagent_should_return_error(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task",
                name="task",
                arguments='{"prompt":"测试","agent":"unknown"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("调用未知子代理", session_id="s_task_unknown")
    assert "Unknown subagent" in get_message_text(result)


def test_task_tool_description_should_include_registered_subagents():
    task_tool = build_task_tool()
    function_spec = task_tool["function"]

    assert "explore" in function_spec["description"]
    assert "上下文探索" in function_spec["description"]
    assert function_spec["parameters"]["properties"]["agent"]["enum"] == ["explore"]


def test_task_with_primary_agent_should_return_error(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_primary",
                name="task",
                arguments='{"prompt":"测试","agent":"build"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("调用主代理", session_id="s_task_primary")

    assert "不是 subagent" in get_message_text(result)


def test_run_session_should_expose_webfetch_to_build_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "webfetch" in _tool_names(tools)
        assistant = create_message("assistant", "s_build_webfetch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("检查 build agent 工具", session_id="s_build_webfetch")

    assert get_message_text(result) == "ok"


def test_run_session_should_expose_webfetch_to_plan_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "webfetch" in _tool_names(tools)
        assistant = create_message("assistant", "s_plan_webfetch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("检查 plan agent 工具", session_id="s_plan_webfetch", mode="plan")

    assert get_message_text(result) == "ok"


def test_subagent_loop_should_expose_webfetch_to_explore_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "webfetch" in _tool_names(tools)
        assistant = create_message("assistant", "s_explore_webfetch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = session_module.subagent_loop("检查 explore agent 工具", session_id="s_explore_webfetch")

    assert result == "ok"


def test_run_session_should_execute_webfetch_tool(monkeypatch):
    call_state = {"count": 0}

    def fake_webfetch(params):
        return {
            "output": f"抓取成功:{params['url']}",
            "metadata": {"status": "completed"},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_webfetch",
                name="webfetch",
                arguments='{"url":"http://example.com","format":"text"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.webfetch", fake_webfetch)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行 webfetch", session_id="s_run_webfetch")

    assert "抓取成功:http://example.com" in get_message_text(result)


def test_run_session_should_expose_websearch_to_build_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "websearch" in _tool_names(tools)
        assistant = create_message("assistant", "s_build_websearch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("检查 build agent websearch 工具", session_id="s_build_websearch")

    assert get_message_text(result) == "ok"


def test_run_session_should_expose_websearch_to_plan_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "websearch" in _tool_names(tools)
        assistant = create_message("assistant", "s_plan_websearch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("检查 plan agent websearch 工具", session_id="s_plan_websearch", mode="plan")

    assert get_message_text(result) == "ok"


def test_subagent_loop_should_expose_websearch_to_explore_agent(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del messages, max_tokens, hooks, llm_config
        assert "websearch" in _tool_names(tools)
        assistant = create_message("assistant", "s_explore_websearch", status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = session_module.subagent_loop("检查 explore agent websearch 工具", session_id="s_explore_websearch")

    assert result == "ok"


def test_run_session_should_execute_websearch_tool(monkeypatch):
    call_state = {"count": 0}

    def fake_websearch(params):
        return {
            "output": f"搜索成功:{params['query']}",
            "metadata": {"status": "completed"},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_websearch",
                name="websearch",
                arguments='{"query":"python agent","type":"fast"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.websearch", fake_websearch)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行 websearch", session_id="s_run_websearch")

    assert "搜索成功:python agent" in get_message_text(result)


def test_run_session_should_truncate_tool_output_with_task_guidance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    call_state = {"count": 0}
    long_output = "\n".join(f"line {i}" for i in range(2505))

    def fake_webfetch(params):
        del params
        return {
            "output": long_output,
            "metadata": {"status": "completed"},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del tools, max_tokens, hooks, llm_config
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_webfetch_long",
                name="webfetch",
                arguments='{"url":"http://example.com","format":"text"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.webfetch", fake_webfetch)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行长输出 webfetch", session_id="s_long_task")

    text = get_message_text(result)
    assert "Task 工具委托 explore agent" in text
    assert "read_file 配合 offset/limit" in text
    assert "src/storage/tool-output" in text


def test_subagent_loop_should_truncate_tool_output_without_task_guidance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    call_state = {"count": 0}
    long_output = "\n".join(f"line {i}" for i in range(2505))

    def fake_webfetch(params):
        del params
        return {
            "output": long_output,
            "metadata": {"status": "completed"},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del tools, max_tokens, hooks, llm_config
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_webfetch_long_explore",
                name="webfetch",
                arguments='{"url":"http://example.com","format":"text"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.webfetch", fake_webfetch)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = session_module.subagent_loop("执行长输出 webfetch", session_id="s_long_no_task")

    assert "bash + rg" in result
    assert "read_file 配合 offset/limit" in result
    assert "Task 工具委托 explore agent" not in result


def test_run_session_should_store_truncation_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    call_state = {"count": 0}
    long_output = "\n".join(f"line {i}" for i in range(2505))
    seen_metadata: dict[str, object] = {}

    def fake_webfetch(params):
        del params
        return {
            "output": long_output,
            "metadata": {"status": "completed"},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        del tools, max_tokens, hooks, llm_config
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_webfetch_metadata",
                name="webfetch",
                arguments='{"url":"http://example.com","format":"text"}',
            )
        else:
            seen_metadata.update(_last_tool_result_metadata(messages))
            append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.webfetch", fake_webfetch)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行长输出 webfetch", session_id="s_long_metadata")

    assert get_message_text(result) == "ok"
    assert seen_metadata["truncated"] is True
    assert str(seen_metadata["full_output_path"]).endswith("src/storage/tool-output/s_long_metadata/webfetch-call_webfetch_metadata.log")


def test_run_read_should_support_offset_and_limit(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    monkeypatch.setattr("agent.tools.handlers.WORKDIR", tmp_path)
    content = run_read("sample.txt", limit=2, offset=1)

    assert content == "b\nc\n... (1 more lines)"


def test_run_session_should_use_memory_between_calls(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_memory")
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")

        if call_state["count"] == 1:
            append_text_part(assistant, "第一轮回答")
        else:
            has_history_answer = any(
                msg["info"].get("role") == "assistant" and get_message_text(msg) == "第一轮回答"
                for msg in messages
            )
            append_text_part(assistant, "ok" if has_history_answer else "bad")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("第一轮问题", session_id="s_memory")
    result = run_session("第二轮问题", session_id="s_memory")

    assert get_message_text(result) == "ok"


def test_run_session_should_use_configured_memory_store(monkeypatch):
    class StubMemoryStore(SessionMemoryStore):
        def __init__(self) -> None:
            self.saved = False
            self._history: list = []

        def load(self, session_id: str):
            return self._history

        def save(self, session_id: str, messages):
            self.saved = True
            self._history = [msg for msg in messages if msg["info"].get("role") != "system"][-2:]

        def clear(self, session_id: str | None = None):
            self._history = []

    store = StubMemoryStore()
    configure_session_memory_store(store)
    try:
        call_state = {"count": 0}

        def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
            session_id = messages[-1]["info"]["session_id"]
            call_state["count"] += 1
            assistant = create_message("assistant", session_id, status="completed")
            if call_state["count"] == 1:
                append_text_part(assistant, "stub-1")
            else:
                has_memory = any(get_message_text(msg) == "stub-1" for msg in messages if msg["info"]["role"] == "assistant")
                append_text_part(assistant, "ok" if has_memory else "bad")
            return assistant

        monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

        run_session("第一问", session_id="s_stub")
        result = run_session("第二问", session_id="s_stub")

        assert store.saved is True
        assert get_message_text(result) == "ok"
    finally:
        configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))


def test_run_session_stream_events_should_emit_text_delta_and_done(monkeypatch):
    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        yield {"type": "text_delta", "delta": "流式"}
        yield {"type": "text_delta", "delta": "回答"}
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "流式回答")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("你好", session_id="s_stream_1"))
    event_names = [event["type"] for event in events]
    text = "".join(event.get("delta", "") for event in events if event["type"] == "text_delta")

    assert "start" in event_names
    assert "round_start" in event_names
    assert "round_end" in event_names
    assert "done" in event_names
    assert text == "流式回答"


def test_run_session_stream_events_should_emit_tool_events(monkeypatch):
    call_state = {"count": 0}

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(assistant, tool_call_id="call_1", name="todo_read", arguments="{}")
        else:
            append_text_part(assistant, "ok")
        return assistant
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("测试工具", session_id="s_stream_2"))
    event_names = [event["type"] for event in events]

    assert "tool_call" in event_names
    assert "tool_result" in event_names
    assert event_names[-1] == "done"


def test_run_session_should_remember_explicit_provider(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_provider_memory")
    seen: list[tuple[str, str]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen.append((llm_config.provider, llm_config.model))
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("第一轮", session_id="s_provider_memory", provider="gpt", provider_specified=True)
    run_session("第二轮", session_id="s_provider_memory")

    assert seen[0][0] == "gpt"
    assert seen[1][0] == "gpt"


def test_run_session_should_reset_to_agent_default_provider(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_provider_reset")
    seen: list[str] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen.append(llm_config.provider)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("第一轮", session_id="s_provider_reset", provider="gpt", provider_specified=True)
    run_session("第二轮", session_id="s_provider_reset", mode="plan", provider="", provider_specified=True)

    assert seen[0] == "gpt"
    assert seen[1] == "qwen"


def test_build_system_prompt_should_use_model_specific_prompt(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(agent="build", model="qwen3-max", provider="qwen")

    assert "你是 **爪爪**" in prompt
    assert "- model: qwen3-max" in prompt


def test_build_system_prompt_should_fallback_to_default_prompt(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(agent="build", model="gpt-4.1", provider="gpt")

    assert "Qwen 系列模型" not in prompt
    assert "你是 **爪爪**" in prompt
    assert "- model: gpt-4.1" in prompt


def test_build_system_prompt_should_append_agents_md(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    (tmp_path / "AGENTS.md").write_text("请始终先写测试。", encoding="utf-8")

    prompt = build_system_prompt(agent="plan", model="qwen3-max", provider="qwen")

    assert "请始终先写测试。" in prompt
    assert "以下是当前工作目录下的 AGENTS.md 内容" in prompt
    assert f"- workdir: {tmp_path}" in prompt


def test_build_system_prompt_should_include_git_environment(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (True, "/tmp/repo"))
    prompt = build_system_prompt(agent="explore", model="gemini-2.0-flash", provider="gemini")

    assert "- is_git_repo: true" in prompt
    assert "- git_root: /tmp/repo" in prompt
    assert "- provider: gemini" in prompt


def test_run_session_should_refresh_system_prompt_when_mode_changes(monkeypatch):
    seen_system_prompts: list[str] = []
    call_state = {"count": 0}

    def fake_prompt(agent: str, model: str, provider: str) -> str:
        return f"PROMPT::{agent}::{provider}::{model}"

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen_system_prompts.append(get_message_text(messages[0]))
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_plan_enter_yes",
                name="plan_enter",
                arguments='{"confirmed": true}',
            )
        else:
            append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr(session_module, "build_system_prompt", fake_prompt)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("进入 plan", session_id="s_prompt_mode_switch")

    assert get_message_text(result) == "ok"
    assert seen_system_prompts[0] == "PROMPT::build::qwen::qwen3-max"
    assert seen_system_prompts[-1] == "PROMPT::plan::qwen::qwen3-max"


def test_run_session_stream_events_should_use_file_prompt_builder(monkeypatch):
    seen_system_prompts: list[str] = []

    def fake_prompt(agent: str, model: str, provider: str) -> str:
        return f"STREAM::{agent}::{provider}::{model}"

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen_system_prompts.append(get_message_text(messages[0]))
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "流式回答")
        return assistant
        yield  # pragma: no cover

    monkeypatch.setattr(session_module, "build_system_prompt", fake_prompt)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("你好", session_id="s_stream_prompt"))

    assert events[-1]["type"] == "done"
    assert seen_system_prompts == ["STREAM::build::qwen::qwen3-max"]
