import pytest

from agent.core.message import append_text_part, append_tool_call_part, create_message, get_message_text
from agent.runtime.session import clear_session_memory, run_session, run_session_stream_events, subagent_loop
from agent.runtime.workspace import configure_workspace
from agent.runtime.session_hooks import (
    SessionHook,
    SessionLoggingHook,
    clear_global_session_hooks,
    register_global_session_hook,
)


@pytest.fixture(autouse=True)
def reset_global_session_hooks():
    clear_global_session_hooks()
    register_global_session_hook(SessionLoggingHook())
    yield
    clear_global_session_hooks()
    register_global_session_hook(SessionLoggingHook())


@pytest.fixture(autouse=True)
def disable_mcp_tools(monkeypatch):
    import agent.runtime.session as session_module

    monkeypatch.setattr(session_module, "list_mcp_tools", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(session_module, "describe_mcp_runtime_alerts_for_mode", lambda *args, **kwargs: [])


class RecorderSessionHook(SessionHook):
    def __init__(
        self,
        name: str,
        records: list[str],
        *,
        fail_fast: bool = False,
        order: int = 1000,
        agent_kinds: set[str] | None = None,
        agent_names: set[str] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            fail_fast=fail_fast,
            order=order,
            agent_kinds=agent_kinds,
            agent_names=agent_names,
        )
        self.records = records

    def before_session(self, ctx):
        self.records.append(f"{self.name}.before:{ctx.get('agent')}:{ctx.get('agent_kind')}")

    def after_session(self, ctx, message):
        self.records.append(f"{self.name}.after:{ctx.get('agent')}:{ctx.get('agent_kind')}")

    def on_error(self, ctx, error, normalized_error):
        self.records.append(f"{self.name}.error:{normalized_error.get('code', '')}")


class ModeRecorderSessionHook(SessionHook):
    def __init__(self, name: str, records: list[str]) -> None:
        super().__init__(name=name)
        self.records = records

    def before_session(self, ctx):
        self.records.append(f"{self.name}.before:{ctx.get('agent')}:{ctx.get('agent_kind')}:{ctx.get('mode')}")

    def after_session(self, ctx, message):
        self.records.append(f"{self.name}.after:{ctx.get('agent')}:{ctx.get('agent_kind')}:{ctx.get('mode')}")


class BrokenSessionHook(SessionHook):
    def __init__(self, *, fail_fast: bool) -> None:
        super().__init__(name="broken_session", fail_fast=fail_fast)

    def before_session(self, ctx):
        raise RuntimeError("session before failed")


class ErrorCaptureSessionHook(SessionHook):
    def __init__(self, records: list[str]) -> None:
        super().__init__(name="session_error_capture")
        self.records = records

    def on_error(self, ctx, error, normalized_error):
        self.records.append(normalized_error.get("code", ""))


def _mock_chat_with_one_tool_then_text(tool_name: str = "todo_read", arguments: str = "{}"):
    call_state = {"count": 0}

    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_1",
                name=tool_name,
                arguments=arguments,
            )
        else:
            append_text_part(assistant, "done")
        return assistant

    return _fake_chat


def _mock_chat_return_text(content: str = "done"):
    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed", finish_reason="stop")
        append_text_part(assistant, content)
        return assistant

    return _fake_chat


def _mock_stream_return_text(content: str = "done"):
    def _fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed", finish_reason="stop")
        append_text_part(assistant, content)
        yield {"type": "text_delta", "delta": content}
        return assistant

    return _fake_stream


