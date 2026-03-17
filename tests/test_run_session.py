import agent.runtime.session as session_module
import agent.runtime.compaction as compaction_module
from agent.config.settings import clear_runtime_settings_cache, resolve_llm_config
from agent.tools.handlers import run_read
from agent.tools.specs import build_base_tools, build_task_tool
from agent.runtime.session import (
    build_system_prompt,
    clear_session_memory,
    configure_session_memory_store,
    run_session,
    run_mode_switch_stream_events,
    run_session_stream_events,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from agent.core.message import (
    append_text_part,
    append_tool_call_part,
    create_error_message,
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
    assert "等待用户确认是否切换到 plan 模式" in text
    assert result["info"]["confirmation"]["target_agent"] == "plan"
    assert session_module.get_pending_mode_switch("s_plan_confirm") is not None


def test_plan_enter_confirmed_should_switch_by_program_control(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
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
            last_agent = _last_user_agent(messages)
            provider = str(messages[-1]["info"].get("provider", ""))
            final_text = "ok" if (last_agent == "plan" and provider and "用户已确认切换到 plan 模式" in get_message_text(messages[-1])) else "bad"
            append_text_part(assistant, final_text)
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    first_result = run_session("进入 plan", session_id="s_plan_yes")
    assert first_result["info"]["finish_reason"] == "confirmation_required"
    result = session_module.apply_mode_switch_action("s_plan_yes", "confirm")
    assert get_message_text(result) == "ok"
    assert session_module.get_pending_mode_switch("s_plan_yes") is None


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
                tool_call_id="call_plan_exit",
                name="plan_exit",
                arguments="{}",
            )
        else:
            last_user_text = get_message_text(messages[-1])
            append_text_part(
                assistant,
                "ok" if ("用户已确认计划已完成" in last_user_text and str(placeholder) in last_user_text) else "bad",
            )
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    first_result = run_session("退出 plan", session_id="s_plan_exit_yes", mode="plan")
    assert first_result["info"]["finish_reason"] == "confirmation_required"
    result = session_module.apply_mode_switch_action("s_plan_exit_yes", "confirm")
    assert get_message_text(result) == "ok"


def test_plan_enter_confirmed_should_continue_with_stream_events(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_tool_call_part(
            assistant,
            tool_call_id="call_plan_enter_stream",
            name="plan_enter",
            arguments="{}",
        )
        return assistant

    def fake_stream_chat(messages, tools, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1

        def _generator():
            yield {"type": "text_delta", "delta": "进入 plan 流式执行"}
            assistant = create_message("assistant", session_id, status="completed")
            append_text_part(assistant, "进入 plan 流式执行")
            return assistant

        return _generator()

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream_chat)
    first_result = run_session("进入 plan", session_id="s_plan_stream_confirm")
    assert first_result["info"]["finish_reason"] == "confirmation_required"

    events = list(run_mode_switch_stream_events("s_plan_stream_confirm", "confirm"))

    assert any(event["type"] == "start" for event in events)
    assert any(event["type"] == "text_delta" for event in events)
    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["agent"] == "plan"
    assert done_event["status"] == "completed"
    assert call_state["count"] == 1
    assert session_module.get_pending_mode_switch("s_plan_stream_confirm") is None


def test_mode_switch_cancel_should_be_program_controlled(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_tool_call_part(
            assistant,
            tool_call_id="call_plan_enter_cancel",
            name="plan_enter",
            arguments="{}",
        )
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    first_result = run_session("进入 plan", session_id="s_plan_cancel")
    assert first_result["info"]["finish_reason"] == "confirmation_required"

    cancelled = session_module.apply_mode_switch_action("s_plan_cancel", "cancel")
    assert cancelled["info"]["finish_reason"] == "cancelled"
    assert "已取消切换到 plan 模式" in get_message_text(cancelled)
    assert session_module.get_pending_mode_switch("s_plan_cancel") is None


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
    assert "禁止重定向" in get_message_text(result)


def test_plan_mode_bash_should_allow_readonly_pipe(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_bash_pipe",
                name="bash",
                arguments='{"command":"grep -n \\"build.default.txt\\" README.md | head -5"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("在 plan 模式执行只读管道 bash", session_id="s_plan_bash_pipe", mode="plan")
    assert "build.default.txt" in get_message_text(result)


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


def test_run_session_should_answer_after_task_result(monkeypatch, caplog):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_answer",
                name="task",
                arguments='{"prompt":"检查 hello.py","agent":"explore"}',
            )
        else:
            append_text_part(assistant, f"最终结论：{_last_tool_result_content(messages)}")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr(session_module, "subagent_loop", lambda *args, **kwargs: "项目中没有 hello.py")

    with caplog.at_level("INFO"):
        result = run_session("请帮我查 hello.py", session_id="s_task_followup")

    assert get_message_text(result) == "最终结论：项目中没有 hello.py"


def test_run_session_should_return_error_when_followup_llm_times_out_after_task(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        if call_state["count"] == 1:
            assistant = create_message("assistant", session_id, status="completed")
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_timeout",
                name="task",
                arguments='{"prompt":"检查 hello.py","agent":"explore"}',
            )
            return assistant
        return create_error_message(
            session_id=session_id,
            model="qwen3-max",
            provider="qwen",
            error={"code": "timeout", "message": "request timeout", "details": "TimeoutError"},
        )

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr(session_module, "subagent_loop", lambda *args, **kwargs: "项目中没有 hello.py")

    result = run_session("请帮我查 hello.py", session_id="s_task_followup_timeout")

    assert result["info"]["status"] == "failed"
    assert "request timeout" in get_message_text(result)

def test_task_tool_description_should_include_registered_subagents():
    task_tool = build_task_tool()
    function_spec = task_tool["function"]

    assert "explore" in function_spec["description"]
    assert "上下文探索" in function_spec["description"]
    assert function_spec["parameters"]["properties"]["agent"]["enum"] == ["explore"]


def test_load_skill_tool_description_should_include_available_skills_without_path():
    tools = build_base_tools(
        [
            {
                "name": "python_development_guide",
                "description": "提供 Python 开发规范、测试与性能优化建议。",
                "path": "/tmp/skills/python_development_guide",
            }
        ]
    )
    load_skill_tool = next(tool for tool in tools if tool["function"]["name"] == "load_skill")
    description = load_skill_tool["function"]["description"]

    assert "Skills 提供专门的知识和分步骤的指导。" in description
    assert "<available_skills>" in description
    assert "<name>python_development_guide</name>" in description
    assert "<description>提供 Python 开发规范、测试与性能优化建议。</description>" in description
    assert "/tmp/skills/python_development_guide" not in description


def test_load_skill_tool_description_should_show_empty_message_when_no_skills():
    tools = build_base_tools([])
    load_skill_tool = next(tool for tool in tools if tool["function"]["name"] == "load_skill")

    assert (
        load_skill_tool["function"]["description"]
        == "加载一个 skill，以获取完成某个特定任务的详细指导。目前没有可用的 skills。"
    )


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
    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["display_parts"][0]["kind"] == "assistant_text"
    assert done_event["display_parts"][0]["text"] == "流式回答"


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
    assert all(event.get("event_id") for event in events)
    assert all("depth" in event for event in events)


def test_run_session_stream_events_should_include_subagent_timeline(monkeypatch):
    call_state = {"count": 0}

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")

        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_1",
                name="task",
                arguments='{"agent":"explore","prompt":"子任务"}',
            )
            return assistant

        if call_state["count"] == 2:
            append_tool_call_part(
                assistant,
                tool_call_id="call_sub_1",
                name="todo_read",
                arguments="{}",
            )
            return assistant

        if call_state["count"] == 3:
            append_text_part(assistant, "子代理完成")
            return assistant

        append_text_part(assistant, "主流程完成")
        return assistant
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("测试 task 委派", session_id="s_stream_task"))
    task_tool_call = next(event for event in events if event["type"] == "tool_call" and event["name"] == "task")
    subagent_start = next(event for event in events if event["type"] == "start" and event["agent_kind"] == "subagent")
    subagent_tool_call = next(event for event in events if event["type"] == "tool_call" and event["agent_kind"] == "subagent")
    subagent_done = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "subagent")
    task_tool_result = next(event for event in events if event["type"] == "tool_result" and event["name"] == "task")

    assert task_tool_call["tool_call_id"] == "call_task_1"
    assert subagent_start["parent_tool_call_id"] == "call_task_1"
    assert subagent_tool_call["depth"] == 1
    assert subagent_done["delegation_id"] == task_tool_result["delegation_id"]
    assert task_tool_result["output_preview"] == "子代理完成"
    assert isinstance(subagent_done.get("process_items"), list)


def test_run_session_stream_done_should_include_response_summary(monkeypatch):
    call_state = {"count": 0}

    def fake_stream_with_result(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(assistant, tool_call_id="call_read_1", name="todo_read", arguments="{}")
            return assistant
        append_text_part(assistant, "ok")
        return assistant
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream_with_result)

    events = list(run_session_stream_events("测试 summary", session_id="s_stream_summary"))
    done_event = next(event for event in events if event["type"] == "done")

    assert done_event["response_meta"]["tool_call_count"] == 1
    assert done_event["response_meta"]["round_count"] >= 2
    assert "todo_read" in done_event["response_meta"]["tool_names"]
    assert any(item["kind"] == "tool_call" for item in done_event["process_items"])
    assert [item["kind"] for item in done_event["display_parts"]] == ["tool_call", "tool_result", "assistant_text"]


def test_run_session_stream_done_should_keep_text_and_tool_order(monkeypatch):
    call_state = {"count": 0}

    def fake_stream_with_interleaving(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        if call_state["count"] == 1:
            yield {"type": "text_delta", "delta": "先说明"}
            assistant = create_message("assistant", session_id, status="completed")
            append_text_part(assistant, "先说明")
            append_tool_call_part(assistant, tool_call_id="call_1", name="todo_read", arguments="{}")
            return assistant
        yield {"type": "text_delta", "delta": "再总结"}
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "再总结")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream_with_interleaving)

    events = list(run_session_stream_events("测试交错顺序", session_id="s_stream_interleave"))
    done_event = next(event for event in events if event["type"] == "done")

    assert [item["kind"] for item in done_event["display_parts"]] == ["assistant_text", "tool_call", "tool_result", "assistant_text"]
    assert done_event["display_parts"][0]["text"] == "先说明"
    assert done_event["display_parts"][-1]["text"] == "再总结"


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
    prompt = build_system_prompt(agent="build", model="qwen3-max", provider="qwen", vendor="qwen")

    assert "你是 **爪爪**" in prompt
    assert "- vendor: qwen" in prompt
    assert "- model: qwen3-max" in prompt


