import asyncio
import logging
import os

import pytest

from agent.config.settings import clear_runtime_settings_cache, get_mcp_settings
from agent.mcp.runtime import (
    _ToolCallOutcome,
    _get_asyncio_thread_runner,
    _shutdown_asyncio_thread_runner,
    clear_mcp_runtime_cache,
    describe_mcp_runtime_alerts_for_mode,
    describe_mcp_warnings_for_mode,
    execute_mcp_tool,
    list_mcp_tools,
)


@pytest.fixture(autouse=True)
def _clear_mcp_cache():
    clear_mcp_runtime_cache()
    _shutdown_asyncio_thread_runner()
    yield
    clear_mcp_runtime_cache()
    _shutdown_asyncio_thread_runner()


def test_get_mcp_settings_should_parse_servers(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "discovery_timeout_ms": 1234,
            "call_timeout_ms": 5678,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"
                },
                "expose_to_plan": false
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    try:
        settings = get_mcp_settings()
        assert settings.enabled is True
        assert settings.discovery_timeout_ms == 1234
        assert settings.call_timeout_ms == 5678
        assert settings.servers is not None
        assert settings.servers["github"].transport == "stdio"
        assert settings.servers["github"].args == ("-y", "@modelcontextprotocol/server-github")
        assert settings.servers["github"].expose_to_plan is False
    finally:
        clear_runtime_settings_cache()


def test_get_mcp_settings_should_keep_multiple_servers(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "local_8080": {
                "enabled": true,
                "transport": "streamable_http",
                "url": "http://127.0.0.1:8080",
                "expose_to_plan": true
              },
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"
                },
                "expose_to_plan": true
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    try:
        settings = get_mcp_settings()
        assert settings.servers is not None
        assert set(settings.servers) == {"local_8080", "github"}
        assert settings.servers["local_8080"].transport == "streamable_http"
        assert settings.servers["local_8080"].url == "http://127.0.0.1:8080"
        assert settings.servers["github"].command == "npx"
        assert settings.servers["github"].args == ("-y", "@modelcontextprotocol/server-github")
        assert settings.servers["github"].env["GITHUB_TOKEN"] == "${GITHUB_TOKEN}"
        assert settings.servers["github"].expose_to_plan is True
    finally:
        clear_runtime_settings_cache()


