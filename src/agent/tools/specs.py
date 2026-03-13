from pathlib import Path
from typing import Any

from ..runtime.agents import get_subagents

TODO_DESC_FILE = Path(__file__).with_name("todo_write.txt")
TODO_TOOL_DESCRIPTION = TODO_DESC_FILE.read_text().strip()
PLAN_ENTER_DESC_FILE = Path(__file__).with_name("plan_enter.txt")
PLAN_ENTER_TOOL_DESCRIPTION = PLAN_ENTER_DESC_FILE.read_text().strip()
PLAN_EXIT_DESC_FILE = Path(__file__).with_name("plan_exit.txt")
PLAN_EXIT_TOOL_DESCRIPTION = PLAN_EXIT_DESC_FILE.read_text().strip()
TASK_DESC_FILE = Path(__file__).with_name("task.txt")
WEBFETCH_DESC_FILE = Path(__file__).with_name("webfetch.txt")
WEBFETCH_TOOL_DESCRIPTION = WEBFETCH_DESC_FILE.read_text().strip()
WEBSEARCH_DESC_FILE = Path(__file__).with_name("websearch.txt")
WEBSEARCH_TOOL_DESCRIPTION = WEBSEARCH_DESC_FILE.read_text().strip()

def _build_subagent_listing() -> str:
    subagents = get_subagents()
    if not subagents:
        return "- 当前未注册可用 subagent。"
    return "\n".join(f"- {agent.name}: {agent.description}" for agent in subagents)


def _build_task_tool_description() -> str:
    template = TASK_DESC_FILE.read_text(encoding="utf-8").strip()
    return template.format(agents=_build_subagent_listing())


def build_base_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents. Supports offset and limit for chunked reading.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace exact text in file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "webfetch",
                "description": WEBFETCH_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "format": {"type": "string", "enum": ["text", "markdown", "html"]},
                        "timeout": {"type": "number"},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "websearch",
                "description": WEBSEARCH_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "numResults": {"type": "integer"},
                        "livecrawl": {"type": "string", "enum": ["fallback", "preferred"]},
                        "type": {"type": "string", "enum": ["auto", "fast", "deep"]},
                        "contextMaxCharacters": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_write",
                "description": TODO_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todo_list": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "text": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                                    },
                                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                                },
                                "required": ["text", "status", "priority"],
                            },
                        }
                    },
                    "required": ["todo_list"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_read",
                "description": "使用这个工具来阅读你的待办事项清单。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "加载一个或多个 skill 的完整内容。当你需要查看某个 skill 的详细说明时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要加载的 skill 名称列表",
                        }
                    },
                    "required": ["skill_names"],
                },
            },
        },
    ]


def build_task_tool() -> dict[str, Any]:
    subagent_names = [agent.name for agent in get_subagents()]
    agent_description = "要调用的子代理名称。可选子代理：" + (
        "、".join(subagent_names) if subagent_names else "当前无"
    )
    agent_schema: dict[str, Any] = {
        "type": "string",
        "description": agent_description,
        "default": subagent_names[0] if subagent_names else "explore",
    }
    if subagent_names:
        agent_schema["enum"] = subagent_names

    return {
        "type": "function",
        "function": {
            "name": "task",
            "description": _build_task_tool_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "发给子代理的完整任务说明，包含目标、上下文、约束条件和期望输出。",
                    },
                    "agent": agent_schema,
                },
                "required": ["prompt"],
            },
        },
    }


def build_plan_enter_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "plan_enter",
            "description": PLAN_ENTER_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed": {
                        "type": "boolean",
                        "description": "是否确认切换。true 表示用户已同意切换。",
                    }
                },
            },
        },
    }


def build_plan_exit_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "plan_exit",
            "description": PLAN_EXIT_TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed": {
                        "type": "boolean",
                        "description": "是否已获得用户确认。true 才会执行退出。",
                    }
                },
            },
        },
    }


def build_agent_tools(mode: str) -> list[dict[str, Any]]:
    base_tools = build_base_tools()
    task_tool = build_task_tool()
    if (mode or "").strip().lower() == "plan":
        return base_tools + [task_tool, build_plan_exit_tool()]
    return base_tools + [task_tool, build_plan_enter_tool()]
