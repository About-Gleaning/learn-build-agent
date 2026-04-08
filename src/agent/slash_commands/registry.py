from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .types import SlashCommandHandlerKey


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class SlashCommandDefinition:
    name: str
    description: str
    usage: str
    placeholder: str
    handler_key: SlashCommandHandlerKey
    visible_in_web: bool = True
    prompt_template_path: Path | None = None


_SLASH_COMMANDS: tuple[SlashCommandDefinition, ...] = (
    SlashCommandDefinition(
        name="init",
        description="初始化当前工作区的 AGENTS.md；若已存在则停止，若不存在则生成简明内容。",
        usage="/init",
        placeholder="为当前项目初始化 AGENTS.md",
        handler_key="init_agents",
        prompt_template_path=PROMPTS_DIR / "init.txt",
    ),
    SlashCommandDefinition(
        name="analyze",
        description="初始化当前项目的开发手册；若已存在则停止，避免覆盖人工维护内容。",
        usage="/analyze",
        placeholder="初始化当前项目的开发手册",
        handler_key="analyze_project",
        prompt_template_path=PROMPTS_DIR / "analyze.txt",
    ),
)


def list_slash_commands() -> list[SlashCommandDefinition]:
    return list(_SLASH_COMMANDS)


def list_visible_slash_commands() -> list[SlashCommandDefinition]:
    return [command for command in _SLASH_COMMANDS if command.visible_in_web]


def get_slash_command(name: str) -> SlashCommandDefinition | None:
    normalized_name = (name or "").strip().lower()
    if not normalized_name:
        return None
    for command in _SLASH_COMMANDS:
        if command.name == normalized_name:
            return command
    return None
