from pathlib import Path

import pytest

from agent.config.settings import LspIdeSettings, LspLanguageSettings, LspSettings
from agent.lsp.client import LspClient, clear_lsp_runtime_state
from agent.lsp.filters import filter_diagnostics
from agent.lsp.types import LspDiagnostic, LspDiagnosticsResult, LspPosition, LspQueryResult, LspRange


def _build_lsp_settings(*, enabled: bool = True, java_enabled: bool = True, python_enabled: bool = True) -> LspSettings:
    return LspSettings(
        enabled=enabled,
        ide_enabled=False,
        startup_mode="on_demand",
        server_idle_ttl_seconds=60,
        request_timeout_ms=1000,
        max_diagnostics=2,
        max_chars=40,
        include_severity=("error", "warning"),
        strict_unavailable=False,
        languages={
            "java": LspLanguageSettings(
                enabled=java_enabled,
                command=(
                    "/usr/bin/env",
                    "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
                    "jdtls",
                ),
                file_extensions=(".java",),
                workspace_markers=("pom.xml",),
                init_options={},
                maven_local_repository="",
            ),
            "python": LspLanguageSettings(
                enabled=python_enabled,
                command=("pylsp",),
                file_extensions=(".py",),
                workspace_markers=("pyproject.toml", "setup.py", "requirements.txt", "setup.cfg"),
                init_options={},
                maven_local_repository="",
            ),
            "typescript": LspLanguageSettings(
                enabled=False,
                command=(),
                file_extensions=(".ts",),
                workspace_markers=("package.json",),
                init_options={},
                maven_local_repository="",
            ),
        },
        ide=LspIdeSettings(),
    )


@pytest.fixture(autouse=True)
def _clear_lsp_runtime():
    clear_lsp_runtime_state()
    yield
    clear_lsp_runtime_state()


def test_lsp_client_should_return_unsupported_language_for_txt(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())

    result = LspClient().collect_diagnostics(file_path=tmp_path / "notes.txt", content="hello")

    assert result.status == "unsupported_language"
    assert result.diagnostics == ()


def test_lsp_client_should_return_not_enabled_when_global_switch_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings(enabled=False))

    result = LspClient().collect_diagnostics(file_path=tmp_path / "Foo.java", content="class Foo {}")

    assert result.status == "not_enabled"


