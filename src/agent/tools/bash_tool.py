import os
import select
import shlex
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..runtime.workspace import get_workspace

DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
DEFAULT_TIMEOUT = 120000
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
SHELL_READY_MARKER_PREFIX = "__MY_AGENT_SHELL_READY__"
SHELL_EXIT_MARKER_PREFIX = "__MY_AGENT_EXIT__"
@dataclass
class PersistentBashSession:
    workdir: Path
    process: subprocess.Popen[str] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def run(self, command: str, timeout_ms: int) -> str:
        with self.lock:
            self._ensure_started(timeout_ms)
            marker = f"{SHELL_EXIT_MARKER_PREFIX}{uuid.uuid4().hex}"
            wrapped_command = _wrap_bash_command(command, marker)
            try:
                assert self.process is not None and self.process.stdin is not None
                self.process.stdin.write(wrapped_command)
                self.process.stdin.flush()
                output = self._read_until_marker(marker, timeout_ms)
            except subprocess.TimeoutExpired:
                self.close()
                raise
            except BrokenPipeError:
                self.close()
                raise RuntimeError("bash 持久会话已中断")
            return output.strip() or "(no output)"

    def _ensure_started(self, timeout_ms: int) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        self.close()
        self.process = subprocess.Popen(
            ["/bin/bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.workdir,
            text=True,
            bufsize=0,
        )
        ready_marker = f"{SHELL_READY_MARKER_PREFIX}{uuid.uuid4().hex}"
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(f"printf '%s:%s\\n' '{ready_marker}' '0'\n")
            self.process.stdin.flush()
            self._read_until_marker(ready_marker, timeout_ms)
        except Exception:
            self.close()
            raise

    def _read_until_marker(self, marker: str, timeout_ms: int) -> str:
        assert self.process is not None and self.process.stdout is not None
        stream = self.process.stdout
        fd = stream.fileno()
        marker_token = f"{marker}:"
        marker_window_keep = max(len(marker_token) - 1, 0)
        timeout_seconds = timeout_ms / 1000
        expires_at = time.monotonic() + timeout_seconds
        completed_chunks: deque[str] = deque()
        completed_size = 0
        pending = ""
        while True:
            remaining_seconds = expires_at - time.monotonic()
            if remaining_seconds <= 0:
                raise subprocess.TimeoutExpired(cmd="bash", timeout=timeout_seconds)

            ready, _, _ = select.select([fd], [], [], remaining_seconds)
            if not ready:
                raise subprocess.TimeoutExpired(cmd="bash", timeout=timeout_seconds)

            chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
            if chunk == "":
                raise RuntimeError("bash 持久会话提前退出")

            window = pending + chunk
            marker_index = window.find(marker_token)
            if marker_index < 0:
                if marker_window_keep > 0 and len(window) > marker_window_keep:
                    flush_size = len(window) - marker_window_keep
                    completed_chunks.append(window[:flush_size])
                    completed_size += flush_size
                    pending = window[flush_size:]
                else:
                    pending = window
                continue

            # 只有读到完整 marker 行后才能安全截断，避免把半行 marker 当成结束。
            line_end = window.find("\n", marker_index)
            if line_end < 0:
                pending = window
                continue

            marker_absolute_index = completed_size + marker_index
            combined_output = "".join(completed_chunks) + pending + chunk
            content = combined_output[:marker_absolute_index]
            return content.rstrip("\n")

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass
        try:
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


def run_bash(command: str, timeout: int | float | None = None, workdir: str | None = None) -> str:
    if any(pattern in command for pattern in DANGEROUS_PATTERNS):
        return "Error: Dangerous command blocked"

    timeout_ms = _normalize_timeout(timeout)
    target_workdir = resolve_bash_workdir(workdir)
    bash_session = PersistentBashSession(workdir=target_workdir)
    try:
        return bash_session.run(command, timeout_ms)
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({timeout_ms}ms)"
    finally:
        bash_session.close()


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


def _normalize_timeout(timeout: int | float | None) -> int:
    if timeout is None:
        return DEFAULT_TIMEOUT

    timeout_ms = int(timeout)
    if timeout_ms <= 0:
        raise ValueError("timeout 必须大于 0")
    return timeout_ms


def resolve_bash_workdir(workdir: str | None) -> Path:
    workspace_root = get_workspace().root
    if workdir is None:
        return workspace_root

    raw_path = Path(workdir).expanduser()
    target = raw_path.resolve() if raw_path.is_absolute() else (workspace_root / raw_path).resolve()
    if not target.is_relative_to(workspace_root):
        raise ValueError(f"workdir 超出工作区范围: {workdir}")
    if not target.exists():
        raise FileNotFoundError(f"workdir 不存在: {workdir}")
    if not target.is_dir():
        raise NotADirectoryError(f"workdir 不是目录: {workdir}")
    return target


def _wrap_bash_command(command: str, marker: str) -> str:
    # 使用分组包裹命令，并在末尾输出唯一 marker，便于从持久 shell 中安全截取本次输出。
    return (
        "{\n"
        f"{command}\n"
        "}\n"
        f"printf '%s:%s\\n' '{marker}' \"$?\"\n"
    )
