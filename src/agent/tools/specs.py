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


def _build_load_skill_tool_description(skills: list[dict[str, Any]] | None = None) -> str:
    normalized_skills = skills or []
    if not normalized_skills:
        return "加载一个 skill，以获取完成某个特定任务的详细指导。目前没有可用的 skills。"

    skill_lines: list[str] = []
    for skill in normalized_skills:
        name = str(skill.get("name", "")).strip()
        description = str(skill.get("description", "")).strip()
        if not name:
            continue
        # 这里仅暴露技能路由所需信息，避免把本地实现路径泄露给模型。
        skill_lines.extend(
            [
                "    <skill>",
                f"        <name>{name}</name>",
                f"        <description>{description}</description>",
                "    </skill>",
            ]
        )

    if not skill_lines:
        return "加载一个 skill，以获取完成某个特定任务的详细指导。目前没有可用的 skills。"

    return "\n".join(
        [
            "加载一个 skill，以获取完成某个特定任务的详细指导。",
            "Skills 提供专门的知识和分步骤的指导。",
            "当某个任务与某个 skill 的描述相匹配时，应使用它。",
            "这里只列出了当前可用的 skills：",
            "<available_skills>",
            *skill_lines,
            "</available_skills>",
        ]
    )

def _build_subagent_listing() -> str:
    subagents = get_subagents()
    if not subagents:
        return "- 当前未注册可用 subagent。"
    return "\n".join(f"- {agent.name}: {agent.description}" for agent in subagents)


def _build_task_tool_description() -> str:
    template = TASK_DESC_FILE.read_text(encoding="utf-8").strip()
    # task 模板里可能包含示例代码的花括号，使用定点替换避免被 str.format 误解析。
    return template.replace("{agents}", _build_subagent_listing())


def build_base_tools(skills: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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
                "description": "Read file contents. Supports offset and limit for chunked reading. Reading a PDF returns a PDF attachment for Responses-compatible providers.",
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
                "description": _build_load_skill_tool_description(skills),
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
                "properties": {},
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
                "properties": {},
            },
        },
    }


def build_agent_tools(mode: str, skills: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    base_tools = build_base_tools(skills)
    task_tool = build_task_tool()
    if (mode or "").strip().lower() == "plan":
        return base_tools + [task_tool, build_plan_exit_tool()]
    return base_tools + [task_tool, build_plan_enter_tool()]
