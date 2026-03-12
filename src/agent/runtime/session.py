import logging
from pathlib import Path
from typing import Any, Callable, Literal

from ..adapters.llm.client import create_chat_completion
from ..core.context import set_session_id
from ..core.message import (
    EVENT_BUS,
    Message,
    append_tool_part,
    append_text_part,
    create_message,
    extract_tool_calls,
    get_role,
    get_message_text,
    utc_now_iso,
)
from ..skills.runtime import SkillRegistry
from ..tools.handlers import (
    build_plan_placeholder_path,
    is_allowed_plan_write_path,
    run_bash,
    run_edit,
    run_plan_enter,
    run_plan_exit,
    run_read,
    run_write,
    validate_readonly_bash,
)
from ..tools.specs import BASE_TOOL, BUILD_AGENT_TOOL, PLAN_AGENT_TOOL
from ..tools.todo_manager import TodoManager
from .compaction import compact
from .session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from .tool_executor import ToolExecutor, ToolHook, ToolResult, get_global_tool_hooks

logger = logging.getLogger(__name__)

MainAgentMode = Literal["build", "plan"]

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
registry = SkillRegistry(SKILLS_ROOT)
registry.discover()
skills_catalog = registry.build_brief_catalog_for_model()

WORKDIR = Path.cwd()

BUILD_SYSTEM = f"""
You are a coding agent at {WORKDIR}.

使用待办事项工具来规划多步骤任务。开始前标记为“in_progress”，完成后标记为“completed”。

你可以看到一个 skills catalog，里面只有每个 skill 的简短介绍。
当用户的问题需要某个专业 skill 时，你不要瞎猜 skill 的细节，
而是应该调用工具去加载对应的 skill。

规则：
1. 如果现有上下文已经足够回答，就直接回答。
2. 如果你判断某个 skill 会显著提高回答质量，就调用工具 load_skill。
3. 不要假装已经看过某个 skill 的完整内容，除非你真的调用过工具。
4. 可以一次加载一个或多个 skill，但尽量克制，只加载必要的。

当前可用 skills catalog:\n{skills_catalog}

优先使用工具而非文字描述。
"""

PLAN_SYSTEM = f"""
You are a coding planning agent at {WORKDIR}.

你当前处于 plan 模式，目标是澄清需求、拆解任务、组织执行计划，并在必要时委托 explore 子代理收集信息。
使用待办事项工具来规划多步骤任务。开始前标记为“in_progress”，完成后标记为“completed”。

关键限制：
1. 允许写入和编辑 `src/plan/` 下的文件。
2. 禁止写入和编辑 `src/plan/` 之外的文件。
3. bash 仅允许只读命令，禁止链式执行、重定向和命令替换。
4. 退出 plan 模式时，先调用 plan_exit 并等待用户确认。

你可以看到一个 skills catalog，里面只有每个 skill 的简短介绍。
当用户的问题需要某个专业 skill 时，你不要瞎猜 skill 的细节，
而是应该调用工具去加载对应的 skill。

规则：
1. 如果现有上下文已经足够回答，就直接回答。
2. 如果你判断某个 skill 会显著提高回答质量，就调用工具 load_skill。
3. 不要假装已经看过某个 skill 的完整内容，除非你真的调用过工具。
4. 可以一次加载一个或多个 skill，但尽量克制，只加载必要的。

当前可用 skills catalog:\n{skills_catalog}

优先使用工具而非文字描述。
"""

SUBAGENT_EXPLORE_SYSTEM = f"""
You are a coding subagent at {WORKDIR}.
完成给定的任务，然后总结你的发现。
使用待办事项工具来规划多步骤任务。开始前标记为“in_progress”，完成后标记为“completed”。

你可以看到一个 skills catalog，里面只有每个 skill 的简短介绍。
当用户的问题需要某个专业 skill 时，你不要瞎猜 skill 的细节，
而是应该调用工具去加载对应的 skill。

规则：
1. 如果现有上下文已经足够回答，就直接回答。
2. 如果你判断某个 skill 会显著提高回答质量，就调用工具 load_skill。
3. 不要假装已经看过某个 skill 的完整内容，除非你真的调用过工具。
4. 可以一次加载一个或多个 skill，但尽量克制，只加载必要的。

当前可用 skills catalog:\n{skills_catalog}

优先使用工具而非文字描述。
"""

TODO = TodoManager()
SESSION_MEMORY_STORE: SessionMemoryStore = InMemorySessionMemoryStore(max_messages=24)