def test_lsp_client_should_route_java_file_to_manager(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            captured["language"] = adapter.language
            captured["file_path"] = file_path
            captured["content"] = content
            return LspDiagnosticsResult(
                status="completed",
                diagnostics=(),
                diagnostics_total=0,
                lsp_language="java",
                lsp_server="jdtls",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    target = tmp_path / "Foo.java"
    result = LspClient().collect_diagnostics(file_path=target, content="class Foo {}")

    assert result.status == "completed"
    assert captured["language"] == "java"
    assert captured["file_path"] == target


def test_lsp_client_should_passthrough_diagnostics_wait_metadata(monkeypatch, tmp_path):
    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            del adapter, file_path, content
            return LspDiagnosticsResult(
                status="completed",
                diagnostics=(),
                diagnostics_total=0,
                lsp_language="java",
                lsp_server="jdtls",
                raw_diagnostics_total=5,
                diagnostics_sequence=3,
                diagnostics_previous_sequence=1,
                diagnostics_latest_sequence=3,
                diagnostics_wait_rounds=2,
                diagnostics_wait_ms=860,
                diagnostics_settled=True,
                lsp_workspace_root=str(tmp_path),
                lsp_data_dir=str(tmp_path / ".my-agent-lsp" / "java" / "abc123"),
                lsp_workspace_selection_reason="maven_aggregator_root",
                lsp_server_key=f"java:{tmp_path}:direct_lsp",
                lsp_snapshot_uri=(tmp_path / "Foo.java").resolve().as_uri(),
                recent_status_summary="Starting:Init...",
                recent_log_summary="1:build running",
                recent_publish_uris="src/Foo.java#3(5)",
                received_other_file_diagnostics=False,
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    result = LspClient().collect_diagnostics(file_path=tmp_path / "Foo.java", content="class Foo {}")

    assert result.raw_diagnostics_total == 5
    assert result.diagnostics_sequence == 3
    assert result.diagnostics_previous_sequence == 1
    assert result.diagnostics_latest_sequence == 3
    assert result.diagnostics_wait_rounds == 2
    assert result.diagnostics_wait_ms == 860
    assert result.diagnostics_settled is True
    assert result.lsp_workspace_root == str(tmp_path)
    assert result.lsp_data_dir == str(tmp_path / ".my-agent-lsp" / "java" / "abc123")
    assert result.lsp_workspace_selection_reason == "maven_aggregator_root"
    assert "Foo.java" in (result.lsp_snapshot_uri or "")
    assert result.recent_status_summary == "Starting:Init..."
    assert result.recent_publish_uris == "src/Foo.java#3(5)"


def test_lsp_client_should_passthrough_timeout_degraded(monkeypatch, tmp_path):
    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            del adapter, file_path, content
            return LspDiagnosticsResult(
                status="timeout_degraded",
                lsp_language="java",
                lsp_server="jdtls",
                lsp_server_pid=123,
                lsp_workspace_root=str(tmp_path),
                lsp_workspace_selection_reason="maven_nearest_module",
                lsp_server_key="java:test:direct_lsp",
                lsp_snapshot_uri=(tmp_path / "Foo.java").resolve().as_uri(),
                recent_status_summary="Starting:Init...",
                recent_log_summary="1:still indexing",
                recent_publish_uris="src/Bar.java#1(2)",
                received_other_file_diagnostics=True,
                lsp_error="等待 diagnostics 超时；已补发 didSave 重试，但仍未收到 publishDiagnostics。",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    result = LspClient().collect_diagnostics(file_path=tmp_path / "Foo.java", content="class Foo {}")

    assert result.status == "timeout_degraded"
    assert result.lsp_server == "jdtls"
    assert result.lsp_workspace_selection_reason == "maven_nearest_module"
    assert result.received_other_file_diagnostics is True
    assert "Bar.java" in result.recent_publish_uris
    assert "didSave" in (result.lsp_error or "")


def test_lsp_client_should_passthrough_project_import_failed(monkeypatch, tmp_path):
    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            del adapter, file_path, content
            return LspDiagnosticsResult(
                status="project_import_failed",
                lsp_language="java",
                lsp_server="jdtls",
                lsp_server_pid=123,
                java_project_issue_code="java_model_exception_969",
                java_project_state="partial_java_model",
                java_maven_profiles=("hna",),
                java_maven_profiles_source="auto_detected",
                java_maven_local_repository="/custom/maven/repository",
                lsp_error="Java 工程导入失败，Maven 本地仓库不可写：/Users/liurui/.m2/repository/... Operation not permitted",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    result = LspClient().collect_diagnostics(file_path=tmp_path / "Foo.java", content="class Foo {}")

    assert result.status == "project_import_failed"
    assert result.java_project_issue_code == "java_model_exception_969"
    assert result.java_project_state == "partial_java_model"
    assert result.java_maven_profiles == ("hna",)
    assert result.java_maven_profiles_source == "auto_detected"
    assert result.java_maven_local_repository == "/custom/maven/repository"
    assert "Maven 本地仓库不可写" in (result.lsp_error or "")


def test_lsp_client_should_passthrough_debug_observation_fields(monkeypatch, tmp_path):
    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            del adapter, file_path, content
            return LspDiagnosticsResult(
                status="timeout_degraded",
                lsp_language="java",
                lsp_server="jdtls",
                java_debug_observation_enabled=True,
                debug_status_events="1:Starting:Refreshing '/instruction-service/src/main/java'.",
                debug_log_events="2:1:Error in Java Model (code 969)",
                debug_publish_events="3:src/Foo.java#1(0)",
                debug_issue_probe="contains_code_969=True",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    result = LspClient().collect_diagnostics(file_path=tmp_path / "Foo.java", content="class Foo {}")

    assert result.java_debug_observation_enabled is True
    assert "code 969" in result.debug_log_events


def test_lsp_client_should_route_query_to_manager(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeManager:
        def execute_operation(self, adapter, *, operation, file_path, content, line, character):
            captured.update(
                language=adapter.language,
                operation=operation,
                file_path=file_path,
                content=content,
                line=line,
                character=character,
            )
            return LspQueryResult(
                status="completed",
                operation=operation,
                result=[{"name": "Foo"}],
                lsp_language="java",
                lsp_server="jdtls",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    target = tmp_path / "Foo.java"
    result = LspClient().query(
        operation="hover",
        file_path=target,
        content="class Foo {}",
        line=2,
        character=3,
    )

    assert result.status == "completed"
    assert result.result == [{"name": "Foo"}]
    assert captured == {
        "language": "java",
        "operation": "hover",
        "file_path": target,
        "content": "class Foo {}",
        "line": 2,
        "character": 3,
    }


def test_lsp_client_should_return_not_enabled_for_query_when_global_switch_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings(enabled=False))

    result = LspClient().query(
        operation="hover",
        file_path=tmp_path / "Foo.java",
        content="class Foo {}",
        line=0,
        character=0,
    )

    assert result.status == "not_enabled"
    assert result.operation == "hover"


def test_lsp_client_should_passthrough_query_server_unavailable(monkeypatch, tmp_path):
    class FakeManager:
        def execute_operation(self, adapter, *, operation, file_path, content, line, character):
            del adapter, operation, file_path, content, line, character
            raise RuntimeError("boom")

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    target = tmp_path / "Foo.java"
    result = LspClient().query(
        operation="goToDefinition",
        file_path=target,
        content="class Foo {}",
        line=0,
        character=0,
    )

    assert result.status == "server_unavailable"
    assert result.operation == "goToDefinition"
    assert result.lsp_language == "java"
    assert result.lsp_server == "jdtls"
    assert result.lsp_error == "boom"


def test_filter_diagnostics_should_dedupe_and_truncate():
    duplicate = LspDiagnostic(
        severity="error",
        code="E1",
        source="jdtls",
        message="very long error message that should be truncated",
        range=LspRange(
            start=LspPosition(line=1, character=1),
            end=LspPosition(line=1, character=2),
        ),
    )
    warning = LspDiagnostic(
        severity="warning",
        code="W1",
        source="jdtls",
        message="warning",
        range=LspRange(
            start=LspPosition(line=2, character=1),
            end=LspPosition(line=2, character=2),
        ),
    )

    result = filter_diagnostics(
        [duplicate, duplicate, warning],
        include_severity=("error", "warning"),
        max_diagnostics=2,
        max_chars=16,
        lsp_language="java",
        lsp_server="jdtls",
        lsp_server_pid=1,
    )

    assert result.status == "completed"
    assert len(result.diagnostics) == 1
    assert result.diagnostics_truncated is True
    assert result.diagnostics[0].message.endswith("…")


def test_filter_diagnostics_should_keep_wait_metadata():
    diagnostic = LspDiagnostic(
        severity="warning",
        code="W1",
        source="jdtls",
        message="warning",
        range=LspRange(
            start=LspPosition(line=2, character=1),
            end=LspPosition(line=2, character=2),
        ),
    )

    result = filter_diagnostics(
        [diagnostic],
        include_severity=("error", "warning"),
        max_diagnostics=2,
        max_chars=40,
        lsp_language="java",
        lsp_server="jdtls",
        lsp_server_pid=1,
        diagnostics_sequence=4,
        diagnostics_previous_sequence=2,
        diagnostics_latest_sequence=4,
        diagnostics_wait_rounds=2,
        diagnostics_wait_ms=910,
        diagnostics_settled=True,
        lsp_workspace_root="/tmp/project",
        lsp_workspace_selection_reason="maven_aggregator_root",
        lsp_server_key="java:/tmp/project:direct_lsp",
        lsp_snapshot_uri="file:///tmp/project/src/Foo.java",
        recent_status_summary="Starting:Init...",
        recent_log_summary="1:build running",
        recent_publish_uris="src/Foo.java#4(1)",
        received_other_file_diagnostics=False,
    )

    assert result.raw_diagnostics_total == 1
    assert result.diagnostics_sequence == 4
    assert result.diagnostics_previous_sequence == 2
    assert result.diagnostics_latest_sequence == 4
    assert result.diagnostics_wait_rounds == 2
    assert result.diagnostics_wait_ms == 910
    assert result.diagnostics_settled is True
    assert result.lsp_workspace_root == "/tmp/project"
    assert result.lsp_workspace_selection_reason == "maven_aggregator_root"


def test_lsp_client_should_route_python_file_to_manager(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeManager:
        def collect_diagnostics(self, adapter, *, file_path, content):
            captured["language"] = adapter.language
            captured["server_name"] = adapter.server_name
            captured["file_path"] = file_path
            captured["content"] = content
            return LspDiagnosticsResult(
                status="completed",
                diagnostics=(),
                diagnostics_total=0,
                lsp_language="python",
                lsp_server="pylsp",
            )

    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.client.get_lsp_manager", lambda: FakeManager())

    target = tmp_path / "foo.py"
    result = LspClient().collect_diagnostics(file_path=target, content="def foo(): pass")

    assert result.status == "completed"
    assert captured["language"] == "python"
    assert captured["server_name"] == "pylsp"
    assert captured["file_path"] == target
    assert result.lsp_language == "python"
    assert result.lsp_server == "pylsp"


def test_lsp_client_should_return_not_enabled_for_python_when_python_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.lsp.client.get_lsp_settings", lambda: _build_lsp_settings(python_enabled=False))

    result = LspClient().collect_diagnostics(file_path=tmp_path / "foo.py", content="def foo(): pass")

    assert result.status == "not_enabled"
    assert result.diagnostics == ()
