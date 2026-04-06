from __future__ import annotations

import re
from dataclasses import dataclass


_COMMAND_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class ParsedSlashCommand:
    raw_input: str
    name: str


def parse_slash_command(user_input: str) -> ParsedSlashCommand | None:
    normalized = (user_input or "").strip()
    if not normalized.startswith("/"):
        return None

    content = normalized[1:].strip()
    if not content:
        return None

    # slash command 只允许“纯命令”触发。
    # 只要用户在命令名后额外追加文本，就视为普通输入继续交给 LLM，
    # 避免把“/analyze xxx”这类用户自由输入误识别成系统命令。
    parts = content.split()
    if len(parts) != 1:
        return None

    name = parts[0].strip().lower()
    # parser 只负责识别 slash command 语法，不负责判断命令是否已注册。
    # 但命令名本身仍要满足安全且稳定的形态约束，避免把路径之类的输入误判成命令。
    if _COMMAND_NAME_PATTERN.fullmatch(name) is None:
        return None
    return ParsedSlashCommand(raw_input=normalized, name=name)