def _get_system_prompt_for_mode(mode: MainAgentMode) -> str:
    return PLAN_SYSTEM if mode == "plan" else BUILD_SYSTEM


def _get_tools_for_mode(mode: MainAgentMode) -> list[dict]:
    return PLAN_AGENT_TOOL if mode == "plan" else BUILD_AGENT_TOOL


def _ensure_system_prompt(messages: list[Message], prompt: str, session_id: str) -> list[Message]:
    system_msg = _build_text_message("system", prompt, session_id)
    if messages and get_role(messages[0]) == "system":
        messages[0] = system_msg
        return messages
    return [system_msg, *messages]


def _latest_model(messages: list[Message]) -> str:
    for msg in reversed(messages):
        model = str(msg["info"].get("model", "")).strip()
        if model:
            return model
    return ""


def _resolve_mode_from_messages(messages: list[Message], fallback: MainAgentMode = "build") -> MainAgentMode:
    for msg in reversed(messages):
        if get_role(msg) != "user":
            continue
        for part in msg["parts"]:
            if part.get("type") != "text":
                continue
            meta = part.get("meta") or {}
            if not isinstance(meta, dict):
                continue
            agent = str(meta.get("agent", "")).strip().lower()
            if agent in {"build", "plan"}:
                return agent  # type: ignore[return-value]
    return fallback


def _build_confirmation_interrupted_message(
    session_id: str,
    *,
    tool_name: str,
    output_text: str,
    question: str,
    metadata: dict[str, Any],
) -> Message:
    message = create_message(
        role="assistant",
        session_id=session_id,
        status="interrupted",
        finish_reason="confirmation_required",
    )
    text = f"{output_text}\n{question}".strip()
    append_text_part(
        message,
        text,
        meta={
            "tool": tool_name,
            "confirmation_required": True,
            "tool_metadata": metadata,
        },
    )
    return message


def _append_synthetic_user_message(
    messages: list[Message],
    *,
    session_id: str,
    agent: MainAgentMode,
    text: str,
    plan_path: str,
    model: str,
) -> None:
    synthetic = _build_text_message(
        "user",
        text,
        session_id,
        model=model,
        text_meta={
            "agent": agent,
            "synthetic": True,
            "plan_path": plan_path,
            "model": model,
        },
    )
    messages.append(synthetic)


def _on_message_completed(event: dict) -> None:
    payload = event.get("payload", {})
    logger.debug(
        "event.message_completed session_id=%s message_id=%s finish_reason=%s",
        event.get("session_id", ""),
        event.get("message_id", ""),
        payload.get("finish_reason", ""),
    )


EVENT_BUS.subscribe("message_completed", _on_message_completed)


def _build_text_message(
    role: str,
    content: str,
    session_id: str,
    *,
    model: str = "",
    text_meta: dict[str, Any] | None = None,
) -> Message:
    message = create_message(role=role, session_id=session_id, model=model)
    append_text_part(message, content, meta=text_meta)
    return message


def _set_message_runtime_info(
    message: Message,
    *,
    agent: str,
    turn_started_at: str,
    turn_completed_at: str | None = None,
) -> None:
    message["info"]["agent"] = agent
    message["info"]["turn_started_at"] = turn_started_at
    if turn_completed_at:
        message["info"]["turn_completed_at"] = turn_completed_at


def _build_tool_message(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: str,
    result: ToolResult,
    *,
    agent: str,
    turn_started_at: str,
) -> Message:
    message = create_message(role="tool", session_id=session_id)
    status = str((result.get("metadata") or {}).get("status", "completed")).strip().lower()
    if status not in {"completed", "failed"}:
        status = "completed"
    append_tool_part(
        message,
        tool_call_id=tool_call_id,
        name=tool_name,
        status=status,  # type: ignore[arg-type]
        arguments=arguments,
        output=result,
    )
    _set_message_runtime_info(
        message,
        agent=agent,
        turn_started_at=turn_started_at,
        turn_completed_at=utc_now_iso(),
    )
    return message


def configure_session_memory_store(store: SessionMemoryStore) -> None:
    """配置会话记忆存储实现，便于替换为 Redis 等后端。"""
    global SESSION_MEMORY_STORE
    SESSION_MEMORY_STORE = store


def clear_session_memory(session_id: str | None = None) -> None:
    SESSION_MEMORY_STORE.clear(session_id)


