from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..runtime.workspace import get_workspace
from .parser import parse_slash_command
from .registry import SlashCommandDefinition, get_slash_command, list_visible_slash_commands


@dataclass(frozen=True)
class ResolvedSlashCommand:
    command: SlashCommandDefinition
    user_input: str
    override_mode: str | None = None
    display_text: str = ""


def _render_init_prompt(command: SlashCommandDefinition) -> str:
    template_path = command.prompt_template_path
    if template_path is None or not template_path.exists():
        raise ValueError(f"未找到 slash command prompt 模板: {template_path}")

    workspace = get_workspace()
    target_path = (workspace.root / "analyze_docs" / "project-context.md").resolve()
    template = template_path.read_text(encoding="utf-8").strip()
    return template.format(
        workspace_root=workspace.root,
        workspace_name=workspace.workspace_name,
        target_doc_path=target_path,
        readme_path=(workspace.root / "README.md").resolve(),
        agents_path=(workspace.root / "AGENTS.md").resolve(),
        docs_dir=(workspace.root / "analyze_docs").resolve(),
    )


def _build_unknown_command_error(command_name: str) -> str:
    visible_names = "、".join(f"/{item.name}" for item in list_visible_slash_commands())
    if not command_name:
        return f"未识别到具体命令。当前可用命令：{visible_names or '无'}"
    return f"未找到命令 `/{command_name}`。当前可用命令：{visible_names or '无'}"


def resolve_slash_command(user_input: str) -> ResolvedSlashCommand | None:
    parsed = parse_slash_command(user_input)
    if parsed is None:
        return None

    command = get_slash_command(parsed.name)
    if command is None:
        raise ValueError(_build_unknown_command_error(parsed.name))

    if command.name == "analyze":
        return ResolvedSlashCommand(
            command=command,
            user_input=_render_init_prompt(command),
            override_mode="build",
            display_text="/analyze",
        )

    raise ValueError(_build_unknown_command_error(parsed.name))
