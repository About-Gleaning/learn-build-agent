from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from pathlib import Path

from ..runtime.workspace import get_workspace
from .parser import parse_slash_command
from .registry import SlashCommandDefinition, get_slash_command
from .types import SlashCommandHandlerKey


@dataclass(frozen=True)
class ResolvedSlashCommand:
    command: SlashCommandDefinition
    user_input: str
    override_mode: str | None = None
    display_text: str = ""
    immediate_output: str | None = None


def _render_analyze_prompt(command: SlashCommandDefinition) -> str:
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


def _render_init_agents_prompt(command: SlashCommandDefinition) -> str:
    template_path = command.prompt_template_path
    if template_path is None or not template_path.exists():
        raise ValueError(f"未找到 slash command prompt 模板: {template_path}")

    workspace = get_workspace()
    target_path = workspace.agents_md_path.resolve()
    template = template_path.read_text(encoding="utf-8").strip()
    return template.format(
        workspace_root=workspace.root,
        workspace_name=workspace.workspace_name,
        agents_path=target_path,
        readme_path=(workspace.root / "README.md").resolve(),
    )


def _resolve_init_agents(command: SlashCommandDefinition, user_input: str) -> ResolvedSlashCommand:
    workspace = get_workspace()
    if workspace.has_agents_md:
        return ResolvedSlashCommand(
            command=command,
            user_input=user_input,
            display_text="/init",
            immediate_output="当前工作区已存在 `AGENTS.md`，已停止初始化，未执行生成。",
        )
    return ResolvedSlashCommand(
        command=command,
        user_input=_render_init_agents_prompt(command),
        override_mode="build",
        display_text="/init",
    )


def _resolve_analyze_project(command: SlashCommandDefinition, user_input: str) -> ResolvedSlashCommand:
    workspace = get_workspace()
    if not workspace.has_agents_md:
        return ResolvedSlashCommand(
            command=command,
            user_input=user_input,
            display_text="/analyze",
            immediate_output="当前工作区不存在 `AGENTS.md`，请先执行 `/init` 完成初始化后再使用 `/analyze`。",
        )
    return ResolvedSlashCommand(
        command=command,
        user_input=_render_analyze_prompt(command),
        override_mode="build",
        display_text="/analyze",
    )


_RESOLVERS: dict[SlashCommandHandlerKey, Callable[[SlashCommandDefinition, str], ResolvedSlashCommand]] = {
    "init_agents": _resolve_init_agents,
    "analyze_project": _resolve_analyze_project,
}


def resolve_slash_command(user_input: str) -> ResolvedSlashCommand | None:
    parsed = parse_slash_command(user_input)
    if parsed is None:
        return None

    command = get_slash_command(parsed.name)
    if command is None:
        return None

    resolver = _RESOLVERS.get(command.handler_key)
    if resolver is None:
        raise ValueError(f"slash command `/{command.name}` 缺少 handler: {command.handler_key}")
    return resolver(command, user_input)