def subagent_loop(prompt: str, agent: str = "explore", session_id: str | None = None) -> str:
    agent_name = (agent or "explore").strip().lower()
    if agent_name != "explore":
        return f"Error: Unknown subagent '{agent_name}'. 当前仅支持 explore。"

    logger.info("subagent.start agent=%s prompt_preview=%s", agent_name, prompt[:120].replace("\n", "\\n"))
    result = run_session(
        user_input=prompt,
        session_id=session_id,
        tools=BASE_TOOL,
        system_prompt=SUBAGENT_EXPLORE_SYSTEM,
        todo_tool_names={"todo_write", "todo_read"},
    )
    return get_message_text(result)


def _build_tool_handlers(
    *,
    session_id: str,
    get_mode: Callable[[], MainAgentMode],
    get_latest_model: Callable[[], str],
) -> dict[str, Callable[..., object]]:
    def _normalize_confirmed(raw_args: dict[str, Any]) -> bool | None:
        if "confirmed" not in raw_args:
            return None
        return bool(raw_args.get("confirmed"))

    def _run_mode_aware_bash(command: str) -> str:
        if get_mode() == "plan":
            validation_error = validate_readonly_bash(command)
            if validation_error is not None:
                return validation_error
        return run_bash(command)

    def _run_mode_aware_write(path: str, content: str) -> str:
        if get_mode() == "plan" and not is_allowed_plan_write_path(path):
            return "Error: plan 模式下仅允许写入 src/plan 目录。"
        return run_write(path, content)

    def _run_mode_aware_edit(path: str, old_text: str, new_text: str) -> str:
        if get_mode() == "plan" and not is_allowed_plan_write_path(path):
            return "Error: plan 模式下仅允许编辑 src/plan 目录。"
        return run_edit(path, old_text, new_text)

    def _run_plan_enter_tool(**kw: Any) -> dict[str, Any]:
        plan_path = str(build_plan_placeholder_path(session_id))
        return run_plan_enter(
            current_mode=get_mode(),
            plan_path=plan_path,
            plan_exists=Path(plan_path).exists(),
            latest_model=get_latest_model(),
            confirmed=_normalize_confirmed(kw),
        )

    def _run_plan_exit_tool(**kw: Any) -> dict[str, Any]:
        plan_path = str(build_plan_placeholder_path(session_id))
        return run_plan_exit(
            current_mode=get_mode(),
            plan_path=plan_path,
            plan_exists=Path(plan_path).exists(),
            latest_model=get_latest_model(),
            confirmed=_normalize_confirmed(kw),
        )

    return {
        "bash": lambda **kw: _run_mode_aware_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: _run_mode_aware_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: _run_mode_aware_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "todo_write": lambda **kw: TODO.update(kw["todo_list"]),
        "todo_read": lambda **kw: TODO.read_current_session(),
        "task": lambda **kw: subagent_loop(kw["prompt"], agent=kw.get("agent", "explore"), session_id=session_id),
        "plan_enter": lambda **kw: _run_plan_enter_tool(**kw),
        "plan_exit": lambda **kw: _run_plan_exit_tool(**kw),
        "load_skill": lambda **kw: registry.build_skill_context(kw["skill_names"]),
    }