def test_session_hooks_should_sort_before_and_reverse_after(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    clear_global_session_hooks()
    register_global_session_hook(RecorderSessionHook("g2", records, order=200))
    register_global_session_hook(RecorderSessionHook("g1", records, order=100))
    local_hook = RecorderSessionHook("l1", records, order=150)

    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    result = run_session("测试", session_id="s_session_order", session_hooks=[local_hook])

    assert get_message_text(result) == "done"
    assert records == [
        "g1.before:build:primary",
        "l1.before:build:primary",
        "g2.before:build:primary",
        "g2.after:build:primary",
        "l1.after:build:primary",
        "g1.after:build:primary",
    ]


def test_session_hooks_should_keep_stable_order_when_order_equal(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    clear_global_session_hooks()
    register_global_session_hook(RecorderSessionHook("g1", records, order=100))
    register_global_session_hook(RecorderSessionHook("g2", records, order=100))
    local_hook = RecorderSessionHook("l1", records, order=100)

    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    run_session("测试", session_id="s_session_same_order", session_hooks=[local_hook])

    assert records == [
        "g1.before:build:primary",
        "g2.before:build:primary",
        "l1.before:build:primary",
        "l1.after:build:primary",
        "g2.after:build:primary",
        "g1.after:build:primary",
    ]


def test_session_hook_fail_open_should_continue(monkeypatch):
    import agent.runtime.session as session_module

    clear_global_session_hooks()
    register_global_session_hook(BrokenSessionHook(fail_fast=False))

    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    result = run_session("测试", session_id="s_session_open")

    assert get_message_text(result) == "done"


def test_session_hook_fail_fast_should_interrupt(monkeypatch):
    import agent.runtime.session as session_module

    clear_global_session_hooks()
    register_global_session_hook(BrokenSessionHook(fail_fast=True))

    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    with pytest.raises(RuntimeError, match="Hook 'broken_session' failed"):
        run_session("测试", session_id="s_session_fast")


def test_session_hook_should_call_on_error_when_session_raises(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    clear_global_session_hooks()
    register_global_session_hook(ErrorCaptureSessionHook(records))

    def _broken_chat(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(session_module, "create_chat_completion", _broken_chat)

    with pytest.raises(RuntimeError, match="boom"):
        run_session("测试", session_id="s_session_error")

    assert records == ["api_error"]


def test_session_hook_should_run_on_stream_session(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    clear_global_session_hooks()
    local_hook = RecorderSessionHook("stream", records, order=100)

    monkeypatch.setattr(session_module, "create_chat_completion_stream", _mock_stream_return_text())

    events = list(run_session_stream_events("测试流式", session_id="s_session_stream", session_hooks=[local_hook]))

    assert any(event["type"] == "text_delta" for event in events)
    assert records == [
        "stream.before:build:primary",
        "stream.after:build:primary",
    ]


def test_session_hook_should_filter_primary_and_subagent(monkeypatch):
    import agent.runtime.session as session_module

    primary_records: list[str] = []
    subagent_records: list[str] = []
    primary_hook = RecorderSessionHook("primary", primary_records, agent_kinds={"primary"})
    subagent_hook = RecorderSessionHook("subagent", subagent_records, agent_kinds={"subagent"}, agent_names={"explore"})

    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    run_session("主代理", session_id="s_session_primary", session_hooks=[primary_hook, subagent_hook])
    subagent_loop("子代理", session_id="s_session_subagent", session_hooks=[primary_hook, subagent_hook])

    assert primary_records == [
        "primary.before:build:primary",
        "primary.after:build:primary",
    ]
    assert subagent_records == [
        "subagent.before:explore:subagent",
        "subagent.after:explore:subagent",
    ]


def test_session_logging_hook_should_log_start_and_finish(monkeypatch, caplog):
    import agent.runtime.session as session_module

    clear_global_session_hooks()
    register_global_session_hook(SessionLoggingHook())
    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())

    with caplog.at_level("INFO"):
        run_session("记录日志", session_id="s_session_log")

    assert "session.start" in caplog.text
    assert "session.finish" in caplog.text
    assert "session_id=s_session_log" in caplog.text


def test_session_hook_should_propagate_to_subagent_from_task(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    local_hook = RecorderSessionHook("trace", records, agent_names={"build", "explore"})

    call_state = {"build": 0, "explore": 0}

    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config
        session_id = messages[-1]["info"]["session_id"]
        call_state[agent] = call_state.get(agent, 0) + 1
        assistant = create_message("assistant", session_id, status="completed")
        if agent == "build" and call_state[agent] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task",
                name="task",
                arguments='{"prompt":"子代理任务","agent":"explore"}',
            )
        else:
            assistant["info"]["finish_reason"] = "stop"
            append_text_part(assistant, "done")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", _fake_chat)

    result = run_session("执行 task", session_id="s_session_task", session_hooks=[local_hook])

    assert get_message_text(result) == "done"
    assert records == [
        "trace.before:build:primary",
        "trace.before:explore:subagent",
        "trace.after:explore:subagent",
        "trace.after:build:primary",
    ]


def test_session_hook_should_run_on_slash_command_immediate_output(tmp_path):
    configure_workspace(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 已存在\n", encoding="utf-8")

    records: list[str] = []
    clear_global_session_hooks()
    local_hook = ModeRecorderSessionHook("slash", records)

    result = run_session("/init", session_id="s_session_slash_done", session_hooks=[local_hook])

    assert "已存在 `AGENTS.md`" in get_message_text(result)
    assert records == [
        "slash.before:build:primary:build",
        "slash.after:build:primary:build",
    ]


def test_session_hook_should_run_on_slash_command_error_path(monkeypatch):
    import agent.runtime.session as session_module

    records: list[str] = []
    clear_global_session_hooks()
    local_hook = ModeRecorderSessionHook("slash_error", records)

    monkeypatch.setattr(session_module, "resolve_slash_command", lambda _user_input: (_ for _ in ()).throw(ValueError("bad slash")))

    result = run_session("/init", session_id="s_session_slash_error", session_hooks=[local_hook])

    assert get_message_text(result) == "bad slash"
    assert records == [
        "slash_error.before:build:primary:build",
        "slash_error.after:build:primary:build",
    ]


def test_session_hook_should_run_on_stream_slash_command_immediate_output(tmp_path):
    configure_workspace(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 已存在\n", encoding="utf-8")

    records: list[str] = []
    clear_global_session_hooks()
    local_hook = ModeRecorderSessionHook("slash_stream", records)

    events = list(run_session_stream_events("/init", session_id="s_session_slash_stream", session_hooks=[local_hook]))

    assert any(event["type"] == "done" for event in events)
    assert records == [
        "slash_stream.before:build:primary:build",
        "slash_stream.after:build:primary:build",
    ]


def test_session_hook_should_use_resumed_plan_mode(monkeypatch):
    import agent.runtime.session as session_module

    clear_session_memory("s_session_resume_plan")
    monkeypatch.setattr(session_module, "create_chat_completion", _mock_chat_return_text())
    run_session("进入 plan", session_id="s_session_resume_plan", mode="plan")

    records: list[str] = []
    clear_global_session_hooks()
    local_hook = ModeRecorderSessionHook("resume", records)

    result = run_session("继续执行", session_id="s_session_resume_plan", session_hooks=[local_hook])

    assert get_message_text(result) == "done"
    assert records == [
        "resume.before:plan:primary:plan",
        "resume.after:plan:primary:plan",
    ]


def test_session_hook_should_refresh_mode_after_plan_enter_switch(monkeypatch):
    import agent.runtime.session as session_module

    clear_session_memory("s_session_plan_switch")
    records: list[str] = []
    clear_global_session_hooks()
    local_hook = ModeRecorderSessionHook("switch", records)
    call_state = {"count": 0}

    def _fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_plan_enter",
                name="plan_enter",
                arguments="{}",
            )
        else:
            assistant["info"]["finish_reason"] = "stop"
            append_text_part(assistant, "done" if agent == "plan" else "bad")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", _fake_chat)
    monkeypatch.setattr(
        session_module,
        "run_plan_enter",
        lambda **kw: {
            "output": "已切换到 plan 模式",
            "metadata": {
                "status": "switched",
                "synthetic_agent": "plan",
                "synthetic_user_message": "用户已确认切换到 plan 模式。",
                "current_agent": "build",
                "target_agent": "plan",
            },
        },
    )

    result = run_session("进入 plan", session_id="s_session_plan_switch", session_hooks=[local_hook])

    assert get_message_text(result) == "done"
    assert records == [
        "switch.before:build:primary:build",
        "switch.after:plan:primary:plan",
    ]
