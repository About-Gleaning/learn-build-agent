from pathlib import Path

from agent.runtime.session import clear_session_memory, configure_session_memory_store, run_session
from agent.runtime.session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from agent.core.message import (
    append_text_part,
    append_tool_call_part,
    create_message,
    get_message_text,
)


def _last_tool_result_content(messages):
    for part in messages[-1]["parts"]:
        if part.get("type") == "tool_result":
            return str(part.get("content", ""))
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

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试", session_id="s_test")

    assert result["info"]["role"] == "assistant"
    assert get_message_text(result) == "最终答案"


def test_run_session_end_on_failed_message(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="failed")
        append_text_part(assistant, "Error: 模型调用失败")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试失败", session_id="s_fail")

    assert result["info"]["status"] == "failed"
    assert "模型调用失败" in get_message_text(result)


def test_plan_enter_should_interrupt_on_confirmation_required(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096):
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

    def fake_chat(messages, tools, max_tokens=4096):
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
            system_text = get_message_text(messages[0])
            last_agent = _last_user_agent(messages)
            final_text = "ok" if ("planning agent" in system_text and last_agent == "plan") else "bad"
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

    def fake_chat(messages, tools, max_tokens=4096):
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

    def fake_chat(messages, tools, max_tokens=4096):
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

    def fake_chat(messages, tools, max_tokens=4096):
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

    def fake_chat(messages, tools, max_tokens=4096):
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


def test_run_session_should_use_memory_between_calls(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_memory")
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096):
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

        def fake_chat(messages, tools, max_tokens=4096):
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
