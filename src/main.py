import json
from pathlib import Path

from .compact import compact
from .client import create_chat_completion

from .skills_runtime import SkillRegistry

from .ctx import get_session_id, set_session_id
from .todo_manager import TodoManager

from .tool import MAIN_AGENT_TOOL, BASE_TOOL, run_bash, run_edit, run_read, run_write

# skills 注册与目录构建
registry = SkillRegistry("./src/skills")
registry.discover()
skills_catalog = registry.build_brief_catalog_for_model()

# 构建 system prompt
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"

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

# 工具函数与处理器
TODO = TodoManager()

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo_write": lambda **kw: TODO.update(kw["todo_list"]),
    "todo_read":  lambda **kw: TODO.read_current_session(),
    "task": lambda **kw: subagent_loop(kw["prompt"], session_id=get_session_id()),
    "load_skill": lambda **kw: registry.build_skill_context(kw["skill_names"])
}

def normalize_tool_result(result) -> str:
    """将工具返回值规范为字符串，避免非法 content 结构导致接口报错。"""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def subagent_loop(prompt: str, session_id: str = None) -> str:
    print(f"Subagent received task: {prompt}")
    if session_id is not None:
        set_session_id(session_id)

    todo_tool_name = "todo"
    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

    messages = [
        {
            "role": "system",
            "content": SUBAGENT_SYSTEM
        },
        {
            "role": "user",
            "content": prompt
        }
    ]
    while True:
        compact(messages)  
        response = create_chat_completion(
            messages=messages,
            tools=BASE_TOOL,
        )
        message = response.choices[0].message
        is_tool_calls = bool(getattr(message, "tool_calls", None))
        has_todo_call_in_round = False

        if is_tool_calls:
            has_todo_call_in_round = any(
                tc.function.name == todo_tool_name for tc in (message.tool_calls or [])
            )
            if has_todo_call_in_round:
                non_todo_round_streak = 0
            else:
                non_todo_round_streak += 1

        # 追加助手消息
        assistant_message = {
            "role": "assistant",
            "content": message.content
        }
        # 如果有工具调用，附加工具调用信息
        if is_tool_calls:
            assistant_message["tool_calls"] = []
            for tc in message.tool_calls or []:
                assistant_message["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        messages.append(assistant_message)


        # 如果不是工具调用，代表结束了
        if not is_tool_calls:
            return message.content or ""
        
        # 工具调用
        for tool_call in message.tool_calls or []:
            handler = TOOL_HANDLERS.get(tool_call.function.name)
            if handler:
                try:
                    args = json.loads(tool_call.function.arguments)
                except Exception as e:
                    result = f"Error: Invalid tool arguments: {type(e).__name__}: {e}"
                else:
                    try:
                        result = handler(**args)
                    except Exception as e:
                        result = f"Error: Tool execution failed: {type(e).__name__}: {e}"
            else:
                result = "Error: Unknown tool"
            # 工具调用结果封装为 message
            tool_message = {
                "role": "tool",
                "content": normalize_tool_result(result),
                "tool_call_id": tool_call.id
            }
            messages.append(tool_message)

        # 连续多轮未使用 todo 工具时，追加提醒消息。
        if non_todo_round_streak >= 3:
            messages.append(
                {
                    "role": "user",
                    "content": todo_reminder_text,
                }
            )

def agent_loop(user_input: str, session_id: str = None):
    set_session_id(session_id)

    todo_tool_name = ["todo_write", "todo_read"]
    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

    messages = [
        {
            "role": "system",
            "content": SYSTEM
        },
        {
            "role": "user",
            "content": user_input
        }
    ]
    while True:
        compact(messages) 
        response = create_chat_completion(
            messages=messages,
            tools=MAIN_AGENT_TOOL,
        )
        message = response.choices[0].message
        is_tool_calls = bool(getattr(message, "tool_calls", None))
        has_todo_call_in_round = False

        if is_tool_calls:
            has_todo_call_in_round = any(
                tc.function.name in todo_tool_name for tc in (message.tool_calls or [])
            )
            if has_todo_call_in_round:
                non_todo_round_streak = 0
            else:
                non_todo_round_streak += 1

        # 追加助手消息
        assistant_message = {
            "role": "assistant",
            "content": message.content
        }
        # 如果有工具调用，附加工具调用信息
        if is_tool_calls:
            assistant_message["tool_calls"] = []
            for tc in message.tool_calls or []:
                assistant_message["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        messages.append(assistant_message)


        # 如果不是工具调用，代表结束了
        if not is_tool_calls:
            return messages
        
        # 工具调用
        for tool_call in message.tool_calls or []:
            handler = TOOL_HANDLERS.get(tool_call.function.name)
            if handler:
                try:
                    args = json.loads(tool_call.function.arguments)
                except Exception as e:
                    result = f"Error: Invalid tool arguments: {type(e).__name__}: {e}"
                else:
                    try:
                        result = handler(**args)
                    except Exception as e:
                        result = f"Error: Tool execution failed: {type(e).__name__}: {e}"
            else:
                result = "Error: Unknown tool"
            # 工具调用结果封装为 message
            tool_message = {
                "role": "tool",
                "content": normalize_tool_result(result),
                "tool_call_id": tool_call.id
            }
            messages.append(tool_message)

        # 连续多轮未使用 todo 工具时，追加提醒消息。
        if non_todo_round_streak >= 3:
            messages.append(
                {
                    "role": "user",
                    "content": todo_reminder_text,
                }
            )


if __name__ == "__main__":
    result = agent_loop("""
你有哪些工具
""",
"test-session-123"
    )
    print("最终结果：")
    print(result)