def test_list_mcp_tools_should_filter_plan_visibility(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "expose_to_plan": true
              },
              "private_docs": {
                "enabled": true,
                "transport": "stdio",
                "command": "python3",
                "expose_to_plan": false
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    discover_calls: list[str] = []

    def fake_discover(server_alias, server_settings):
        del server_settings
        discover_calls.append(server_alias)
        return [{"name": f"{server_alias}_search", "description": f"{server_alias} search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        build_tools, warnings = list_mcp_tools("build")
        plan_tools, _ = list_mcp_tools("plan")
        assert warnings == []
        assert {tool["function"]["name"] for tool in build_tools} == {
            "github__github_search",
            "private_docs__private_docs_search",
        }
        assert {tool["function"]["name"] for tool in plan_tools} == {"github__github_search"}
        assert discover_calls == ["github", "private_docs"]
    finally:
        clear_runtime_settings_cache()


def test_list_mcp_tools_should_skip_disabled_server_before_discovery(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "enabled_server": {
                "enabled": true,
                "transport": "stdio",
                "command": "python3"
              },
              "disabled_server": {
                "enabled": false,
                "transport": "stdio",
                "command": "python3"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    discover_calls: list[str] = []

    def fake_discover(server_alias, server_settings):
        del server_settings
        discover_calls.append(server_alias)
        if server_alias == "disabled_server":
            raise RuntimeError("disabled server should not be discovered")
        return [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        tools, warnings = list_mcp_tools("build")
        assert {tool["function"]["name"] for tool in tools} == {"enabled_server__search"}
        assert warnings == []
        assert discover_calls == ["enabled_server"]
    finally:
        clear_runtime_settings_cache()


def test_describe_mcp_warnings_for_mode_should_skip_hidden_plan_server(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "expose_to_plan": true
              },
              "private_docs": {
                "enabled": true,
                "transport": "stdio",
                "command": "python3",
                "expose_to_plan": false
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    discover_calls: list[str] = []

    def fake_discover(server_alias, server_settings):
        del server_settings
        discover_calls.append(server_alias)
        if server_alias == "github":
            raise RuntimeError("github 连接失败")
        raise RuntimeError("private_docs 不应在 plan 模式 discovery")

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        warning_text = describe_mcp_warnings_for_mode("plan")
        assert "github" in warning_text
        assert "github 连接失败" in warning_text
        assert "private_docs" not in warning_text
        assert discover_calls == ["github"]
    finally:
        clear_runtime_settings_cache()


def test_describe_mcp_warnings_for_mode_should_include_failed_server(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "broken": {
                "enabled": true,
                "transport": "stdio",
                "command": "missing-cmd"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    monkeypatch.setattr(
        "agent.mcp.runtime._discover_server_tools",
        lambda server_alias, server_settings: (_ for _ in ()).throw(RuntimeError("连接失败")),
    )
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        warning_text = describe_mcp_warnings_for_mode("build")
        assert "broken" in warning_text
        assert "连接失败" in warning_text
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_wrap_result_metadata(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    monkeypatch.setattr(
        "agent.mcp.runtime._discover_server_tools",
        lambda server_alias, server_settings: [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}],
    )
    monkeypatch.setattr(
        "agent.mcp.runtime._call_server_tool",
        lambda server_alias, server_settings, tool_name, arguments: {
            "content": [{"type": "text", "text": f"{server_alias}:{tool_name}:{arguments['query']}"}]
        },
    )
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        result = execute_mcp_tool("github__search", {"query": "issue"})
        assert result["metadata"]["status"] == "completed"
        assert result["metadata"]["mcp_server_alias"] == "github"
        assert result["metadata"]["mcp_tool_name"] == "search"
        assert "github:search:issue" in result["output"]
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_reject_hidden_plan_tool(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "private_docs": {
                "enabled": true,
                "transport": "stdio",
                "command": "python3",
                "expose_to_plan": false
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)

    discover_calls = {"count": 0}

    def fake_discover(server_alias, server_settings):
        del server_alias, server_settings
        discover_calls["count"] += 1
        return [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        result = execute_mcp_tool("private_docs__search", {"query": "bug"}, mode="plan")
        assert result["metadata"]["status"] == "failed"
        assert result["metadata"]["error_code"] == "mcp_tool_not_allowed_in_plan"
        assert result["metadata"]["mcp_server_alias"] == "private_docs"
        assert discover_calls["count"] == 0
    finally:
        clear_runtime_settings_cache()


def test_list_mcp_tools_should_retry_after_discovery_failure(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)

    discover_calls = {"count": 0}

    def fake_discover(server_alias, server_settings):
        del server_alias, server_settings
        discover_calls["count"] += 1
        if discover_calls["count"] == 1:
            raise RuntimeError("连接失败")
        return [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    try:
        first_tools, first_warnings = list_mcp_tools("build")
        second_tools, second_warnings = list_mcp_tools("build")

        assert first_tools == []
        assert len(first_warnings) == 1
        assert "连接失败" in first_warnings[0].message
        assert {tool["function"]["name"] for tool in second_tools} == {"github__search"}
        assert second_warnings == []
        assert discover_calls["count"] == 2
    finally:
        clear_runtime_settings_cache()


def test_list_mcp_tools_should_refresh_cache_when_resolved_env_changes(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "env": {
                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"
                }
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)

    discover_calls: list[str] = []

    def fake_discover(server_alias, server_settings):
        del server_alias
        discover_calls.append(os.getenv("GITHUB_TOKEN", ""))
        if not os.getenv("GITHUB_TOKEN"):
            raise ValueError("mcp.servers.env.GITHUB_TOKEN 引用了未设置的环境变量 GITHUB_TOKEN")
        return [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)
    monkeypatch.setenv("GITHUB_TOKEN", "token-v1")

    try:
        first_tools, first_warnings = list_mcp_tools("build")
        monkeypatch.setenv("GITHUB_TOKEN", "token-v2")
        second_tools, second_warnings = list_mcp_tools("build")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        third_tools, third_warnings = list_mcp_tools("build")

        assert {tool["function"]["name"] for tool in first_tools} == {"github__search"}
        assert {tool["function"]["name"] for tool in second_tools} == {"github__search"}
        assert third_tools == []
        assert first_warnings == []
        assert second_warnings == []
        assert len(third_warnings) == 1
        assert "GITHUB_TOKEN" in third_warnings[0].message
        assert len(discover_calls) == 3
    finally:
        clear_runtime_settings_cache()


def test_describe_mcp_runtime_alerts_for_mode_should_surface_warnings(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "env": {
                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"
                }
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    try:
        alerts = describe_mcp_runtime_alerts_for_mode("build")
        assert len(alerts) == 1
        assert alerts[0].server_alias == "github"
        assert alerts[0].code == "mcp_server_unavailable"
        assert "GITHUB_TOKEN" in alerts[0].message
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_work_inside_running_event_loop(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)

    loop_records: list[tuple[str, int]] = []

    class FakeSession:
        async def list_tools(self):
            loop_records.append(("list_tools", id(asyncio.get_running_loop())))
            return {
                "tools": [
                    {
                        "name": "search",
                        "description": "search",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }

        async def call_tool(self, tool_name, arguments):
            loop_records.append((f"call:{tool_name}", id(asyncio.get_running_loop())))
            return {"content": [{"type": "text", "text": arguments["query"]}]}

    class FakeSessionHandle:
        def __init__(self):
            self.session = FakeSession()
            self.close_warning = ""

        def consume_close_warning(self):
            warning = self.close_warning
            self.close_warning = ""
            return warning

    class FakeSessionContextManager:
        async def __aenter__(self):
            loop_records.append(("enter", id(asyncio.get_running_loop())))
            return FakeSessionHandle()

        async def __aexit__(self, exc_type, exc, tb):
            loop_records.append(("exit", id(asyncio.get_running_loop())))

    monkeypatch.setattr(
        "agent.mcp.runtime._open_server_session",
        lambda server_settings, server_alias, tool_name="": FakeSessionContextManager(),
    )

    async def run_case():
        caller_loop_id = id(asyncio.get_running_loop())
        result = execute_mcp_tool("github__search", {"query": "issue"})
        return caller_loop_id, result

    try:
        caller_loop_id, result = asyncio.run(run_case())
        assert result["metadata"]["status"] == "completed"
        assert result["output"] == "issue"
        assert loop_records
        assert {name for name, _ in loop_records} >= {"enter", "list_tools", "call:search", "exit"}
        assert all(loop_id != caller_loop_id for _, loop_id in loop_records)
        assert len({loop_id for _, loop_id in loop_records}) == 1
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_keep_success_when_close_warning_happens(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    monkeypatch.setattr(
        "agent.mcp.runtime._discover_server_tools",
        lambda server_alias, server_settings: [{"name": "search", "description": "search", "inputSchema": {"type": "object"}}],
    )
    monkeypatch.setattr(
        "agent.mcp.runtime._call_server_tool",
        lambda server_alias, server_settings, tool_name, arguments: _ToolCallOutcome(
            result={"content": [{"type": "text", "text": "ok"}]},
            close_warning="RuntimeError: close failed",
        ),
    )
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: coro)

    try:
        with caplog.at_level(logging.WARNING):
            result = execute_mcp_tool("github__search", {"query": "issue"})
        assert result["metadata"]["status"] == "completed"
        assert result["output"] == "ok"
        assert "mcp.call.close_warning" in caplog.text
        assert "close failed" in caplog.text
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_keep_primary_error_when_close_warning_happens(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    async def fake_discover(server_alias, server_settings):
        del server_alias, server_settings
        return [{"name": "create_branch", "description": "create branch", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: asyncio.run(coro))

    class FakeSession:
        async def call_tool(self, tool_name, arguments):
            del tool_name, arguments
            raise RuntimeError("GitHub API 403")

    class FakeSessionHandle:
        def __init__(self):
            self.session = FakeSession()
            self.close_warning = ""

        def consume_close_warning(self):
            warning = self.close_warning
            self.close_warning = ""
            return warning

    class FakeSessionContextManager:
        def __init__(self):
            self.handle = FakeSessionHandle()

        async def __aenter__(self):
            return self.handle

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            self.handle.close_warning = "RuntimeError: session close failed"

    monkeypatch.setattr(
        "agent.mcp.runtime._open_server_session",
        lambda server_settings, server_alias, tool_name="": FakeSessionContextManager(),
    )

    try:
        with caplog.at_level(logging.WARNING):
            result = execute_mcp_tool("github__create_branch", {"branch": "feature/test"})
        assert result["metadata"]["status"] == "failed"
        assert result["metadata"]["error_code"] == "mcp_tool_call_failed"
        assert result["metadata"]["error_type"] == "RuntimeError"
        assert result["metadata"]["error_summary"] == "RuntimeError: GitHub API 403"
        assert result["metadata"]["close_warning"] == "RuntimeError: session close failed"
        assert "GitHub API 403" in result["output"]
        assert "关闭阶段告警: RuntimeError: session close failed" in result["output"]
        assert "mcp.call.failed" in caplog.text
        assert "GitHub API 403" in caplog.text
    finally:
        clear_runtime_settings_cache()


def test_execute_mcp_tool_should_expand_exception_group_summary(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx"
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    async def fake_discover(server_alias, server_settings):
        del server_alias, server_settings
        return [{"name": "create_branch", "description": "create branch", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("agent.mcp.runtime._discover_server_tools", fake_discover)
    monkeypatch.setattr("agent.mcp.runtime._run_sync", lambda coro: asyncio.run(coro))

    def raise_group(server_alias, server_settings, tool_name, arguments):
        del server_alias, server_settings, tool_name, arguments
        raise ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("GitHub API 403"), ValueError("bad branch name")])

    monkeypatch.setattr("agent.mcp.runtime._call_server_tool", raise_group)

    try:
        result = execute_mcp_tool("github__create_branch", {"branch": "feature/test"})
        assert result["metadata"]["status"] == "failed"
        assert result["metadata"]["error_type"] == "ExceptionGroup"
        assert "子异常: RuntimeError: GitHub API 403; ValueError: bad branch name" in result["metadata"]["error_summary"]
        assert "GitHub API 403" in result["output"]
        assert "bad branch name" in result["output"]
    finally:
        clear_runtime_settings_cache()


def test_describe_mcp_warnings_for_mode_should_surface_missing_github_token(tmp_path, monkeypatch):
    config_path = tmp_path / "project_runtime.json"
    config_path.write_text(
        """
        {
          "mcp": {
            "enabled": true,
            "servers": {
              "github": {
                "enabled": true,
                "transport": "stdio",
                "command": "npx",
                "env": {
                  "GITHUB_TOKEN": "${GITHUB_TOKEN}"
                }
              }
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    clear_runtime_settings_cache()
    monkeypatch.setattr("agent.config.settings.PROJECT_RUNTIME_CONFIG_PATH", config_path)
    monkeypatch.setattr("agent.mcp.runtime._MCP_IMPORT_ERROR", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    try:
        warning_text = describe_mcp_warnings_for_mode("build")
        assert "GITHUB_TOKEN" in warning_text
        assert "未设置的环境变量" in warning_text
    finally:
        clear_runtime_settings_cache()


def test_asyncio_thread_runner_should_recreate_after_shutdown():
    first = _get_asyncio_thread_runner()
    _shutdown_asyncio_thread_runner()
    second = _get_asyncio_thread_runner()
    try:
        assert first is not second
        assert second.is_alive() is True
    finally:
        _shutdown_asyncio_thread_runner()


def test_open_server_session_should_close_streamable_http_async_client(monkeypatch):
    from agent.config.settings import McpServerSettings
    from agent.mcp.runtime import _open_server_session

    enter_events: list[str] = []
    exit_events: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            enter_events.append("http_client")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            exit_events.append("http_client")

    class FakeTransportContext:
        async def __aenter__(self):
            enter_events.append("transport")
            return object(), object(), object()

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            exit_events.append("transport")

    class FakeClientSession:
        def __init__(self, read_stream, write_stream):
            del read_stream, write_stream

        async def __aenter__(self):
            enter_events.append("session")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            exit_events.append("session")

        async def initialize(self):
            enter_events.append("initialize")

    monkeypatch.setattr("agent.mcp.runtime.httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeAsyncClient}))
    monkeypatch.setattr(
        "agent.mcp.runtime.streamable_http_client",
        lambda url, http_client: FakeTransportContext(),
    )
    monkeypatch.setattr("agent.mcp.runtime.ClientSession", FakeClientSession)

    server_settings = McpServerSettings(
        enabled=True,
        transport="streamable_http",
        command="",
        args=(),
        env={},
        cwd="",
        url="http://127.0.0.1:8080/mcp",
        headers={"Authorization": "Bearer token"},
        expose_to_plan=True,
        discovery_timeout_ms=1000,
        call_timeout_ms=2000,
    )

    async def run_case():
        async with _open_server_session(server_settings, server_alias="github", tool_name="search") as session_ctx:
            assert session_ctx.session is not None

    asyncio.run(run_case())

    assert enter_events == ["http_client", "transport", "session", "initialize"]
    assert exit_events == ["session", "transport", "http_client"]
