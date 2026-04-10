from pathlib import Path

import pytest

import agent.runtime.session as session_module
import agent.runtime.compaction as compaction_module
from agent.config.settings import (
    ResolvedLLMConfig,
    clear_runtime_settings_cache,
    get_project_runtime_settings,
    resolve_agent_loop_settings,
    resolve_compaction_settings,
    resolve_file_extraction_settings,
    resolve_llm_config,
)
from agent.runtime.workspace import build_plan_storage_path, configure_workspace, get_workspace
from agent.tools.file_edit_state import clear_file_edit_states
from agent.tools.handlers import build_plan_placeholder_path
from agent.mcp.runtime import _shutdown_asyncio_thread_runner
from agent.tools.read_file_tool import run_read
from agent.tools.specs import build_base_tools, build_task_tool
from agent.runtime.session import (
    build_system_prompt,
    clear_session_memory,
    configure_session_memory_store,
    generate_session_id,
    get_pending_question,
    request_session_stop,
    run_question_answer_stream_events,
    run_question_reject_stream_events,
    run_session,
    run_mode_switch_stream_events,
    run_session_stream_events,
)
from agent.runtime.session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from agent.core.message import (
    append_reasoning_part,
    append_text_part,
    append_tool_part,
    append_tool_call_part,
    create_error_message,
    create_message,
    get_message_text,
    to_provider_messages,
)
from agent.adapters.llm.vendors import build_provider_adapter


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


def _last_user_text(messages):
    for msg in reversed(messages):
        if msg["info"].get("role") != "user":
            continue
        return get_message_text(msg)
    return ""


def _last_user_display_text(messages):
    for msg in reversed(messages):
        if msg["info"].get("role") != "user":
            continue
        for part in msg["parts"]:
            if part.get("type") != "text":
                continue
            meta = part.get("meta") or {}
            if isinstance(meta, dict) and meta.get("display_text"):
                return str(meta["display_text"])
        return get_message_text(msg)
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


@pytest.fixture(autouse=True)
def _clear_edit_state():
    clear_file_edit_states()
    _shutdown_asyncio_thread_runner()
    yield
    clear_file_edit_states()
    _shutdown_asyncio_thread_runner()


@pytest.fixture(autouse=True)
def _disable_real_mcp_runtime(monkeypatch):
    monkeypatch.setattr(session_module, "list_mcp_tools", lambda mode=None: ([], []))
    monkeypatch.setattr(session_module, "describe_mcp_runtime_alerts_for_mode", lambda mode=None: [])


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


