from __future__ import annotations

from dataclasses import dataclass

from .registry import get_slash_command


@dataclass(frozen=True)
class ParsedSlashCommand:
    raw_input: str
    name: str
    args_text: str


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
    # 只有完整输入恰好命中已注册 slash command 时，才进入命令分支；
    # 像 /tmp/foo、/bin/bash -lc ...、/analyze 请帮我... 这类输入都应继续交给 LLM。
    if get_slash_command(name) is None:
        return None
    return ParsedSlashCommand(raw_input=normalized, name=name, args_text="")