def run_session(
    user_input: str,
    session_id: str | None = None,
    *,
    mode: MainAgentMode | None = None,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
) -> Message:
    """新会话入口：返回最终助手 Message（含结构化 parts）。"""
    active_session_id = set_session_id(session_id)
    turn_started_at = utc_now_iso()
    mode_enabled = tools is None and system_prompt is None

    initial_mode: MainAgentMode = "build"
    if mode in {"build", "plan"}:
        initial_mode = mode

    history_messages: list[Message] = SESSION_MEMORY_STORE.load(active_session_id) if mode_enabled else []
    if mode is None and mode_enabled and history_messages:
        initial_mode = _resolve_mode_from_messages(history_messages, fallback=initial_mode)

    initial_tools = _get_tools_for_mode(initial_mode) if mode_enabled else (tools if tools is not None else BUILD_AGENT_TOOL)
    initial_system_prompt = _get_system_prompt_for_mode(initial_mode) if mode_enabled else (system_prompt or BUILD_SYSTEM)

    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])

    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

    user_meta: dict[str, Any] | None = None
    if mode_enabled:
        user_meta = {"agent": initial_mode}

    messages: list[Message] = [
        _build_text_message("system", initial_system_prompt, active_session_id),
        *history_messages,
        _build_text_message("user", user_input, active_session_id, text_meta=user_meta),
    ]

    current_mode: MainAgentMode = initial_mode

    tool_executor = ToolExecutor(
        _build_tool_handlers(
            session_id=active_session_id,
            get_mode=lambda: current_mode,
            get_latest_model=lambda: _latest_model(messages),
        )
    )

    round_no = 0
    while True:
        round_no += 1
        messages = compact(messages)

        logger.debug(
            "session.round.start session_id=%s round=%d message_count=%d",
            active_session_id,
            round_no,
            len(messages),
        )

        selected_tools = initial_tools
        if mode_enabled:
            current_mode = _resolve_mode_from_messages(messages, fallback=initial_mode)
            selected_tools = _get_tools_for_mode(current_mode)
            messages = _ensure_system_prompt(messages, _get_system_prompt_for_mode(current_mode), active_session_id)
        active_agent = current_mode if mode_enabled else ("explore" if system_prompt == SUBAGENT_EXPLORE_SYSTEM else "build")

        assistant_message = create_chat_completion(messages=messages, tools=selected_tools)
        _set_message_runtime_info(assistant_message, agent=active_agent, turn_started_at=turn_started_at)
        messages.append(assistant_message)

        tool_calls = extract_tool_calls(assistant_message)
        has_tool_calls = bool(tool_calls)

        if has_tool_calls:
            has_todo_call = any(tc["name"] in todo_names for tc in tool_calls)
            if has_todo_call:
                non_todo_round_streak = 0
            else:
                non_todo_round_streak += 1

        if not has_tool_calls:
            assistant_message["info"]["turn_completed_at"] = utc_now_iso()
            logger.info(
                "session.round.finish session_id=%s round=%d status=%s",
                active_session_id,
                round_no,
                assistant_message["info"].get("status", "unknown"),
            )
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return assistant_message

        should_interrupt = False
        for tool_call in tool_calls:
            result = tool_executor.execute(
                tool_call["name"],
                tool_call["arguments"],
                session_id=active_session_id,
                tool_call_id=tool_call["id"],
                round_no=round_no,
                hooks=effective_tool_hooks,
            )
            messages.append(
                _build_tool_message(
                    active_session_id,
                    tool_call_id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["arguments"],
                    result=result,
                    agent=active_agent,
                    turn_started_at=turn_started_at,
                )
            )

            if tool_call["name"] not in {"plan_enter", "plan_exit"}:
                continue

            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            status = str(metadata.get("status", "")).strip().lower()

            if status == "confirmation_required":
                output_text = str(result.get("output", "请确认后继续。"))
                question = str(metadata.get("confirmation_question", "是否继续？"))
                interrupted_message = _build_confirmation_interrupted_message(
                    active_session_id,
                    tool_name=tool_call["name"],
                    output_text=output_text,
                    question=question,
                    metadata=metadata,
                )
                _set_message_runtime_info(
                    interrupted_message,
                    agent=active_agent,
                    turn_started_at=turn_started_at,
                    turn_completed_at=utc_now_iso(),
                )
                messages.append(interrupted_message)
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
                return interrupted_message

            if status == "switched":
                synthetic_agent = str(metadata.get("synthetic_agent", "")).strip().lower()
                synthetic_text = str(metadata.get("synthetic_user_message", "")).strip()
                plan_path = str(metadata.get("plan_path", "")).strip()
                model_name = str(metadata.get("model", "")).strip()

                if synthetic_agent in {"build", "plan"} and synthetic_text:
                    _append_synthetic_user_message(
                        messages,
                        session_id=active_session_id,
                        agent=synthetic_agent,  # type: ignore[arg-type]
                        text=synthetic_text,
                        plan_path=plan_path,
                        model=model_name,
                    )

            if status == "cancelled":
                should_interrupt = True

        if should_interrupt:
            message = create_message(
                role="assistant",
                session_id=active_session_id,
                status="interrupted",
                finish_reason="cancelled",
            )
            append_text_part(message, "用户取消了模式切换，当前流程已中断。")
            _set_message_runtime_info(
                message,
                agent=active_agent,
                turn_started_at=turn_started_at,
                turn_completed_at=utc_now_iso(),
            )
            messages.append(message)
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return message

        if non_todo_round_streak >= 3:
            messages.append(_build_text_message("user", todo_reminder_text, active_session_id))


def agent_loop(user_input: str, session_id: str | None = None) -> Message:
    """兼容入口：内部转发到新接口。"""
    return run_session(user_input=user_input, session_id=session_id)
