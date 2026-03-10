from pathlib import Path
from typing import Any

TODO_DESC_FILE = Path(__file__).with_name("todo_write.txt")
TODO_TOOL_DESCRIPTION = TODO_DESC_FILE.read_text().strip()

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

MAIN_AGENT_TOOL = BASE_TOOL + [
    {
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
                    }
                },
                "required": ["prompt"],
            },
        },
    },
]