def test_run_session_should_execute_mcp_tool_via_normal_tool_chain(monkeypatch):
    call_state = {"count": 0}
    captured_tool_names: list[str] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        captured_tool_names.append(",".join(_tool_names(tools)))
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_mcp_1",
                name="github__search_issues",
                arguments='{"query":"bug"}',
            )
        else:
            append_text_part(assistant, "MCP 已执行")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)
    monkeypatch.setattr(
        session_module,
        "list_mcp_tools",
        lambda mode=None: (
            [
                {
                    "type": "function",
                    "function": {
                        "name": "github__search_issues",
                        "description": "GitHub 搜索",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        session_module,
        "execute_mcp_tool",
        lambda prefixed_tool_name, arguments: {
            "output": f"{prefixed_tool_name}:{arguments['query']}",
            "metadata": {
                "status": "completed",
                "mcp_server_alias": "github",
                "mcp_tool_name": "search_issues",
            },
        },
    )

    result = run_session("执行 MCP", session_id="s_mcp")

    assert get_message_text(result) == "MCP 已执行"
    assert "github__search_issues" in captured_tool_names[0]


def test_run_session_should_resolve_analyze_slash_command_before_llm(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 已存在\n", encoding="utf-8")
    captured = {"user_text": "", "agent": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config
        captured["user_text"] = _last_user_text(messages)
        captured["agent"] = _last_user_agent(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "已生成说明书")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("/analyze", session_id="s_analyze", mode="plan")

    assert get_message_text(result) == "已生成说明书"
    assert captured["agent"] == "build"
    assert "project-context.md" in captured["user_text"]
    assert "analyze_docs" in captured["user_text"]
    assert "README.md" in captured["user_text"]
    assert "AGENTS.md" in captured["user_text"]
    assert "后续开发主手册" in captured["user_text"]
    assert "必须一并补充到" in captured["user_text"]
    assert "文档分工与优先级" in captured["user_text"]
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_analyze")
    assert _last_user_display_text(history_messages) == "/analyze"


def test_run_session_should_stop_analyze_when_agents_missing(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    called = {"chat": False}

    def fake_chat(*args, **kwargs):
        called["chat"] = True
        raise AssertionError("缺少 AGENTS.md 时不应继续调用 LLM")

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("/analyze", session_id="s_analyze_missing", mode="plan")

    assert "请先执行 `/init`" in get_message_text(result)
    assert called["chat"] is False
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_analyze_missing")
    assert _last_user_display_text(history_messages) == "/analyze"


def test_run_session_should_resolve_init_slash_command_before_llm_when_agents_missing(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    captured = {"user_text": "", "agent": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config
        captured["user_text"] = _last_user_text(messages)
        captured["agent"] = _last_user_agent(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "已生成 AGENTS.md")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("/init", session_id="s_init", mode="plan")

    assert get_message_text(result) == "已生成 AGENTS.md"
    assert captured["agent"] == "build"
    assert "AGENTS.md" in captured["user_text"]
    assert "面向内容贡献者" in captured["user_text"]
    assert "无需再次检查文件是否存在" in captured["user_text"]
    assert "先确认目标文件当前不存在" not in captured["user_text"]
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_init")
    assert _last_user_display_text(history_messages) == "/init"


def test_run_session_should_stop_init_when_agents_already_exists(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 已存在\n", encoding="utf-8")
    called = {"chat": False}

    def fake_chat(*args, **kwargs):
        called["chat"] = True
        raise AssertionError("已有 AGENTS.md 时不应继续调用 LLM")

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("/init", session_id="s_init_exists", mode="plan")

    assert "已存在 `AGENTS.md`" in get_message_text(result)
    assert called["chat"] is False
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_init_exists")
    assert _last_user_display_text(history_messages) == "/init"


def test_run_session_should_forward_unknown_slash_like_input_to_llm(monkeypatch):
    captured = {"user_text": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        captured["user_text"] = _last_user_text(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "已进入 LLM")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("/missing", session_id="s_missing")

    assert get_message_text(result) == "已进入 LLM"
    assert captured["user_text"] == "/missing"
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_missing")
    assert _last_user_text(history_messages) == "/missing"


def test_run_session_should_forward_slash_command_with_extra_text_to_llm(monkeypatch):
    captured = {"user_text": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        captured["user_text"] = _last_user_text(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "按普通输入处理")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    user_input = "/analyze 请顺便补充数据库设计"
    result = run_session(user_input, session_id="s_analyze_with_text")

    assert get_message_text(result) == "按普通输入处理"
    assert captured["user_text"] == user_input
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_analyze_with_text")
    assert _last_user_text(history_messages) == user_input


def test_run_session_should_forward_init_with_extra_text_to_llm(monkeypatch):
    captured = {"user_text": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        captured["user_text"] = _last_user_text(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "按普通输入处理")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    user_input = "/init 请顺便补充 README"
    result = run_session(user_input, session_id="s_init_with_text")

    assert get_message_text(result) == "按普通输入处理"
    assert captured["user_text"] == user_input
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_init_with_text")
    assert _last_user_text(history_messages) == user_input


@pytest.mark.parametrize("user_input", ["/", "/tmp/foo", "/bin/bash -lc echo hi"])
def test_run_session_should_forward_absolute_path_like_input_to_llm(monkeypatch, user_input):
    captured = {"user_text": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        captured["user_text"] = _last_user_text(messages)
        assistant = create_message("assistant", messages[-1]["info"]["session_id"], status="completed")
        append_text_part(assistant, "正常进入 LLM")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session(user_input, session_id=f"s_path_{abs(hash(user_input))}")

    assert get_message_text(result) == "正常进入 LLM"
    assert captured["user_text"] == user_input


def test_run_session_should_block_hidden_mcp_tool_in_plan_mode(monkeypatch):
    call_state = {"count": 0}
    execute_calls: list[tuple[str, dict[str, object], str | None]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_mcp_plan_1",
                name="private_docs__search",
                arguments='{"query":"secret"}',
            )
        else:
            append_text_part(assistant, "已收到拒绝结果")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)
    monkeypatch.setattr(
        session_module,
        "list_mcp_tools",
        lambda mode=None: (
            [
                {
                    "type": "function",
                    "function": {
                        "name": "private_docs__search",
                        "description": "私有文档搜索",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                }
            ],
            [],
        ),
    )

    def fake_execute(prefixed_tool_name, arguments, mode=None):
        execute_calls.append((prefixed_tool_name, arguments, mode))
        return {
            "output": "Error: plan 模式下不允许执行 MCP tool: private_docs__search",
            "metadata": {
                "status": "failed",
                "error": {"code": "mcp_tool_not_allowed_in_plan"},
                "mcp_server_alias": "private_docs",
                "mcp_tool_name": "search",
            },
        }

    monkeypatch.setattr(session_module, "execute_mcp_tool", fake_execute)

    result = run_session("执行私有 MCP", session_id="s_plan_mcp_block", mode="plan")

    assert get_message_text(result) == "已收到拒绝结果"
    assert execute_calls == [("private_docs__search", {"query": "secret"}, "plan")]


def test_run_session_should_replay_reasoning_content_for_tool_followup(monkeypatch):
    call_state = {"count": 0}
    captured_assistant_history: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1

        if call_state["count"] == 2:
            provider_messages = to_provider_messages(messages)
            assistant_history = next(msg for msg in provider_messages if msg["role"] == "assistant")
            captured_assistant_history.append(assistant_history)

        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_reasoning_part(assistant, "先读取待办列表。")
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

    result = run_session("测试 reasoning tool followup", session_id="s_reasoning_followup")

    assert get_message_text(result) == "最终答案"
    assert captured_assistant_history[0]["reasoning_content"] == "先读取待办列表。"
    assert captured_assistant_history[0]["tool_calls"][0]["function"]["name"] == "todo_read"


def test_run_session_should_continue_when_finish_reason_is_unknown(monkeypatch):
    call_state = {"count": 0}
    captured_assistant_history: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1

        if call_state["count"] == 2:
            provider_messages = to_provider_messages(messages)
            assistant_history = next(msg for msg in provider_messages if msg["role"] == "assistant")
            captured_assistant_history.append(assistant_history)

        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            assistant["info"]["finish_reason"] = "unknown"
            append_reasoning_part(assistant, "先确认当前工作目录。")
        else:
            assistant["info"]["finish_reason"] = "stop"
            append_text_part(assistant, "当前工作目录是测试目录。")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("当前工作目录是多少", session_id="s_reasoning_continue")

    assert get_message_text(result) == "当前工作目录是测试目录。"
    assert captured_assistant_history[0]["reasoning_content"] == "先确认当前工作目录。"


def test_run_session_should_stop_when_agent_loop_exceeds_max_rounds(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "agent_loop": {
            "max_rounds": 2
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        assistant["info"]["finish_reason"] = "unknown"
        append_reasoning_part(assistant, "继续思考")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    try:
        result = run_session("为什么会一直思考", session_id="s_round_limit")
        assert result["info"]["finish_reason"] == "error"
        assert result["info"]["error"]["code"] == "loop_round_limit_exceeded"
    finally:
        clear_runtime_settings_cache()


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


def test_question_tool_should_interrupt_and_save_pending_question(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_tool_call_part(
            assistant,
            tool_call_id="call_question",
            name="question",
            arguments=(
                '{"questions":[{"question":"选择方案？","header":"方案",'
                '"options":[{"label":"A","description":"快"},{"label":"B","description":"稳"}]}]}'
            ),
        )
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("需要补充问题", session_id="s_question_pending")

    assert result["info"]["status"] == "interrupted"
    assert result["info"]["finish_reason"] == "question_required"
    assert result["info"]["question"]["questions"][0]["header"] == "方案"
    assert result["info"]["question"]["questions"][0]["custom"] is True
    pending = get_pending_question("s_question_pending")
    assert pending is not None
    assert pending["questions"][0]["question"] == "选择方案？"
    assert pending["questions"][0]["custom"] is True
    assert pending["resume_mode"] == "build"
    assert pending["agent_kind"] == "primary"


def test_question_answer_should_resume_session(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_question_resume",
                name="question",
                arguments=(
                    '{"questions":[{"question":"选哪个？","header":"方向",'
                    '"options":[{"label":"方案A","description":"最小改动"},{"label":"方案B","description":"扩展性好"}]}]}'
                ),
            )
        else:
            last_user_text = get_message_text(messages[-1])
            append_text_part(assistant, "已收到方案A" if "方向：方案A" in last_user_text else "bad")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    first_result = run_session("继续执行", session_id="s_question_answer")
    request_id = first_result["info"]["question"]["request_id"]

    result = session_module.apply_question_answer(
        "s_question_answer",
        request_id,
        [{"answers": ["方案A"], "notes": "优先最小改动"}],
    )

    assert get_message_text(result) == "已收到方案A"
    assert get_pending_question("s_question_answer") is None


def test_question_reject_should_resume_session_with_reject_context(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_question_reject",
                name="question",
                arguments=(
                    '{"questions":[{"question":"是否继续？","header":"确认",'
                    '"options":[{"label":"是","description":"继续"},{"label":"否","description":"停止"}]}]}'
                ),
            )
        else:
            last_user_text = get_message_text(messages[-1])
            append_text_part(assistant, "知道用户拒绝" if "用户拒绝回答这些问题" in last_user_text else "bad")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    first_result = run_session("继续执行", session_id="s_question_reject")
    request_id = first_result["info"]["question"]["request_id"]

    result = session_module.apply_question_reject("s_question_reject", request_id)

    assert get_message_text(result) == "知道用户拒绝"
    assert get_pending_question("s_question_reject") is None


def test_question_answer_stream_events_should_continue(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_tool_call_part(
            assistant,
            tool_call_id="call_question_stream",
            name="question",
            arguments=(
                '{"questions":[{"question":"选哪个？","header":"方向",'
                '"options":[{"label":"A","description":"快"},{"label":"B","description":"稳"}]}]}'
            ),
        )
        return assistant

    def fake_stream_chat(messages, tools, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]

        def _generator():
            yield {"type": "text_delta", "delta": "继续处理"}
            assistant = create_message("assistant", session_id, status="completed")
            append_text_part(assistant, "继续处理")
            return assistant

        return _generator()

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream_chat)
    first_result = run_session("继续执行", session_id="s_question_stream")
    request_id = first_result["info"]["question"]["request_id"]

    events = list(
        run_question_answer_stream_events(
            "s_question_stream",
            request_id,
            [{"answers": ["A"], "notes": ""}],
        )
    )

    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["status"] == "completed"
    assert done_event["display_parts"][-1]["text"] == "继续处理"


def test_question_answer_should_reject_empty_answers():
    pending = session_module.PendingQuestion(
        request_id="question_empty",
        tool_name="question",
        title="等待用户回答 1 个问题",
        questions=[
            session_module.PendingQuestionItem(
                question="选哪个？",
                header="方向",
                options=[session_module.PendingQuestionOption(label="A", description="快")],
                multiple=False,
                custom=True,
            )
        ],
        agent_name="build",
        agent_kind="primary",
        resume_mode="build",
        resume_runtime_agent="build",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        delegation_id="",
        parent_tool_call_id="",
        requested_at="t1",
    )

    with pytest.raises(ValueError) as exc:
        session_module._normalize_question_answers(pending, [{"answers": [], "notes": ""}])

    assert "至少需要一个答案" in str(exc.value)


def test_task_subagent_question_should_interrupt_top_level_and_preserve_resume_context(monkeypatch):
    call_state = {"count": 0}

    def fake_stream_chat(messages, tools, llm_config=None, agent=""):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1

        def _generator():
            if False:
                yield {"type": "text_delta", "delta": ""}
            assistant = create_message("assistant", session_id, status="completed")
            if call_state["count"] == 1:
                append_tool_call_part(
                    assistant,
                    tool_call_id="call_task_question",
                    name="task",
                    arguments='{"prompt":"补充需求","agent":"explore"}',
                )
            elif agent == "explore":
                append_tool_call_part(
                    assistant,
                    tool_call_id="call_sub_question",
                    name="question",
                    arguments=(
                        '{"questions":[{"question":"需要哪种输出？","header":"输出",'
                        '"options":[{"label":"A","description":"简洁"},{"label":"B","description":"详细"}]}]}'
                    ),
                )
            else:
                append_text_part(assistant, "bad")
            return assistant

        return _generator()

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream_chat)

    events = list(run_session_stream_events("主任务", session_id="s_task_question"))

    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["finish_reason"] == "question_required"
    assert done_event["question"]["questions"][0]["header"] == "输出"
    assert done_event["delegation_id"]
    pending = get_pending_question("s_task_question")
    assert pending is not None
    assert pending["agent_kind"] == "subagent"
    assert pending["resume_runtime_agent"] == "explore"
    assert pending["delegation_id"] == done_event["delegation_id"]


def test_subagent_question_answer_should_resume_with_subagent_runtime(monkeypatch):
    pending = session_module.PendingQuestion(
        request_id="question_subagent",
        tool_name="question",
        title="等待用户回答 1 个问题",
        questions=[
            session_module.PendingQuestionItem(
                question="需要哪种输出？",
                header="输出",
                options=[session_module.PendingQuestionOption(label="A", description="简洁")],
                multiple=False,
                custom=True,
            )
        ],
        agent_name="explore",
        agent_kind="subagent",
        resume_mode="",
        resume_runtime_agent="explore",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        delegation_id="delegation_123",
        parent_tool_call_id="call_task_1",
        requested_at="t1",
    )
    session_module.PENDING_QUESTIONS["s_subagent_resume"] = pending
    captured: dict[str, object] = {}

    def fake_run_session(user_input: str, session_id: str, **kwargs):
        captured["user_input"] = user_input
        captured["session_id"] = session_id
        captured["runtime_agent"] = kwargs.get("runtime_agent")
        captured["tools"] = kwargs.get("tools")
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "subagent resumed")
        return assistant

    monkeypatch.setattr(session_module, "run_session", fake_run_session)

    result = session_module.apply_question_answer(
        "s_subagent_resume",
        "question_subagent",
        [{"answers": ["A"], "notes": "备注信息"}],
    )

    assert get_message_text(result) == "subagent resumed"
    assert captured["runtime_agent"] == "explore"
    assert isinstance(captured["tools"], list)
    assert "输出：A" in str(captured["user_input"])
    assert "备注：备注信息" in str(captured["user_input"])
    assert get_pending_question("s_subagent_resume") is None


def test_subagent_question_answer_stream_should_emit_subagent_done(monkeypatch):
    pending = session_module.PendingQuestion(
        request_id="question_subagent_stream",
        tool_name="question",
        title="等待用户回答 1 个问题",
        questions=[
            session_module.PendingQuestionItem(
                question="需要哪种输出？",
                header="输出",
                options=[session_module.PendingQuestionOption(label="A", description="简洁")],
                multiple=False,
                custom=True,
            )
        ],
        agent_name="explore",
        agent_kind="subagent",
        resume_mode="",
        resume_runtime_agent="explore",
        provider="qwen",
        vendor="qwen",
        model="qwen3-max",
        delegation_id="delegation_stream_123",
        parent_tool_call_id="call_task_stream_1",
        requested_at="t1",
    )
    session_module.PENDING_QUESTIONS["s_subagent_resume_stream"] = pending

    def fake_run_session_stream_events(user_input: str, session_id: str, **kwargs):
        captured_agent = kwargs.get("runtime_agent")
        assert user_input.startswith("question 工具已收到用户回答")
        assert session_id == "s_subagent_resume_stream"
        assert captured_agent == "explore"

        yield {
            "type": "start",
            "event_id": "evt_subagent_start",
            "session_id": session_id,
            "agent": "explore",
            "agent_kind": "subagent",
            "depth": 0,
            "provider": "qwen",
            "model": "qwen3-max",
            "started_at": "t1",
        }
        yield {
            "type": "text_delta",
            "event_id": "evt_subagent_delta",
            "session_id": session_id,
            "agent": "explore",
            "agent_kind": "subagent",
            "depth": 0,
            "delta": "subagent resumed",
        }
        yield {
            "type": "done",
            "event_id": "evt_subagent_done",
            "session_id": session_id,
            "agent": "explore",
            "agent_kind": "subagent",
            "depth": 0,
            "message_id": "msg_subagent_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {
                "round_count": 1,
                "tool_call_count": 0,
                "tool_names": [],
                "delegation_count": 1,
                "delegated_agents": ["explore"],
                "duration_ms": 100,
            },
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr(session_module, "run_session_stream_events", fake_run_session_stream_events)

    events = list(
        run_question_answer_stream_events(
            "s_subagent_resume_stream",
            "question_subagent_stream",
            [{"answers": ["A"], "notes": ""}],
        )
    )

    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["agent"] == "explore"
    assert done_event["agent_kind"] == "subagent"
    assert done_event["depth"] == 0
    assert get_pending_question("s_subagent_resume_stream") is None


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
    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")
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


def test_plan_mode_write_should_be_limited_to_workspace_plan_file(monkeypatch):
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
                arguments='{"filePath":"src/main.py","content":"x"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("在 plan 模式写文件", session_id="s_plan_write", mode="plan")
    assert str(build_plan_storage_path("s_plan_write")) in get_message_text(result)


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
                arguments='{"command":"echo hello > /tmp/a.txt","description":"Writes hello to temp file"}',
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
                arguments=(
                    '{"command":"grep -n \\"build.default.txt\\" README.md | head -5",'
                    '"description":"Finds build prompt references"}'
                ),
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    result = run_session("在 plan 模式执行只读管道 bash", session_id="s_plan_bash_pipe", mode="plan")
    assert "build.default.txt" in get_message_text(result)


def test_bash_tool_description_should_use_external_template():
    tools = build_base_tools([])
    bash_tool = next(tool for tool in tools if tool["function"]["name"] == "bash")
    function_spec = bash_tool["function"]
    runtime_settings = get_project_runtime_settings()

    assert "# Bash 工具说明" in function_spec["description"]
    assert "持久的 bash shell 会话" in function_spec["description"]
    assert "本次调用结束后立即销毁" in function_spec["description"]
    assert "不同 `bash` 调用之间不会共享这些状态" in function_spec["description"]
    assert "${directory}" not in function_spec["description"]
    assert "${maxLines}" not in function_spec["description"]
    assert "${maxBytes}" not in function_spec["description"]
    assert "当前工作区根目录" in function_spec["description"]
    assert str(runtime_settings.compaction_default.tool_output_max_lines) in function_spec["description"]
    assert str(runtime_settings.compaction_default.tool_output_max_bytes) in function_spec["description"]
    assert function_spec["parameters"]["required"] == ["command", "description"]
    assert function_spec["parameters"]["properties"]["timeout"]["description"] == "Optional timeout in milliseconds"
    assert "working directory" in function_spec["parameters"]["properties"]["workdir"]["description"]


def test_run_session_should_pass_timeout_to_bash_handler(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_bash(command, timeout=None, workdir=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["workdir"] = workdir
        return "bash output"

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        if not any(part.get("type") == "tool" for part in messages[-1]["parts"]):
            append_tool_call_part(
                assistant,
                tool_call_id="call_bash_timeout",
                name="bash",
                arguments=(
                    '{"command":"pwd","timeout":2500,'
                    '"description":"Shows current working directory"}'
                ),
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr(session_module, "run_bash", fake_run_bash)

    result = run_session("执行带超时的 bash", session_id="s_bash_timeout")

    assert get_message_text(result) == "bash output"
    assert captured == {"command": "pwd", "timeout": 2500, "workdir": None}


def test_run_session_should_pass_workdir_to_bash_handler(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_bash(command, timeout=None, workdir=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["workdir"] = workdir
        return "bash output"

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        if not any(part.get("type") == "tool" for part in messages[-1]["parts"]):
            append_tool_call_part(
                assistant,
                tool_call_id="call_bash_workdir",
                name="bash",
                arguments=(
                    '{"command":"pwd","workdir":"src",'
                    '"description":"Shows source directory path"}'
                ),
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)
    monkeypatch.setattr(session_module, "run_bash", fake_run_bash)
    monkeypatch.setattr(session_module, "resolve_bash_workdir", lambda workdir: Path("/tmp"))

    result = run_session("执行带目录的 bash", session_id="s_bash_workdir")

    assert get_message_text(result) == "bash output"
    assert captured == {"command": "pwd", "timeout": None, "workdir": "src"}


def test_run_session_should_fail_when_bash_workdir_is_outside_workspace(monkeypatch):
    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        if not any(part.get("type") == "tool" for part in messages[-1]["parts"]):
            append_tool_call_part(
                assistant,
                tool_call_id="call_bash_bad_workdir",
                name="bash",
                arguments=(
                    '{"command":"pwd","workdir":"../outside",'
                    '"description":"Attempts to escape workspace"}'
                ),
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行非法目录 bash", session_id="s_bash_bad_workdir")

    assert "超出工作区范围" in get_message_text(result)


def test_run_session_should_fail_when_bash_timeout_is_non_positive():
    handlers = session_module._build_tool_handlers(
        session_id="s_bash_bad_timeout",
        get_mode=lambda: "build",
        get_latest_model=lambda: "qwen-plus",
        get_current_runtime=lambda: ResolvedLLMConfig(
            agent="build",
            provider="qwen",
            vendor="qwen",
            model="qwen3-coder-next",
            max_tokens=32000,
            api_mode="responses",
            base_url="https://example.com",
            api_key="test",
            timeout_seconds=60,
        ),
    )

    result = handlers["bash"](command="pwd", timeout=0, description="Attempts invalid timeout")

    assert result["output"] == "Error: timeout 必须大于 0"
    assert result["metadata"]["status"] == "failed"
    assert result["metadata"]["error_code"] == "bash_timeout_invalid"


def test_edit_file_tool_schema_should_use_camel_case_and_replace_all():
    tools = build_base_tools()
    edit_tool = next(tool for tool in tools if tool["function"]["name"] == "edit_file")
    properties = edit_tool["function"]["parameters"]["properties"]

    assert "filePath" in properties
    assert "oldString" in properties
    assert "newString" in properties
    assert "replaceAll" in properties
    assert edit_tool["function"]["parameters"]["required"] == ["filePath", "oldString", "newString"]


def test_write_file_tool_schema_should_use_file_path_and_content():
    tools = build_base_tools()
    write_tool = next(tool for tool in tools if tool["function"]["name"] == "write_file")
    properties = write_tool["function"]["parameters"]["properties"]

    assert "filePath" in properties
    assert "content" in properties
    assert write_tool["function"]["parameters"]["required"] == ["filePath", "content"]


def test_lsp_tool_schema_should_expose_operation_file_path_and_position():
    tools = build_base_tools()
    lsp_tool = next(tool for tool in tools if tool["function"]["name"] == "lsp")
    properties = lsp_tool["function"]["parameters"]["properties"]

    assert properties["operation"]["enum"] == [
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
    ]
    assert "filePath" in properties
    assert "line" in properties
    assert "character" in properties
    assert lsp_tool["function"]["parameters"]["required"] == ["operation", "filePath", "line", "character"]


def test_run_session_should_route_write_file_arguments(monkeypatch):
    handlers = session_module._build_tool_handlers(
        session_id="s_write_file_route",
        get_mode=lambda: "build",
        get_latest_model=lambda: "qwen-plus",
        get_current_runtime=lambda: ResolvedLLMConfig(
            agent="build",
            provider="qwen",
            vendor="qwen",
            model="qwen3-coder-next",
            max_tokens=32000,
            api_mode="responses",
            base_url="https://example.com",
            api_key="test",
            timeout_seconds=60,
        ),
    )
    captured: dict[str, object] = {}

    def fake_run_write(file_path, content):
        captured.update(file_path=file_path, content=content)
        return {"output": "ok", "metadata": {"status": "completed"}}

    monkeypatch.setattr(session_module, "run_write", fake_run_write)

    result = handlers["write_file"](
        filePath="/tmp/demo.py",
        content="hello",
    )

    assert result["metadata"]["status"] == "completed"
    assert captured == {
        "file_path": "/tmp/demo.py",
        "content": "hello",
    }


def test_run_session_should_route_camel_case_edit_file_arguments(monkeypatch):
    handlers = session_module._build_tool_handlers(
        session_id="s_edit_file_route",
        get_mode=lambda: "build",
        get_latest_model=lambda: "qwen-plus",
        get_current_runtime=lambda: ResolvedLLMConfig(
            agent="build",
            provider="qwen",
            vendor="qwen",
            model="qwen3-coder-next",
            max_tokens=32000,
            api_mode="responses",
            base_url="https://example.com",
            api_key="test",
            timeout_seconds=60,
        ),
    )
    captured: dict[str, object] = {}

    def fake_run_edit(file_path, old_string, new_string, replace_all=False):
        captured.update(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
        return {"output": "ok", "metadata": {"status": "completed"}}

    monkeypatch.setattr(session_module, "run_edit", fake_run_edit)

    result = handlers["edit_file"](
        filePath="/tmp/demo.py",
        oldString="old",
        newString="new",
        replaceAll=True,
    )

    assert result["metadata"]["status"] == "completed"
    assert captured == {
        "file_path": "/tmp/demo.py",
        "old_string": "old",
        "new_string": "new",
        "replace_all": True,
    }


def test_run_session_should_route_lsp_arguments(monkeypatch):
    handlers = session_module._build_tool_handlers(
        session_id="s_lsp_route",
        get_mode=lambda: "build",
        get_latest_model=lambda: "qwen-plus",
        get_current_runtime=lambda: ResolvedLLMConfig(
            agent="build",
            provider="qwen",
            vendor="qwen",
            model="qwen3-coder-next",
            max_tokens=32000,
            api_mode="responses",
            base_url="https://example.com",
            api_key="test",
            timeout_seconds=60,
        ),
    )
    captured: dict[str, object] = {}

    def fake_run_lsp(operation, file_path, line, character):
        captured.update(
            operation=operation,
            file_path=file_path,
            line=line,
            character=character,
        )
        return {"output": "ok", "metadata": {"status": "completed"}}

    monkeypatch.setattr(session_module, "run_lsp", fake_run_lsp)

    result = handlers["lsp"](
        operation="hover",
        filePath="src/demo.py",
        line=10,
        character=8,
    )

    assert result["metadata"]["status"] == "completed"
    assert captured == {
        "operation": "hover",
        "file_path": "src/demo.py",
        "line": 10,
        "character": 8,
    }


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


def test_task_with_invalid_arguments_should_return_error(monkeypatch):
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_invalid_args",
                name="task",
                arguments='["bad"]',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("调用非法 task 参数", session_id="s_task_invalid_args")

    assert "Invalid tool arguments" in get_message_text(result)


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
    assert "每次只加载一个 skill" in description
    assert load_skill_tool["function"]["parameters"]["required"] == ["name"]
    assert "name" in load_skill_tool["function"]["parameters"]["properties"]
    assert "skill_names" not in load_skill_tool["function"]["parameters"]["properties"]


def test_load_skill_tool_description_should_show_empty_message_when_no_skills():
    tools = build_base_tools([])
    load_skill_tool = next(tool for tool in tools if tool["function"]["name"] == "load_skill")

    assert (
        load_skill_tool["function"]["description"]
        == "加载一个 skill，以获取完成某个特定任务的详细指导。目前没有可用的 skills。"
    )


def test_get_skill_registry_should_read_runtime_home_skills_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    runtime_skill = get_workspace().skills_dir / "runtime-skill" / "SKILL.md"
    runtime_skill.parent.mkdir(parents=True, exist_ok=True)
    runtime_skill.write_text(
        "---\nname: runtime-skill\ndescription: runtime only\n---\n# Runtime Skill\n",
        encoding="utf-8",
    )

    registry = session_module._get_skill_registry()

    assert [skill["name"] for skill in registry.list_briefs()] == ["runtime-skill"]


def test_run_session_load_skill_tool_should_return_structured_result(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_AGENT_HOME", str(tmp_path / ".my-agent"))
    configure_workspace(tmp_path / "workspace")
    skill_dir = get_workspace().skills_dir / "runtime-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: runtime-skill\ndescription: runtime only\n---\n# Runtime Skill\n按步骤执行。\n",
        encoding="utf-8",
    )
    clear_session_memory()

    call_state = {"count": 0, "tool_output": ""}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        if call_state["count"] == 2:
            call_state["tool_output"] = _last_tool_result_content(messages)
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_load_skill",
                name="load_skill",
                arguments='{"name":"runtime-skill"}',
            )
        else:
            append_text_part(assistant, "已完成")
        return assistant

    monkeypatch.setattr(session_module, "create_chat_completion", fake_chat)

    result = run_session("加载 runtime skill", session_id="s_load_skill_result")

    assert get_message_text(result) == "已完成"
    assert "## Skill: runtime-skill" in call_state["tool_output"]
    assert f"Base directory: {skill_dir.resolve()}" in call_state["tool_output"]
    assert "# Runtime Skill" in call_state["tool_output"]


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
        assert "glob" in _tool_names(tools)
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
        assert "glob" in _tool_names(tools)
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
        assert "glob" in _tool_names(tools)
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


def test_run_session_should_execute_glob_tool(monkeypatch):
    call_state = {"count": 0}

    def fake_run_glob(pattern, path=None):
        return {
            "title": "src",
            "output": f"匹配成功:{pattern}:{path}",
            "metadata": {"status": "completed", "count": 1, "truncated": False},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_glob",
                name="glob",
                arguments='{"pattern":"src/**/*.py","path":"src"}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.run_glob", fake_run_glob)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行 glob", session_id="s_run_glob")

    assert "匹配成功:src/**/*.py:src" in get_message_text(result)


def test_run_session_should_execute_grep_tool(monkeypatch):
    call_state = {"count": 0}

    def fake_run_grep(pattern, path=None, include=None):
        return {
            "title": "src",
            "output": f"搜索成功:{pattern}:{path}:{include}",
            "metadata": {"status": "completed", "count": 1, "truncated": False},
        }

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_grep",
                name="grep",
                arguments='{"pattern":"target","path":"src","include":["*.py"]}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.run_grep", fake_run_grep)
    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("执行 grep", session_id="s_run_grep")

    assert "搜索成功:target:src:['*.py']" in get_message_text(result)


def test_run_session_should_truncate_tool_output_with_task_guidance(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
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
    assert str(
        get_workspace().tool_output_root
        / "s_long_task"
        / "webfetch-call_webfetch_long.log"
    ) in text


def test_subagent_loop_should_truncate_tool_output_without_task_guidance(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
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
    configure_workspace(tmp_path)
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
    assert str(seen_metadata["full_output_path"]) == str(
        get_workspace().tool_output_root
        / "s_long_metadata"
        / "webfetch-call_webfetch_metadata.log"
    )


def test_run_read_should_support_offset_and_limit(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    configure_workspace(tmp_path)
    result = run_read(str(file_path), limit=2, offset=1)

    assert result["metadata"]["status"] == "completed"
    assert result["output"] == "b\nc\n... (1 more lines)"


def test_build_tool_message_should_backfill_attachment_runtime_fields():
    result = {
        "output": "PDF read successfully",
        "metadata": {"status": "completed", "filename": "demo.pdf"},
        "attachments": [
            {
                "type": "file",
                "mime": "application/pdf",
                "url": "data:application/pdf;base64,QUJDRA==",
            }
        ],
    }

    message = session_module._build_tool_message(
        "s_pdf",
        tool_call_id="call_pdf",
        tool_name="read_file",
        arguments='{"file_path":"demo.pdf"}',
        result=result,
        agent="build",
        turn_started_at="2026-03-20T00:00:00+00:00",
    )

    tool_part = message["parts"][0]
    output = tool_part["state"]["output"]
    attachment = output["attachments"][0]

    assert attachment["type"] == "file"
    assert attachment["mime"] == "application/pdf"
    assert attachment["sessionID"] == "s_pdf"
    assert attachment["messageID"] == message["info"]["message_id"]
    assert attachment["filename"] == "demo.pdf"
    assert str(attachment["id"]).startswith("att_")


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


def test_run_session_should_recover_incomplete_tool_calls_before_continue(monkeypatch):
    session_id = "s_recover_tool_call"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "读取这个 PDF")
    assistant_message = create_message("assistant", session_id, status="completed")
    assistant_message["info"]["agent"] = "build"
    assistant_message["info"]["model"] = "kimi-k2.5"
    assistant_message["info"]["provider"] = "kimi"
    append_text_part(assistant_message, "我先读取 PDF。")
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_pdf",
        name="read_file",
        arguments='{"file_path":"demo.pdf"}',
    )
    store.save(session_id, [user_message, assistant_message])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("继续", session_id=session_id)

    assert get_message_text(result) == "ok"
    tool_messages = [msg for msg in captured_provider_messages if msg.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_pdf"
    assert "系统恢复提示" in str(tool_messages[0]["content"])
    build_provider_adapter(resolve_llm_config("build", "kimi")).validate_messages(captured_provider_messages)

    persisted_history = store.load(session_id)
    recovered_messages = [msg for msg in persisted_history if msg["info"].get("role") == "tool"]
    assert len(recovered_messages) == 1
    recovered_output = recovered_messages[0]["parts"][0]["state"]["output"]
    assert recovered_output["metadata"]["error_code"] == "missing_tool_result_context"
    assert recovered_output["metadata"]["recovered"] is True
    assert recovered_output["metadata"]["synthetic"] is True


def test_run_session_should_insert_missing_tool_results_before_next_non_tool_message(monkeypatch):
    session_id = "s_recover_partial_tool_call"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    first_user = create_message("user", session_id, status="completed")
    append_text_part(first_user, "同时读取 PDF 和模板")
    assistant_message = create_message("assistant", session_id, status="completed")
    assistant_message["info"]["agent"] = "build"
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_pdf",
        name="read_file",
        arguments='{"file_path":"demo.pdf"}',
    )
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_template",
        name="read_file",
        arguments='{"file_path":"rule.md"}',
    )
    tool_message = create_message("tool", session_id, status="completed")
    append_tool_part(
        tool_message,
        tool_call_id="call_pdf",
        name="read_file",
        status="completed",
        arguments='{"file_path":"demo.pdf"}',
        output={"output": "PDF read successfully", "metadata": {"status": "completed"}},
    )
    interrupted_user = create_message("user", session_id, status="completed")
    append_text_part(interrupted_user, "继续")
    store.save(session_id, [first_user, assistant_message, tool_message, interrupted_user])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("再次继续", session_id=session_id)

    roles = [str(msg.get("role")) for msg in captured_provider_messages]
    assert roles.count("tool") == 2
    template_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_template"
    )
    interrupted_user_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "user" and msg.get("content") == "继续"
    )
    assert template_index < interrupted_user_index
    assert "系统恢复提示" in str(captured_provider_messages[template_index]["content"])


def test_run_session_should_not_duplicate_recovered_tool_messages(monkeypatch):
    session_id = "s_recover_tool_call_once"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "读取文件")
    assistant_message = create_message("assistant", session_id, status="completed")
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_read",
        name="read_file",
        arguments='{"file_path":"demo.txt"}',
    )
    store.save(session_id, [user_message, assistant_message])

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("继续", session_id=session_id)
    run_session("再继续", session_id=session_id)

    persisted_history = store.load(session_id)
    recovered_messages = [
        msg
        for msg in persisted_history
        if msg["info"].get("role") == "tool"
        and msg["parts"][0]["state"]["output"]["metadata"].get("error_code") == "missing_tool_result_context"
    ]
    assert len(recovered_messages) == 1


def test_run_session_should_repair_assistant_tool_calls_prefix(monkeypatch):
    session_id = "s_prefix_assistant_tool_calls"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="tool_calls")
    assistant_message["info"]["agent"] = "build"
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_prefix",
        name="read_file",
        arguments='{"file_path":"demo.txt"}',
    )
    store.save(session_id, [assistant_message])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("继续", session_id=session_id)

    assert get_message_text(result) == "ok"
    build_provider_adapter(resolve_llm_config("build", "kimi")).validate_messages(captured_provider_messages)
    tool_messages = [msg for msg in captured_provider_messages if msg.get("role") == "tool"]
    assert any(msg.get("tool_call_id") == "call_prefix" for msg in tool_messages)
    assert any("tool result 已缺失" in str(msg.get("content", "")) for msg in tool_messages)


def test_run_session_should_repair_tool_prefix_with_synthetic_assistant(monkeypatch):
    session_id = "s_prefix_tool_orphan"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    tool_message = create_message("tool", session_id, status="completed")
    append_tool_part(
        tool_message,
        tool_call_id="call_orphan",
        name="read_file",
        status="completed",
        arguments='{"file_path":"demo.txt"}',
        output={"output": "hello", "metadata": {"status": "completed"}},
    )
    store.save(session_id, [tool_message])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("继续", session_id=session_id)

    assert get_message_text(result) == "ok"
    build_provider_adapter(resolve_llm_config("build", "kimi")).validate_messages(captured_provider_messages)
    assistant_index = next(index for index, msg in enumerate(captured_provider_messages) if msg.get("role") == "assistant")
    assert captured_provider_messages[assistant_index]["tool_calls"][0]["id"] == "call_orphan"
    assert captured_provider_messages[assistant_index + 1]["role"] == "tool"
    assert captured_provider_messages[assistant_index + 1]["tool_call_id"] == "call_orphan"


def test_run_session_should_repair_midstream_orphan_tool(monkeypatch):
    session_id = "s_midstream_tool_orphan"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "第一问")
    assistant_message = create_message("assistant", session_id, status="completed")
    append_text_part(assistant_message, "第一答")
    orphan_tool_message = create_message("tool", session_id, status="completed")
    append_tool_part(
        orphan_tool_message,
        tool_call_id="call_mid",
        name="read_file",
        status="completed",
        arguments='{"file_path":"demo.txt"}',
        output={"output": "mid", "metadata": {"status": "completed"}},
    )
    store.save(session_id, [user_message, assistant_message, orphan_tool_message])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("继续", session_id=session_id)

    assert get_message_text(result) == "ok"
    build_provider_adapter(resolve_llm_config("build", "kimi")).validate_messages(captured_provider_messages)
    tool_index = next(index for index, msg in enumerate(captured_provider_messages) if msg.get("role") == "tool")
    assert captured_provider_messages[tool_index - 1]["role"] == "assistant"
    assert captured_provider_messages[tool_index - 1]["tool_calls"][0]["id"] == "call_mid"


def test_run_session_should_preserve_previous_pending_tool_calls_before_repairing_orphan_tool(monkeypatch):
    session_id = "s_orphan_tool_should_not_override_pending"
    store = InMemorySessionMemoryStore(max_messages=24)
    configure_session_memory_store(store)
    clear_session_memory(session_id)

    user_message = create_message("user", session_id, status="completed")
    append_text_part(user_message, "先读取文件再继续")

    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="tool_calls")
    assistant_message["info"]["agent"] = "build"
    assistant_message["info"]["model"] = "kimi-k2.5"
    assistant_message["info"]["provider"] = "kimi"
    append_tool_call_part(
        assistant_message,
        tool_call_id="call_pending",
        name="read_file",
        arguments='{"file_path":"pending.txt"}',
    )

    orphan_tool_message = create_message("tool", session_id, status="completed")
    append_tool_part(
        orphan_tool_message,
        tool_call_id="call_orphan_mix",
        name="read_file",
        status="completed",
        arguments='{"file_path":"orphan.txt"}',
        output={"output": "orphan content", "metadata": {"status": "completed"}},
    )

    interrupted_user = create_message("user", session_id, status="completed")
    append_text_part(interrupted_user, "继续")
    store.save(session_id, [user_message, assistant_message, orphan_tool_message, interrupted_user])

    captured_provider_messages: list[dict[str, object]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        nonlocal captured_provider_messages
        captured_provider_messages = to_provider_messages(messages)
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("再次继续", session_id=session_id)

    assert get_message_text(result) == "ok"
    build_provider_adapter(resolve_llm_config("build", "kimi")).validate_messages(captured_provider_messages)

    pending_tool_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_pending"
    )
    orphan_assistant_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "assistant"
        and msg.get("tool_calls")
        and msg["tool_calls"][0]["id"] == "call_orphan_mix"
    )
    orphan_tool_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_orphan_mix"
    )
    continue_user_index = next(
        index
        for index, msg in enumerate(captured_provider_messages)
        if msg.get("role") == "user" and msg.get("content") == "继续"
    )

    assert pending_tool_index < orphan_assistant_index < orphan_tool_index < continue_user_index
    assert "系统恢复提示" in str(captured_provider_messages[pending_tool_index]["content"])

    persisted_history = store.load(session_id)
    recovered_tool_ids = [
        str(part["state"].get("tool_call_id", ""))
        for message in persisted_history
        if message["info"].get("role") == "tool"
        for part in message.get("parts", [])
        if part.get("type") == "tool"
        and isinstance(part.get("state"), dict)
        and isinstance(part["state"].get("output"), dict)
        and part["state"]["output"].get("metadata", {}).get("error_code") == "missing_tool_result_context"
    ]
    assert "call_pending" in recovered_tool_ids
    assert "call_orphan_mix" not in recovered_tool_ids


def test_workspace_should_store_session_history_in_global_sessions_dir(tmp_path):
    configure_workspace(tmp_path / "project-a")

    assert get_workspace().sessions_dir == get_workspace().workspaces_root / "sessions"


def test_file_session_memory_store_should_share_session_file_across_workspaces(tmp_path):
    first_workspace = tmp_path / "project-a"
    second_workspace = tmp_path / "project-b"
    first_workspace.mkdir()
    second_workspace.mkdir()

    configure_workspace(first_workspace)
    store = session_module.FileSessionMemoryStore(max_messages=24)
    user_message = create_message("user", "shared_session", status="completed")
    append_text_part(user_message, "第一条")
    assistant_message = create_message("assistant", "shared_session", status="completed")
    append_text_part(assistant_message, "第一条回答")
    store.save("shared_session", [user_message, assistant_message])
    first_session_file = get_workspace().sessions_dir / "shared_session.json"

    configure_workspace(second_workspace)
    second_session_file = get_workspace().sessions_dir / "shared_session.json"
    loaded_messages = store.load("shared_session")

    assert first_session_file == second_session_file
    assert first_session_file.exists()
    assert [get_message_text(message) for message in loaded_messages] == ["第一条", "第一条回答"]


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
    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")
    assert done_event["display_parts"][0]["kind"] == "assistant_text"
    assert done_event["display_parts"][0]["text"] == "流式回答"


def test_run_session_stream_events_should_emit_runtime_alert_without_entering_done_payload(monkeypatch):
    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "流式回答")
        return assistant
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)
    monkeypatch.setattr(
        session_module,
        "describe_mcp_runtime_alerts_for_mode",
        lambda mode=None: [
            type(
                "Alert",
                (),
                {
                    "server_alias": "github",
                    "code": "mcp_server_unavailable",
                    "message": "mcp.servers.env.GITHUB_TOKEN 引用了未设置的环境变量 GITHUB_TOKEN",
                },
            )()
        ],
    )

    events = list(run_session_stream_events("你好", session_id="s_stream_alert"))

    runtime_alert = next(event for event in events if event["type"] == "runtime_alert")
    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")

    assert runtime_alert["scope"] == "mcp"
    assert runtime_alert["server_alias"] == "github"
    assert "GITHUB_TOKEN" in runtime_alert["message"]
    assert all(item["kind"] != "runtime_alert" for item in done_event["display_parts"])
    assert all(item["kind"] != "runtime_alert" for item in done_event["process_items"])


def test_run_session_stream_events_should_emit_reasoning_delta_and_keep_separate_display_part(monkeypatch):
    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        yield {"type": "reasoning_delta", "delta": "先分析上下文"}
        yield {"type": "text_delta", "delta": "最终回答"}
        assistant = create_message("assistant", session_id, status="completed")
        append_reasoning_part(assistant, "先分析上下文")
        append_text_part(assistant, "最终回答")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("测试 reasoning 流式展示", session_id="s_stream_reasoning"))
    event_names = [event["type"] for event in events]
    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")

    assert "reasoning_delta" in event_names
    assert [item["kind"] for item in done_event["display_parts"]] == ["reasoning", "assistant_text"]
    assert done_event["display_parts"][0]["text"] == "先分析上下文"
    assert done_event["display_parts"][1]["text"] == "最终回答"


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
    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")

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


def test_run_session_stream_events_should_stop_before_round_start(monkeypatch):
    request_session_stop("s_stream_stop_before")

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        raise AssertionError("停止检查应在模型调用前生效")
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    events = list(run_session_stream_events("停止前检查", session_id="s_stream_stop_before"))

    done_event = next(event for event in events if event["type"] == "done")
    assert done_event["status"] == "interrupted"
    assert done_event["finish_reason"] == "cancelled"
    assert done_event["display_parts"][0]["text"] == "当前执行已手动停止。"


def test_run_session_stream_events_should_stop_after_tool_result(monkeypatch):
    call_state = {"count": 0}

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(assistant, tool_call_id="call_stop_tool", name="todo_read", arguments="{}")
            return assistant
        raise AssertionError("停止后不应进入下一轮模型调用")
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    original_execute = session_module.ToolExecutor.execute

    def fake_execute(self, tool_name, arguments, **kwargs):
        request_session_stop(kwargs["session_id"])
        return original_execute(self, tool_name, arguments, **kwargs)

    monkeypatch.setattr(session_module.ToolExecutor, "execute", fake_execute)

    events = list(run_session_stream_events("工具后停止", session_id="s_stream_stop_tool"))

    event_names = [event["type"] for event in events]
    done_event = next(event for event in events if event["type"] == "done")
    assert "tool_result" in event_names
    assert done_event["status"] == "interrupted"
    assert done_event["finish_reason"] == "cancelled"


def test_run_session_stream_events_should_stop_after_subagent(monkeypatch):
    call_state = {"count": 0}

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        agent = ""
        for item in reversed(messages):
            if item["info"].get("role") != "assistant":
                continue
            agent = str(item["info"].get("agent", ""))
            if agent:
                break
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")

        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_task_stop",
                name="task",
                arguments='{"agent":"explore","prompt":"子任务停止"}',
            )
            return assistant
        if call_state["count"] == 2:
            append_text_part(assistant, "子代理已完成")
            return assistant

        raise AssertionError("主代理在子代理返回后应命中停止，不再继续下一轮")
        yield  # pragma: no cover

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)
    original_prepare = session_module._prepare_task_tool_request

    def fake_prepare(arguments, *, delegation_id=None):
        result = original_prepare(arguments, delegation_id=delegation_id)
        request_session_stop("s_stream_stop_subagent")
        return result

    monkeypatch.setattr(session_module, "_prepare_task_tool_request", fake_prepare)

    events = list(run_session_stream_events("子代理后停止", session_id="s_stream_stop_subagent"))

    done_event = next(event for event in events if event["type"] == "done" and event["agent_kind"] == "primary")
    assert any(event["type"] == "done" and event["agent_kind"] == "subagent" for event in events)
    assert done_event["agent_kind"] == "primary"
    assert done_event["status"] == "interrupted"
    assert done_event["finish_reason"] == "cancelled"


def test_run_session_stream_events_should_continue_with_normal_history_after_stop(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_continue_after_stop")

    state = {"call_count": 0}
    captured = {"message_count": 0, "history_texts": []}

    def fake_stream(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del tools, max_tokens, hooks, llm_config, agent
        session_id = messages[-1]["info"]["session_id"]
        state["call_count"] += 1

        if state["call_count"] == 1:
            while True:
                yield {"type": "text_delta", "delta": "处理中"}

        captured["message_count"] = len(messages)
        captured["history_texts"] = [get_message_text(message) for message in messages]
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "继续处理 hello.py 检查。")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion_stream", fake_stream)

    stream = run_session_stream_events("检查工作区里是否存在 hello.py 文件", session_id="s_continue_after_stop")
    next(stream)
    next(stream)
    next(stream)
    request_session_stop("s_continue_after_stop")
    stream.close()

    assert session_module.is_session_stop_requested("s_continue_after_stop") is False

    events = list(run_session_stream_events("继续任务", session_id="s_continue_after_stop"))
    done_event = next(event for event in events if event["type"] == "done")

    assert done_event["status"] == "completed"
    assert done_event["display_parts"][0]["text"] == "继续处理 hello.py 检查。"
    assert captured["message_count"] >= 4
    assert "检查工作区里是否存在 hello.py 文件" in captured["history_texts"]
    assert "当前执行已手动停止。" in captured["history_texts"]
    assert all("最近一次未完成任务的恢复上下文" not in text for text in captured["history_texts"])


def test_merge_display_parts_with_message_should_not_append_fallback_when_stream_parts_exist():
    assistant = create_message("assistant", "s_display_merge", status="completed")
    text_part = append_text_part(assistant, "再总结")
    text_part["created_at"] = "2026-03-14T00:00:00+00:00"
    assistant["info"]["agent"] = "build"

    display_parts = [
        {
            "id": "disp_1",
            "kind": "assistant_text",
            "title": "build 回复",
            "detail": "",
            "text": "先说明",
            "created_at": "2026-03-14T00:00:01+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        },
        {
            "id": "disp_2",
            "kind": "tool_call",
            "title": "build 调用工具: todo_read",
            "detail": "{}",
            "text": "",
            "created_at": "2026-03-14T00:00:02+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "todo_read",
            "tool_call_id": "call_1",
        },
        {
            "id": "disp_3",
            "kind": "tool_result",
            "title": "build 工具结果: todo_read",
            "detail": "completed ok",
            "text": "",
            "created_at": "2026-03-14T00:00:03+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "todo_read",
            "tool_call_id": "call_1",
        },
        {
            "id": "disp_4",
            "kind": "assistant_text",
            "title": "build 回复",
            "detail": "",
            "text": "再总结",
            "created_at": "2026-03-14T00:00:04+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 2,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        },
    ]

    merged = session_module._merge_display_parts_with_message(display_parts, assistant)

    assert merged == display_parts


def test_merge_display_parts_with_message_should_not_duplicate_reasoning_fallback():
    assistant = create_message("assistant", "s_display_reasoning", status="completed")
    reasoning_part = append_reasoning_part(assistant, "先分析")
    reasoning_part["created_at"] = "2026-03-14T00:00:00+00:00"
    assistant["info"]["agent"] = "build"

    display_parts = [
        {
            "id": "disp_reasoning_1",
            "kind": "reasoning",
            "title": "build 思考",
            "detail": "",
            "text": "先分析",
            "created_at": "2026-03-14T00:00:00+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        }
    ]

    merged = session_module._merge_display_parts_with_message(display_parts, assistant)

    assert merged == display_parts


def test_run_session_should_remember_explicit_provider_and_model(monkeypatch):
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

    run_session(
        "第一轮",
        session_id="s_provider_memory",
        provider="qwen",
        provider_specified=True,
        model="qwen3-coder-next",
        model_specified=True,
    )
    run_session("第二轮", session_id="s_provider_memory")

    assert seen[0] == ("qwen", "qwen3-coder-next")
    assert seen[1] == ("qwen", "qwen3-coder-next")


def test_run_session_should_reset_to_agent_default_provider_and_model(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_provider_reset")
    seen: list[tuple[str, str]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen.append((llm_config.provider, llm_config.model))
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session(
        "第一轮",
        session_id="s_provider_reset",
        provider="qwen",
        provider_specified=True,
        model="qwen3-coder-next",
        model_specified=True,
    )
    run_session("第二轮", session_id="s_provider_reset", mode="plan", provider="", provider_specified=True)

    assert seen[0] == ("qwen", "qwen3-coder-next")
    assert seen[1] == ("qwen", "qwen3.5-flash")


def test_run_session_should_use_provider_default_model_when_model_is_omitted(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_provider_default_model")
    seen: list[tuple[str, str]] = []

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        seen.append((llm_config.provider, llm_config.model))
        assistant = create_message("assistant", session_id, status="completed")
        append_text_part(assistant, "ok")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    run_session("第一轮", session_id="s_provider_default_model", provider="gpt", provider_specified=True)

    assert seen[0] == ("gpt", "gpt-4.1")


def test_build_system_prompt_should_use_model_specific_prompt(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id=generate_session_id("test_prompt"),
    )

    assert "你是 **爪爪**" in prompt
    assert "- vendor: qwen" in prompt
    assert "- model: qwen3-max" in prompt


def test_build_system_prompt_should_fallback_to_default_prompt(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(
        agent="build",
        model="gpt-4.1",
        provider="gpt",
        vendor="openai",
        session_id=generate_session_id("test_prompt"),
    )

    assert "Qwen 系列模型" not in prompt
    assert "你是 **爪爪**" in prompt
    assert "- vendor: openai" in prompt
    assert "- model: gpt-4.1" in prompt


def test_build_system_prompt_should_not_append_mcp_warning_text(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    monkeypatch.setattr(
        session_module,
        "describe_mcp_runtime_alerts_for_mode",
        lambda mode=None: [
            type(
                "Alert",
                (),
                {
                    "server_alias": "github",
                    "code": "mcp_server_unavailable",
                    "message": "未设置 GITHUB_TOKEN",
                },
            )()
        ],
    )

    prompt = build_system_prompt(
        agent="build",
        model="gpt-4.1",
        provider="gpt",
        vendor="openai",
        session_id=generate_session_id("test_prompt"),
    )

    assert "GITHUB_TOKEN" not in prompt
    assert "以下 MCP server 当前不可用" not in prompt
    assert "未设置 GITHUB_TOKEN" not in prompt


def test_build_system_prompt_should_share_vendor_prompt_for_qwen_coder(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    prompt = build_system_prompt(
        agent="build",
        model="qwen3-coder-next",
        provider="qwen-coder",
        vendor="qwen",
        session_id=generate_session_id("test_prompt"),
    )

    assert "你是 **爪爪**" in prompt
    assert "- provider: qwen-coder" in prompt
    assert "- vendor: qwen" in prompt
    assert "- model: qwen3-coder-next" in prompt


def test_build_system_prompt_should_append_agents_md(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    (tmp_path / "AGENTS.md").write_text("请始终先写测试。", encoding="utf-8")
    configure_workspace(tmp_path)

    prompt = build_system_prompt(
        agent="plan",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_plan_prompt",
    )

    assert "请始终先写测试。" in prompt
    assert "以下是当前工作目录下的 AGENTS.md 内容" in prompt
    assert f"- workdir: {tmp_path}" in prompt
    assert str(build_plan_storage_path("s_plan_prompt")) in prompt
    assert "{plan_path}" not in prompt


def test_build_system_prompt_should_append_global_agents_md(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    home_dir = tmp_path / "home"
    global_agents_path = home_dir / ".my-agent" / "AGENTS.md"
    global_agents_path.parent.mkdir(parents=True)
    global_agents_path.write_text("请优先保证全局风格一致。", encoding="utf-8")
    monkeypatch.setattr(session_module.Path, "home", lambda: home_dir)
    configure_workspace(tmp_path)

    prompt = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_global_prompt",
    )

    assert "请优先保证全局风格一致。" in prompt
    assert "以下是全局 ~/.my-agent/AGENTS.md 内容" in prompt


def test_build_system_prompt_should_append_global_agents_md_before_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    home_dir = tmp_path / "home"
    global_agents_path = home_dir / ".my-agent" / "AGENTS.md"
    global_agents_path.parent.mkdir(parents=True)
    global_agents_path.write_text("全局规范。", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("工作区规范。", encoding="utf-8")
    monkeypatch.setattr(session_module.Path, "home", lambda: home_dir)
    configure_workspace(tmp_path)

    prompt = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_global_local_prompt",
    )

    assert prompt.index("全局规范。") < prompt.index("工作区规范。")


def test_build_system_prompt_should_ignore_missing_or_empty_global_agents_md(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    home_dir = tmp_path / "home"
    monkeypatch.setattr(session_module.Path, "home", lambda: home_dir)
    configure_workspace(tmp_path)

    prompt_without_file = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_global_missing_prompt",
    )

    assert "以下是全局 ~/.my-agent/AGENTS.md 内容" not in prompt_without_file

    global_agents_path = home_dir / ".my-agent" / "AGENTS.md"
    global_agents_path.parent.mkdir(parents=True)
    global_agents_path.write_text("   \n", encoding="utf-8")

    prompt_with_empty_file = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_global_empty_prompt",
    )

    assert "以下是全局 ~/.my-agent/AGENTS.md 内容" not in prompt_with_empty_file


def test_build_system_prompt_should_ignore_global_agents_md_read_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    home_dir = tmp_path / "home"
    global_agents_path = home_dir / ".my-agent" / "AGENTS.md"
    global_agents_path.parent.mkdir(parents=True)
    global_agents_path.write_text("全局规范。", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("工作区规范。", encoding="utf-8")
    monkeypatch.setattr(session_module.Path, "home", lambda: home_dir)
    configure_workspace(tmp_path)

    original_read_text = session_module.Path.read_text

    def fake_read_text(path_obj: Path, *args, **kwargs):
        if path_obj == global_agents_path:
            raise OSError("boom")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(session_module.Path, "read_text", fake_read_text)

    prompt = build_system_prompt(
        agent="build",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_global_error_prompt",
    )

    assert "以下是全局 ~/.my-agent/AGENTS.md 内容" not in prompt
    assert "工作区规范。" in prompt


def test_build_system_prompt_should_include_git_environment(monkeypatch):
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (True, "/tmp/repo"))
    prompt = build_system_prompt(
        agent="explore",
        model="gemini-2.0-flash",
        provider="gemini",
        vendor="google",
        session_id=generate_session_id("test_prompt"),
    )

    assert "- is_git_repo: true" in prompt
    assert "- git_root: /tmp/repo" in prompt
    assert "- provider: gemini" in prompt
    assert "- vendor: google" in prompt
    assert "当前可用 skills catalog" not in prompt
    assert "{skills_catalog}" not in prompt
    assert "`load_skill`" in prompt


def test_build_system_prompt_should_share_plan_path_source_with_plan_enter(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "_detect_git_repository", lambda _workdir: (False, ""))
    configure_workspace(tmp_path)

    prompt = build_system_prompt(
        agent="plan",
        model="qwen3-max",
        provider="qwen",
        vendor="qwen",
        session_id="s_plan_source",
    )
    expected_path = str(build_plan_placeholder_path("s_plan_source"))

    assert expected_path == str(build_plan_storage_path("s_plan_source"))
    assert expected_path in prompt


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
    assert seen_system_prompts[-1] == "PROMPT::plan::qwen::qwen::qwen3.5-flash"


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


def test_run_session_should_fail_when_session_id_missing():
    with pytest.raises(ValueError, match="session_id 不能为空"):
        run_session("缺少会话", session_id="")


def test_generate_session_id_should_return_random_cli_style_value():
    first = generate_session_id("cli")
    second = generate_session_id("cli")

    assert first.startswith("cli_")
    assert second.startswith("cli_")
    assert first != second


def test_run_session_should_report_type_error_for_stringified_todo_list(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_todo_stringified")
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_todo_write",
                name="todo_write",
                arguments=(
                    '{"todo_list":"[{\\"id\\":\\"task1\\",\\"text\\":\\"搜索 hello.py 文件位置\\",'
                    '\\"priority\\":\\"high\\",\\"status\\":\\"completed\\"}]"}'
                ),
            )
        else:
            append_text_part(assistant, "done")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试 todo_write 类型错误", session_id="s_todo_stringified")
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_todo_stringified")
    tool_outputs = [
        str(
            (
                part.get("state", {}).get("output", {}).get("output", "")
                if isinstance(part.get("state"), dict)
                else ""
            )
        )
        for message in history_messages
        for part in message["parts"]
        if part.get("type") == "tool"
    ]

    assert get_message_text(result) == "done"
    assert any("todo_list 必须是 JSON array，不能是字符串" in output for output in tool_outputs)


def test_run_session_should_allow_read_file_with_initialized_tool_session(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_read_file_session")
    target_file = tmp_path / "notes.txt"
    target_file.write_text("hello", encoding="utf-8")
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_read",
                name="read_file",
                arguments=f'{{"file_path":"{target_file}"}}',
            )
        else:
            append_text_part(assistant, "done")
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试 read_file session", session_id="s_read_file_session")
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_read_file_session")
    tool_outputs = [
        str(
            (
                part.get("state", {}).get("output", {}).get("output", "")
                if isinstance(part.get("state"), dict)
                else ""
            )
        )
        for message in history_messages
        for part in message["parts"]
        if part.get("type") == "tool"
    ]

    assert get_message_text(result) == "done"
    assert any("hello" in output for output in tool_outputs)
    assert all("session_id 尚未初始化" not in output for output in tool_outputs)


def test_run_session_should_keep_empty_file_read_as_completed_tool_result(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_read_empty_file_session")
    target_file = tmp_path / "empty.txt"
    target_file.write_text("", encoding="utf-8")
    call_state = {"count": 0}

    def fake_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None):
        session_id = messages[-1]["info"]["session_id"]
        call_state["count"] += 1
        assistant = create_message("assistant", session_id, status="completed")
        if call_state["count"] == 1:
            append_tool_call_part(
                assistant,
                tool_call_id="call_read_empty",
                name="read_file",
                arguments=f'{{"file_path":"{target_file}"}}',
            )
        else:
            append_text_part(assistant, _last_tool_result_content(messages))
        return assistant

    monkeypatch.setattr("agent.runtime.session.create_chat_completion", fake_chat)

    result = run_session("测试 read_file 空文件 session", session_id="s_read_empty_file_session")
    history_messages = session_module.SESSION_MEMORY_STORE.load("s_read_empty_file_session")
    tool_outputs = [
        part.get("state", {}).get("output", {})
        for message in history_messages
        for part in message["parts"]
        if part.get("type") == "tool" and isinstance(part.get("state"), dict)
    ]

    assert get_message_text(result) == "文件存在，但内容为空。"
    assert any(output.get("output") == "文件存在，但内容为空。" for output in tool_outputs)
    assert any(
        isinstance(output.get("metadata"), dict)
        and output["metadata"].get("status") == "completed"
        and output["metadata"].get("is_empty") is True
        for output in tool_outputs
    )


def test_resolve_llm_config_should_expose_provider_vendor(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "qwen", "qwen3-coder-next")
        assert config.provider == "qwen"
        assert config.vendor == "qwen"
        assert config.model == "qwen3-coder-next"
        assert config.max_tokens == 32000
        assert config.api_mode == "chat_completions"
        assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert config.timeout_seconds == 60
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_support_kimi_provider(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-kimi-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "kimi")
        assert config.provider == "kimi"
        assert config.vendor == "kimi"
        assert config.model == "kimi-k2.5"
        assert config.max_tokens == 32000
        assert config.api_mode == "chat_completions"
        assert config.base_url == "https://api.moonshot.cn/v1"
        assert config.timeout_seconds == 60
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_support_kimi_model_under_qwen_provider(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "qwen", "kimi/kimi-k2.5")
        assert config.provider == "qwen"
        assert config.vendor == "qwen"
        assert config.model == "kimi/kimi-k2.5"
        assert config.max_tokens == 32000
        assert config.api_mode == "chat_completions"
        assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert config.timeout_seconds == 60
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_support_zhipu_glm5_model_under_qwen_provider(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "qwen", "ZHIPU/GLM-5")
        assert config.provider == "qwen"
        assert config.vendor == "qwen"
        assert config.model == "ZHIPU/GLM-5"
        assert config.max_tokens == 32000
        assert config.api_mode == "chat_completions"
        assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert config.timeout_seconds == 60
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_use_provider_default_model_when_provider_overridden(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    clear_runtime_settings_cache()

    try:
        config = resolve_llm_config("build", "gpt")
        assert config.provider == "gpt"
        assert config.model == "gpt-4.1"
        assert config.max_tokens == 32000
        assert config.api_mode == "responses"
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_require_kimi_api_key(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    clear_runtime_settings_cache()

    try:
        with pytest.raises(ValueError, match="KIMI_API_KEY"):
            resolve_llm_config("build", "kimi")
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_not_fallback_to_generic_api_key(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "generic-key")
    clear_runtime_settings_cache()

    try:
        with pytest.raises(ValueError, match="QWEN_API_KEY"):
            resolve_llm_config("build", "qwen")
    finally:
        clear_runtime_settings_cache()


def test_get_runtime_settings_should_require_vendor(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "defaults": {
            "max_tokens": 32000
          },
          "providers": {
            "qwen": {
              "base_url": "https://example.com/v1",
              "default_model": "qwen3-max",
              "models": {
                "qwen3-max": {}
              },
              "api_key_env": "QWEN_API_KEY"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen",
              "model": "qwen3-max"
            },
            "plan": {
              "provider": "qwen",
              "model": "qwen3-max"
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


def test_get_runtime_settings_should_require_agent_default_model_in_provider_models(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "defaults": {
            "max_tokens": 32000
          },
          "providers": {
            "qwen": {
              "vendor": "qwen",
              "base_url": "https://example.com/v1",
              "default_model": "qwen3-max",
              "models": {
                "qwen3-max": {}
              },
              "api_key_env": "QWEN_API_KEY",
              "api_mode": "chat_completions"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen",
              "model": "qwen3-coder-next"
            },
            "plan": {
              "provider": "qwen",
              "model": "qwen3-max"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)

    try:
        with pytest.raises(ValueError, match="agent_defaults.build.model"):
            resolve_llm_config("build")
    finally:
        clear_runtime_settings_cache()


def test_get_runtime_settings_should_default_api_mode_to_responses(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "defaults": {
            "max_tokens": 32000
          },
          "providers": {
            "gpt": {
              "vendor": "openai",
              "base_url": "https://api.openai.com/v1",
              "default_model": "gpt-4.1",
              "models": {
                "gpt-4.1": {}
              },
              "api_key_env": "OPENAI_API_KEY"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "gpt",
              "model": "gpt-4.1"
            },
            "plan": {
              "provider": "gpt",
              "model": "gpt-4.1"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    try:
        config = resolve_llm_config("build")
        assert config.max_tokens == 32000
        assert config.api_mode == "responses"
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_use_global_default_max_tokens_from_runtime_file(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "defaults": {
            "max_tokens": 45678
          },
          "providers": {
            "qwen": {
              "vendor": "qwen",
              "base_url": "https://example.com/v1",
              "default_model": "qwen3-max",
              "models": {
                "qwen3-max": {}
              },
              "api_key_env": "QWEN_API_KEY",
              "api_mode": "chat_completions"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen",
              "model": "qwen3-max"
            },
            "plan": {
              "provider": "qwen",
              "model": "qwen3-max"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")

    try:
        config = resolve_llm_config("build")
        assert config.max_tokens == 45678
    finally:
        clear_runtime_settings_cache()


def test_resolve_llm_config_should_allow_model_level_max_tokens_override(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "defaults": {
            "max_tokens": 32000
          },
          "providers": {
            "qwen": {
              "vendor": "qwen",
              "base_url": "https://example.com/v1",
              "default_model": "qwen3-max",
              "models": {
                "qwen3-max": {
                  "max_tokens": 65432
                }
              },
              "api_key_env": "QWEN_API_KEY",
              "api_mode": "chat_completions"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen",
              "model": "qwen3-max"
            },
            "plan": {
              "provider": "qwen",
              "model": "qwen3-max"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")

    try:
        config = resolve_llm_config("build")
        assert config.max_tokens == 65432
    finally:
        clear_runtime_settings_cache()


def test_get_runtime_settings_should_require_defaults_max_tokens(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    config_path.write_text(
        """
        {
          "providers": {
            "qwen": {
              "vendor": "qwen",
              "base_url": "https://example.com/v1",
              "default_model": "qwen3-max",
              "models": {
                "qwen3-max": {}
              },
              "api_key_env": "QWEN_API_KEY",
              "api_mode": "chat_completions"
            }
          },
          "agent_defaults": {
            "build": {
              "provider": "qwen",
              "model": "qwen3-max"
            },
            "plan": {
              "provider": "qwen",
              "model": "qwen3-max"
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.LLM_CONFIG_PATH", config_path)

    try:
        with pytest.raises(ValueError, match="defaults.max_tokens"):
            resolve_llm_config("build")
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_use_default_values_when_file_missing(tmp_path, monkeypatch):
    missing_path = tmp_path / "missing_project_runtime.json"
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", missing_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.compaction_default.tool_result_prune_enabled is True
        assert settings.compaction_default.tool_result_keep_recent == 3
        assert settings.compaction_default.tool_result_prune_min_chars == 100
        assert settings.compaction_default.summary_trigger_threshold == 50000
        assert settings.compaction_default.summary_max_tokens == 2000
        assert settings.compaction_default.tool_output_max_lines == 2000
        assert settings.compaction_default.tool_output_max_bytes == 50 * 1024
        assert settings.compaction_vendors == {}
        assert settings.file_extraction_default.allowed_extensions == (".pdf",)
        assert settings.file_extraction_default.cleanup_mode == "async_delete"
        assert settings.file_extraction_vendors == {}
        assert settings.agent_loop.max_rounds == 8
        assert settings.subagent_loop.max_rounds == 15
        assert settings.logging.truncate_enabled is False
        assert settings.logging.truncate_limit == 500
        assert settings.session_memory.trim_enabled is True
        assert settings.session_memory.max_messages == 24
        assert settings.lsp.enabled is True
        assert settings.lsp.languages["java"].enabled is True
        assert settings.lsp.languages["java"].command == (
            "/usr/bin/env",
            "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
            "jdtls",
        )
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_compaction_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "tool_result_prune_enabled": false,
              "tool_result_keep_recent": 5,
              "tool_result_prune_min_chars": 60,
              "summary_trigger_threshold": 1234,
              "summary_max_tokens": 321,
              "tool_output_max_lines": 88,
              "tool_output_max_bytes": 4096
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.compaction_default.tool_result_prune_enabled is False
        assert settings.compaction_default.tool_result_keep_recent == 5
        assert settings.compaction_default.tool_result_prune_min_chars == 60
        assert settings.compaction_default.summary_trigger_threshold == 1234
        assert settings.compaction_default.summary_max_tokens == 321
        assert settings.compaction_default.tool_output_max_lines == 88
        assert settings.compaction_default.tool_output_max_bytes == 4096
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_support_json_comments(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            // 默认配置
            "default": {
              "tool_result_keep_recent": 6,
              /* 摘要 token 上限 */
              "summary_max_tokens": 789
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.compaction_default.tool_result_keep_recent == 6
        assert settings.compaction_default.summary_max_tokens == 789
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_file_extraction_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "file_extraction": {
            "default": {
              "allowed_extensions": [".pdf", ".md"],
              "cleanup_mode": "async_delete"
            },
            "vendors": {
              "kimi": {
                "allowed_extensions": [".pdf"]
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.file_extraction_default.allowed_extensions == (".pdf", ".md")
        assert settings.file_extraction_default.cleanup_mode == "async_delete"
        assert settings.file_extraction_vendors["kimi"].allowed_extensions == (".pdf",)
        assert settings.file_extraction_vendors["kimi"].cleanup_mode == "async_delete"
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_agent_loop_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "agent_loop": {
            "max_rounds": 12
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.agent_loop.max_rounds == 12
        assert resolve_agent_loop_settings().max_rounds == 12
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_logging_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "logging": {
            "truncate_enabled": true,
            "truncate_limit": 2048
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.logging.truncate_enabled is True
        assert settings.logging.truncate_limit == 2048
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_session_memory_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "session_memory": {
            "trim_enabled": false,
            "max_messages": 128
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.session_memory.trim_enabled is False
        assert settings.session_memory.max_messages == 128
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_read_lsp_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "lsp": {
            "enabled": true,
            "server_idle_ttl_seconds": 123,
            "request_timeout_ms": 4567,
            "max_diagnostics": 9,
            "max_chars": 1024,
            "include_severity": ["error", "warning", "information"],
            "strict_unavailable": true,
            "languages": {
              "java": {
                "enabled": true,
                "command": ["custom-jdtls"],
                "maven_local_repository": "/custom/maven/repository",
                "file_extensions": [".java"],
                "workspace_markers": ["pom.xml"],
                "init_options": {
                  "bundles": []
                }
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        settings = get_project_runtime_settings()
        assert settings.lsp.enabled is True
        assert settings.lsp.server_idle_ttl_seconds == 123
        assert settings.lsp.request_timeout_ms == 4567
        assert settings.lsp.max_diagnostics == 9
        assert settings.lsp.max_chars == 1024
        assert settings.lsp.include_severity == ("error", "warning", "information")
        assert settings.lsp.strict_unavailable is True
        assert settings.lsp.languages["java"].command == ("custom-jdtls",)
        assert settings.lsp.languages["java"].maven_local_repository == "/custom/maven/repository"
        assert settings.lsp.languages["java"].workspace_markers == ("pom.xml",)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_java_maven_profiles_override(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "lsp": {
            "languages": {
              "java": {
                "maven_profiles": ["hna"]
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        with pytest.raises(ValueError, match="maven_profiles 已废弃"):
            get_project_runtime_settings()
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_invalid_lsp_severity(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "lsp": {
            "include_severity": ["fatal"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 lsp.include_severity 配置抛出异常")
    except ValueError as exc:
        assert "lsp.include_severity" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_resolve_file_extraction_settings_should_merge_vendor_override(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "file_extraction": {
            "default": {
              "allowed_extensions": [".pdf", ".md"],
              "cleanup_mode": "async_delete"
            },
            "vendors": {
              "kimi": {
                "allowed_extensions": [".pdf"]
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        kimi_settings = resolve_file_extraction_settings("kimi")
        fallback_settings = resolve_file_extraction_settings("openai")
        assert kimi_settings.allowed_extensions == (".pdf",)
        assert kimi_settings.cleanup_mode == "async_delete"
        assert fallback_settings.allowed_extensions == (".pdf", ".md")
        assert fallback_settings.cleanup_mode == "async_delete"
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_negative_keep_recent(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "tool_result_keep_recent": -1
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 keep_recent 配置抛出异常")
    except ValueError as exc:
        assert "tool_result_keep_recent" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_non_positive_max_rounds(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "agent_loop": {
            "max_rounds": 0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 max_rounds 配置抛出异常")
    except ValueError as exc:
        assert "max_rounds" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_invalid_logging_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "logging": {
            "truncate_enabled": "yes",
            "truncate_limit": 0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 logging 配置抛出异常")
    except ValueError as exc:
        assert "logging.truncate_enabled" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_non_positive_logging_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "logging": {
            "truncate_enabled": true,
            "truncate_limit": 0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 logging.truncate_limit 配置抛出异常")
    except ValueError as exc:
        assert "logging.truncate_limit" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_invalid_session_memory_config(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "session_memory": {
            "trim_enabled": "yes",
            "max_messages": 0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 session_memory 配置抛出异常")
    except ValueError as exc:
        assert "session_memory.trim_enabled" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_get_project_runtime_settings_should_reject_non_positive_session_memory_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "session_memory": {
            "trim_enabled": true,
            "max_messages": 0
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        get_project_runtime_settings()
        raise AssertionError("期望非法 session_memory.max_messages 配置抛出异常")
    except ValueError as exc:
        assert "session_memory.max_messages" in str(exc)
    finally:
        clear_runtime_settings_cache()


def test_resolve_compaction_settings_should_merge_vendor_override(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "tool_result_prune_enabled": true,
              "tool_result_keep_recent": 3,
              "tool_result_prune_min_chars": 100,
              "summary_trigger_threshold": 50000,
              "summary_max_tokens": 2000,
              "tool_output_max_lines": 2000,
              "tool_output_max_bytes": 51200
            },
            "vendors": {
              "qwen": {
                "tool_result_keep_recent": 9,
                "summary_max_tokens": 777
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        resolved = resolve_compaction_settings("qwen")
        fallback = resolve_compaction_settings("openai")
        assert resolved.tool_result_keep_recent == 9
        assert resolved.summary_max_tokens == 777
        assert resolved.tool_result_prune_min_chars == 100
        assert fallback.tool_result_keep_recent == 3
        assert fallback.summary_max_tokens == 2000
    finally:
        clear_runtime_settings_cache()


def test_clear_runtime_settings_cache_should_clear_project_runtime_cache(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "tool_result_prune_enabled": true,
              "tool_result_keep_recent": 1
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        first = get_project_runtime_settings()
        assert first.compaction_default.tool_result_keep_recent == 1

        config_path.write_text(
            """
            {
              "compaction": {
                "default": {
                  "tool_result_prune_enabled": true,
                  "tool_result_keep_recent": 4
                }
              }
            }
            """.strip(),
            encoding="utf-8",
        )

        cached = get_project_runtime_settings()
        assert cached.compaction_default.tool_result_keep_recent == 1

        clear_runtime_settings_cache()
        refreshed = get_project_runtime_settings()
        assert refreshed.compaction_default.tool_result_keep_recent == 4
    finally:
        clear_runtime_settings_cache()


def test_build_default_session_memory_store_should_follow_project_runtime_settings(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "session_memory": {
            "trim_enabled": false,
            "max_messages": 2
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)

    try:
        store = session_module._build_default_session_memory_store()
        configure_session_memory_store(store)
        clear_session_memory("s_default_store")

        first_user = create_message("user", "s_default_store", status="completed")
        append_text_part(first_user, "第一问")
        second_user = create_message("user", "s_default_store", status="completed")
        append_text_part(second_user, "第二问")
        third_user = create_message("user", "s_default_store", status="completed")
        append_text_part(third_user, "第三问")

        session_module.SESSION_MEMORY_STORE.save("s_default_store", [first_user, second_user, third_user])
        loaded = session_module.SESSION_MEMORY_STORE.load("s_default_store")

        assert [get_message_text(message) for message in loaded] == ["第一问", "第二问", "第三问"]
    finally:
        configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
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


def test_compaction_summary_should_use_vendor_specific_max_tokens(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "compaction": {
            "default": {
              "summary_trigger_threshold": 1,
              "summary_max_tokens": 111
            },
            "vendors": {
              "qwen": {
                "summary_max_tokens": 456
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    seen = {}

    def fake_summary_chat(messages, tools, max_tokens=4096, hooks=None, llm_config=None, agent=""):
        del messages, tools, hooks, llm_config, agent
        seen["max_tokens"] = max_tokens
        assistant = create_message("assistant", "s_vendor_compact", status="completed")
        append_text_part(assistant, "压缩摘要")
        return assistant

    monkeypatch.setattr(compaction_module, "create_chat_completion", fake_summary_chat)
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    qwen_config = resolve_llm_config("build")
    user_message = create_message("user", "s_vendor_compact")
    append_text_part(user_message, "x" * 40)

    try:
        compaction_module.compaction_summary([user_message], llm_config=qwen_config, agent="build")
        assert seen["max_tokens"] == 456
    finally:
        clear_runtime_settings_cache()
