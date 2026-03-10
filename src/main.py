import json
import logging
import time
from pathlib import Path
from typing import Callable

from .compact import compact
from .client import create_chat_completion
from .ctx import get_session_id, set_session_id
from .message import (
    EVENT_BUS,
    Message,
    append_text_part,
    append_tool_result_part,
    create_message,
    extract_tool_calls,
    get_message_text,
)
from .skills_runtime import SkillRegistry
from .todo_manager import TodoManager
from .tool import (
    BASE_TOOL,
    MAIN_AGENT_TOOL,
    ToolHook,
    ToolHookContext,
    get_global_tool_hooks,
    invoke_tool_hook,
    normalize_tool_error,
    run_bash,
    run_edit,
    run_read,
    run_write,
)

logger = logging.getLogger(__name__)

# skills 注册与目录构建
registry = SkillRegistry("./src/skills")
registry.discover()
skills_catalog = registry.build_brief_catalog_for_model()

# 构建 system prompt
WORKDIR = Path.cwd()

SYSTEM = f"""
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

SUBAGENT_SYSTEM = f"""
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


def _on_message_completed(event: dict) -> None:
    payload = event.get("payload", {})
    logger.debug(
        "event.message_completed session_id=%s message_id=%s finish_reason=%s",
        event.get("session_id", ""),
        event.get("message_id", ""),
        payload.get("finish_reason", ""),
    )


EVENT_BUS.subscribe("message_completed", _on_message_completed)


def normalize_tool_result(result: object) -> str:
    """将工具返回值规范为字符串，避免非法结构导致模型接口报错。"""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def _build_text_message(role: str, content: str, session_id: str) -> Message:
    message = create_message(role=role, session_id=session_id)
    append_text_part(message, content)
    return message


def _build_tool_message(session_id: str, tool_call_id: str, tool_name: str, content: str) -> Message:
    message = create_message(role="tool", session_id=session_id)
    append_tool_result_part(
        message,
        tool_call_id=tool_call_id,
        name=tool_name,
        content=content,
    )
    return message


def subagent_loop(prompt: str, session_id: str | None = None) -> str:
    logger.info("subagent.start prompt_preview=%s", prompt[:120].replace("\n", "\\n"))
    result = run_session(
        user_input=prompt,
        session_id=session_id,
        tools=BASE_TOOL,
        system_prompt=SUBAGENT_SYSTEM,
        todo_tool_names={"todo_write", "todo_read"},
    )
    return get_message_text(result)


TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo_write": lambda **kw: TODO.update(kw["todo_list"]),
    "todo_read": lambda **kw: TODO.read_current_session(),
    "task": lambda **kw: subagent_loop(kw["prompt"], session_id=get_session_id()),
    "load_skill": lambda **kw: registry.build_skill_context(kw["skill_names"]),
}


def _run_tool_hooks(
    hooks: list[ToolHook],
    stage: str,
    *,
    ctx: ToolHookContext,
    result: str | None = None,
    error: Exception | None = None,
    error_code: str = "execution_error",
) -> None:
    normalized = normalize_tool_error(error, code=error_code) if error is not None else None
    for hook in hooks:
        invoke_tool_hook(
            hook,
            stage,
            ctx=ctx,
            result=result,
            error=error,
            normalized_error=normalized,
        )


def _execute_tool_call(
    tool_name: str,
    arguments: str,
    *,
    session_id: str,
    tool_call_id: str,
    round_no: int,
    hooks: list[ToolHook],
) -> str:
    ctx: ToolHookContext = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "arguments": arguments,
        "round_no": round_no,
    }

    started = time.perf_counter()
    ctx["started_at"] = started
    _run_tool_hooks(hooks, "before", ctx=ctx)

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
        err = ValueError("Unknown tool")
        _run_tool_hooks(hooks, "error", ctx=ctx, error=err, error_code="unknown_tool")
        return "Error: Unknown tool"

    try:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a JSON object")
        ctx["parsed_args"] = args
    except Exception as exc:
        ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
        _run_tool_hooks(hooks, "error", ctx=ctx, error=exc, error_code="invalid_arguments")
        return f"Error: Invalid tool arguments: {type(exc).__name__}: {exc}"

    try:
        result = normalize_tool_result(handler(**args))
    except Exception as exc:
        ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
        _run_tool_hooks(hooks, "error", ctx=ctx, error=exc, error_code="execution_error")
        return f"Error: Tool execution failed: {type(exc).__name__}: {exc}"

    ctx["duration_ms"] = int((time.perf_counter() - started) * 1000)
    ctx["result_size"] = len(result)
    _run_tool_hooks(hooks, "after", ctx=ctx, result=result)
    return result


def run_session(
    user_input: str,
    session_id: str | None = None,
    *,
    tools: list[dict] | None = None,
    system_prompt: str = SYSTEM,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
) -> Message:
    """新会话入口：返回最终助手 Message（含结构化 parts）。"""
    active_session_id = set_session_id(session_id)
    selected_tools = tools if tools is not None else MAIN_AGENT_TOOL
    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])

    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

    messages: list[Message] = [
        _build_text_message("system", system_prompt, active_session_id),
        _build_text_message("user", user_input, active_session_id),
    ]

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

        assistant_message = create_chat_completion(messages=messages, tools=selected_tools)
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
            logger.info(
                "session.round.finish session_id=%s round=%d status=%s",
                active_session_id,
                round_no,
                assistant_message["info"].get("status", "unknown"),
            )
            return assistant_message

        for tool_call in tool_calls:
            result = _execute_tool_call(
                tool_call["name"],
                tool_call["arguments"],
                session_id=active_session_id,
                tool_call_id=tool_call["id"],
                round_no=round_no,
                hooks=effective_tool_hooks,
            )

            tool_message = _build_tool_message(
                active_session_id,
                tool_call_id=tool_call["id"],
                tool_name=tool_call["name"],
                content=result,
            )
            messages.append(tool_message)

        if non_todo_round_streak >= 3:
            messages.append(_build_text_message("user", todo_reminder_text, active_session_id))


def agent_loop(user_input: str, session_id: str | None = None) -> Message:
    """兼容入口：内部转发到新接口。"""
    return run_session(user_input=user_input, session_id=session_id)


if __name__ == "__main__":
    result = run_session(
        """
查看当前目录下有哪些文件
""",
        "test-session-123",
    )
    print("最终结果：")
    print(get_message_text(result))
