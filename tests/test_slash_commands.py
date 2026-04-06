from __future__ import annotations

import pytest

from agent.runtime.workspace import configure_workspace
from agent.slash_commands.parser import parse_slash_command
from agent.slash_commands.registry import SlashCommandDefinition
from agent.slash_commands.resolver import resolve_slash_command


def test_parse_slash_command_should_parse_registered_like_name_without_registry_check():
    parsed = parse_slash_command("/missing")

    assert parsed is not None
    assert parsed.raw_input == "/missing"
    assert parsed.name == "missing"


@pytest.mark.parametrize("user_input", ["/analyze 请补充数据库设计", "/tmp/foo", "/bin/bash -lc echo hi", "/"])
def test_parse_slash_command_should_reject_non_pure_command(user_input: str):
    assert parse_slash_command(user_input) is None


def test_resolve_slash_command_should_return_none_for_unknown_command():
    assert resolve_slash_command("/missing") is None


def test_resolve_slash_command_should_resolve_init_from_handler_key(tmp_path):
    configure_workspace(tmp_path)

    resolved = resolve_slash_command("/init")

    assert resolved is not None
    assert resolved.command.name == "init"
    assert resolved.command.handler_key == "init_agents"
    assert resolved.override_mode == "build"
    assert resolved.display_text == "/init"
    assert "AGENTS.md" in resolved.user_input


def test_resolve_slash_command_should_raise_when_registered_handler_missing(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    broken_command = SlashCommandDefinition(
        name="broken",
        description="broken",
        usage="/broken",
        placeholder="broken",
        handler_key="init_agents",
    )
    monkeypatch.setattr(
        "agent.slash_commands.resolver.get_slash_command",
        lambda name: broken_command if name == "broken" else None,
    )
    monkeypatch.setattr("agent.slash_commands.resolver._RESOLVERS", {})

    with pytest.raises(ValueError, match=r"缺少 handler"):
        resolve_slash_command("/broken")
