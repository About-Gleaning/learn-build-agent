import os
from pathlib import Path
import subprocess

WORKDIR = Path.cwd()

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    
def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"
    
def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
    
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

BASE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }
        }
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
            }
        }
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
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "更新任务清单,跟踪多步骤任务的进度。",
            "parameters": {
                "type": "object",
                "properties": {"todo_list": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["text", "status"]}}},
                "required": ["todo_list"],
            }
        }
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
                        "description": "要加载的 skill 名称列表"
                    }
                },
                "required": ["skill_names"]
            }
        }
    }
]

MAIN_AGENT_TOOL = BASE_TOOL + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "当需要把一个相对独立的复杂任务委托给子代理时调用。子代理拥有全新上下文，不继承当前对话历史，但共享文件系统。",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string", "description": "发给子代理的完整任务说明，包含目标、上下文、约束条件和期望输出。"}},
                "required": ["prompt"],
            }
        }
    },
]


