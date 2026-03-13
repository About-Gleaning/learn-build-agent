from pathlib import Path
from typing import Any

TODO_DESC_FILE = Path(__file__).with_name("todo_write.txt")
TODO_TOOL_DESCRIPTION = TODO_DESC_FILE.read_text().strip()
PLAN_ENTER_DESC_FILE = Path(__file__).with_name("plan_enter.txt")
PLAN_ENTER_TOOL_DESCRIPTION = PLAN_ENTER_DESC_FILE.read_text().strip()
PLAN_EXIT_DESC_FILE = Path(__file__).with_name("plan_exit.txt")
PLAN_EXIT_TOOL_DESCRIPTION = PLAN_EXIT_DESC_FILE.read_text().strip()
WEBFETCH_DESC_FILE = Path(__file__).with_name("webfetch.txt")
WEBFETCH_TOOL_DESCRIPTION = WEBFETCH_DESC_FILE.read_text().strip()
WEBSEARCH_DESC_FILE = Path(__file__).with_name("websearch.txt")
WEBSEARCH_TOOL_DESCRIPTION = WEBSEARCH_DESC_FILE.read_text().strip()

BASE_TOOL: list[dict[str, Any]] = [
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
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
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

TASK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task",
        "description": "当需要把一个相对独立的复杂任务委托给子代理时调用。子代理拥有全新上下文，不继承当前对话历史，但共享文件系统。",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "发给子代理的完整任务说明，包含目标、上下文、约束条件和期望输出。",
                },
                "agent": {
                    "type": "string",
                    "description": "要调用的子代理名称，当前支持 explore。",
                    "default": "explore",
                },
            },
            "required": ["prompt"],
        },
    },
}

PLAN_ENTER_TOOL: dict[str, Any] = {
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

PLAN_EXIT_TOOL: dict[str, Any] = {
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

BUILD_AGENT_TOOL = BASE_TOOL + [TASK_TOOL, PLAN_ENTER_TOOL]
PLAN_AGENT_TOOL = BASE_TOOL + [TASK_TOOL, PLAN_EXIT_TOOL]