def test_build_system_prompt_should_fallback_to_default_prompt(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(agent="build", model="gpt-4.1", provider="gpt", vendor="openai")

    assert "Qwen 系列模型" not in prompt
    assert "你是 **爪爪**" in prompt
    assert "- vendor: openai" in prompt
    assert "- model: gpt-4.1" in prompt


def test_build_system_prompt_should_share_vendor_prompt_for_qwen_coder(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(agent="build", model="qwen3-coder-next", provider="qwen-coder", vendor="qwen")

    assert "你是 **爪爪**" in prompt
    assert "- provider: qwen-coder" in prompt
    assert "- vendor: qwen" in prompt
    assert "- model: qwen3-coder-next" in prompt


def test_build_system_prompt_should_append_agents_md(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    (tmp_path / "AGENTS.md").write_text("请始终先写测试。", encoding="utf-8")

    prompt = build_system_prompt(agent="plan", model="qwen3-max", provider="qwen", vendor="qwen")

    assert "请始终先写测试。" in prompt
    assert "以下是当前工作目录下的 AGENTS.md 内容" in prompt
    assert f"- workdir: {tmp_path}" in prompt


def test_build_system_prompt_should_include_git_environment(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (True, "/tmp/repo"))
    prompt = build_system_prompt(agent="explore", model="gemini-2.0-flash", provider="gemini", vendor="google")

    assert "- is_git_repo: true" in prompt
    assert "- git_root: /tmp/repo" in prompt
    assert "- provider: gemini" in prompt
    assert "- vendor: google" in prompt
    assert "当前可用 skills catalog" not in prompt
    assert "{skills_catalog}" not in prompt
    assert "`load_skill`" in prompt


def test_run_session_should_refresh_system_prompt_when_mode_changes(monkeypatch):
    seen_system_prompts: list[str] = []

    def fake_prompt(agent: str, model: str, provider: str, vendor: str) -> str:
        return f"PROMPT::{agent}::{vendor}::{provider}::{model}"

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen_system_prompts.append(get_message_text(messages[0]))
        assistant = create_message("assistant", session_id, status="completed")
        if len(seen_system_prompts) == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_plan_enter",
                name="plan_enter",
                arguments="{}",
            )
        else:
            append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr(session_module, "build_system_prompt", fake_prompt)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    first_result = run_session("进入 plan", session_id="s_prompt_mode_switch")
    assert first_result["info"]["finish_reason"] == "confirmation_required"
    result = session_module.apply_mode_switch_action("s_prompt_mode_switch", "confirm")

    assert get_message_text(result) == "ok"
    assert seen_system_prompts[0] == "PROMPT::build::qwen::qwen::qwen3-max"
    assert seen_system_prompts[-1] == "PROMPT::plan::qwen::qwen::qwen3-max"


def test_run_session_stream_events_should_use_file_prompt_builder(monkeypatch):
    seen_system_prompts: list[str] = []

    def fake_prompt(agent: str, model: str, provider: str, vendor: str) -> str:
        return f"STREAM::{agent}::{vendor}::{provider}::{model}"

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
    assert seen_system_prompts == ["STREAM::build::qwen::qwen::qwen3-max"]


def test_resolve_llm_config_should_expose_provider_vendor(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "qwen-coder")
        assert config.provider == "qwen-coder"
        assert config.vendor == "qwen"
        assert config.model == "qwen3-coder-next"
        assert config.timeout_seconds == 60
    finally:
        clear_runtime_settings_cache()


def test_get_runtime_settings_should_require_vendor(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "providers": {
            "qwen": {
              "base_url": "https://example.com/v1",
              "model": "qwen3-max",
              "api_key_env": "QWEN_API_KEY"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen"
            },
            "plan": {
              "provider": "qwen"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)

    try:
        resolve_llm_config("build")
        raise AssertionError("期望缺少 vendor 时抛出异常")
    except ValueError as exc:
        assert "vendor" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_compaction_summary_should_log_summary_stages(monkeypatch, caplog):
    system_message = create_message("system", "s_compact_log")
    append_text_part(system_message, "system")
    user_message = create_message("user", "s_compact_log")
    append_text_part(user_message, "x" * (compaction_module.THRESHOLD * 4 + 100))

    def fake_summary_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        assistant = create_message("assistant", "s_compact_log", status="completed")
        append_text_part(assistant, "压缩摘要")
        return assistant

    monkeypatch.setattr(compaction_module, "create_chat_completion", fake_summary_chat)

    with caplog.at_level("INFO"):
        compacted = compaction_module.compaction_summary([system_message, user_message], agent="build")

    assert len(compacted) >= 2
    assert "compaction.check" in caplog.text
    assert "compaction.summary_request" in caplog.text
    assert "compaction.summary_done" in caplog.text
