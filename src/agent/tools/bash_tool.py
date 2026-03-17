import os
import shlex
import subprocess
from pathlib import Path

DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
READ_ONLY_BASH_COMMANDS = {
    "ls",
    "cat",
    "rg",
    "grep",
    "find",
    "head",
    "tail",
    "wc",
    "pwd",
    "sed",
    "sort",
    "uniq",
    "cut",
    "echo",
    "tree",
}
FORBIDDEN_BASH_FRAGMENTS = {
    ";": "Error: plan 模式下禁止链式执行，请拆成单独命令。",
    "&&": "Error: plan 模式下禁止链式执行，请拆成单独命令。",
    "||": "Error: plan 模式下禁止链式执行，请拆成单独命令。",
    ">": "Error: plan 模式下禁止重定向。",
    "<": "Error: plan 模式下禁止输入重定向。",
    "$(": "Error: plan 模式下禁止命令替换。",
    "`": "Error: plan 模式下禁止命令替换。",
}
DANGEROUS_BASH_ARGS = {"-i", "--in-place", "-exec", "--output"}
MAX_PIPE_SEGMENTS = 3


def run_bash(command: str) -> str:
    if any(pattern in command for pattern in DANGEROUS_PATTERNS):
        return "Error: Dangerous command blocked"

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

    output = (completed.stdout + completed.stderr).strip()
    return output[:50000] if output else "(no output)"


def validate_readonly_bash(command: str) -> str | None:
    stripped = command.strip()
    if not stripped:
        return "Error: 空命令。"

    for fragment, error in FORBIDDEN_BASH_FRAGMENTS.items():
        if fragment in stripped:
            return error

    segments = [segment.strip() for segment in stripped.split("|")]
    if any(not segment for segment in segments):
        return "Error: bash 管道格式非法。"
    if len(segments) > MAX_PIPE_SEGMENTS:
        return f"Error: plan 模式下最多允许 {MAX_PIPE_SEGMENTS} 段只读管道。"

    for segment in segments:
        validation_error = _validate_single_readonly_segment(segment)
        if validation_error:
            return validation_error

    return None


def _validate_single_readonly_segment(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except Exception as exc:
        return f"Error: bash 命令解析失败: {type(exc).__name__}: {exc}"

    if not parts:
        return "Error: 空命令。"

    base = Path(parts[0]).name
    if base not in READ_ONLY_BASH_COMMANDS:
        return f"Error: plan 模式下不允许执行命令 `{base}`。"

    if any(arg in DANGEROUS_BASH_ARGS for arg in parts[1:]):
        return "Error: plan 模式下检测到潜在写入参数，已拒绝执行。"

    return None
