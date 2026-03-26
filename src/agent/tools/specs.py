from pathlib import Path
from typing import Any

from ..config.settings import get_project_runtime_settings
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
BASH_DESC_FILE = Path(__file__).with_name("bash.txt")
READ_FILE_DESC_FILE = Path(__file__).with_name("read_file.txt")
READ_FILE_TOOL_DESCRIPTION = READ_FILE_DESC_FILE.read_text(encoding="utf-8").strip()
GLOB_DESC_FILE = Path(__file__).with_name("glob.txt")
GLOB_TOOL_DESCRIPTION = GLOB_DESC_FILE.read_text(encoding="utf-8").strip()
GREP_DESC_FILE = Path(__file__).with_name("grep.txt")
GREP_TOOL_DESCRIPTION = GREP_DESC_FILE.read_text(encoding="utf-8").strip()
EDIT_FILE_DESC_FILE = Path(__file__).with_name("edit_file.txt")
EDIT_FILE_TOOL_DESCRIPTION = EDIT_FILE_DESC_FILE.read_text(encoding="utf-8").strip()
WRITE_FILE_DESC_FILE = Path(__file__).with_name("write_file.txt")
WRITE_FILE_TOOL_DESCRIPTION = WRITE_FILE_DESC_FILE.read_text(encoding="utf-8").strip()
QUESTION_DESC_FILE = Path(__file__).with_name("question.txt")
QUESTION_TOOL_DESCRIPTION = QUESTION_DESC_FILE.read_text(encoding="utf-8").strip()
LOAD_SKILL_DESC_FILE = Path(__file__).with_name("load_skill.txt")


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

    template = LOAD_SKILL_DESC_FILE.read_text(encoding="utf-8").strip()
    return template.replace("{available_skills}", "\n".join(skill_lines))

def _build_subagent_listing() -> str:
    subagents = get_subagents()
    if not subagents:
        return "- 当前未注册可用 subagent。"
    return "\n".join(f"- {agent.name}: {agent.description}" for agent in subagents)


def _build_task_tool_description() -> str:
    template = TASK_DESC_FILE.read_text(encoding="utf-8").strip()
    # task 模板里可能包含示例代码的花括号，使用定点替换避免被 str.format 误解析。
    return template.replace("{agents}", _build_subagent_listing())


def _build_bash_tool_description() -> str:
    template = BASH_DESC_FILE.read_text(encoding="utf-8").strip()
    compaction_settings = get_project_runtime_settings().compaction_default
    return (
        template.replace("${directory}", "当前工作区根目录")
        .replace("${maxLines}", str(compaction_settings.tool_output_max_lines))
        .replace("${maxBytes}", str(compaction_settings.tool_output_max_bytes))
    )


def build_base_tools(skills: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": _build_bash_tool_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command to execute",
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Optional timeout in milliseconds",
                        },
                        "workdir": {
                            "type": "string",
                            "description": (
                                "The working directory to run the command in. "
                                "Defaults to ${当前工作目录}. Use this instead of 'cd' commands."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Clear, concise description of what this command does in 5-10 words. "
                                "Examples:\n"
                                "Input: ls\n"
                                "Output: Lists files in current directory\n\n"
                                "Input: git status\n"
                                "Output: Shows working tree status\n\n"
                                "Input: npm install\n"
                                "Output: Installs package dependencies\n\n"
                                "Input: mkdir foo\n"
                                "Output: Creates directory 'foo'"
                            ),
                        },
                    },
                    "required": ["command", "description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": GLOB_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "用于匹配文件的 glob 模式",
                        },
                        "path": {
                            "type": "string",
                            "description": "要搜索的目录。若未指定，将使用当前工作目录。重要提示：如需使用默认目录，请留空此字段。切勿输入`undefined`或`null` - 默认行为应通过直接留空字段实现。若填写则必须为有效的目录路径。",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": GREP_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "用于在文件内容中搜索的正则表达式模式。",
                        },
                        "path": {
                            "type": "string",
                            "description": "要搜索的目录，默认为当前工作目录。",
                        },
                        "include": {
                            "type": "array",
                            "description": "要包含在搜索中的文件模式（例如 '.js'、'.{ts,tsx}'）。",
                            "items": {
                                "type": "string",
                            },
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": READ_FILE_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "读取文件的绝对路径",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "可选，最多返回多少行文本",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "可选，从第几行开始读取（从 0 开始）",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": WRITE_FILE_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filePath": {
                            "type": "string",
                            "description": "要写入的文件的绝对路径（必须是绝对路径，而非相对路径）。",
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入文件的内容。",
                        },
                    },
                    "required": ["filePath", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": EDIT_FILE_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filePath": {"type": "string", "description": "要修改的文件的绝对路径。"},
                        "oldString": {"type": "string", "description": "要替换的文本。"},
                        "newString": {"type": "string", "description": "用于替换的新文本（必须与原字符串不同）。"},
                        "replaceAll": {"type": "boolean", "description": "是否替换 oldString 的所有出现位置（默认为 false）。"},
                    },
                    "required": ["filePath", "oldString", "newString"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "question",
                "description": QUESTION_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "description": "要提出的问题",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {
                                        "type": "string",
                                        "description": "完整的问题",
                                    },
                                    "header": {
                                        "type": "string",
                                        "description": "简短标签（最多 30 个字符）",
                                    },
                                    "options": {
                                        "type": "array",
                                        "description": "可选选项",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {
                                                    "type": "string",
                                                    "description": "显示文本（1-5 个词，简洁）",
                                                },
                                                "description": {
                                                    "type": "string",
                                                    "description": "选项说明",
                                                },
                                            },
                                            "required": ["label", "description"],
                                        },
                                    },
                                    "multiple": {
                                        "type": "boolean",
                                        "description": "允许选择多个选项，默认 false。",
                                    },
                                    "custom": {
                                        "type": "boolean",
                                        "description": "是否自动追加“不是以上任何选项”兜底项，默认 true。",
                                    },
                                },
                                "required": ["question", "header", "options"],
                            },
                        },
                    },
                    "required": ["questions"],
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
                        "url": {"type": "string", "description": "要从中获取内容的 URL。"},
                        "format": {"type": "string", "enum": ["text", "markdown", "html"], "description": "返回内容的格式（text、markdown 或 html），默认为 markdown。"},
                        "timeout": {"type": "number", "description": "可选的超时时间（秒），最大值为 120。"},
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
                        "query": {"type": "string", "description": "网络搜索查询"},
                        "numResults": {"type": "integer", "description": "要返回的搜索结果数量（默认：8）。"},
                        "livecrawl": {"type": "string", "enum": ["fallback", "preferred"], "description": "实时爬取模式 ——  'fallback'：当缓存内容不可用时，将实时爬取作为备用方案；  'preferred'：优先使用实时爬取（默认值：'fallback'）。"},
                        "type": {"type": "string", "enum": ["auto", "fast", "deep"], "description": "搜索类型 ——  'auto'：平衡型搜索（默认），  'fast'：快速结果，  'deep'：全面深入的搜索。"},
                        "contextMaxCharacters": {"type": "integer", "description": "为大语言模型（LLM）优化的上下文字符串的最大字符数（默认：10000）。"},
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
                                    "id": {"type": "string", "description": "待办事项的唯一标识符"},
                                    "text": {"type": "string", "description": "任务的简要描述"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                                        "description": "任务的当前状态：pending, in_progress, completed, cancelled",
                                    },
                                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "任务的优先级：high, medium, low"},
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
                        "name": {
                            "type": "string",
                            "description": "要加载的 skill 名称",
                        }
                    },
                    "required": ["name"],
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
