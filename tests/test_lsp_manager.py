import threading
from dataclasses import replace
from pathlib import Path

import pytest

from agent.config.settings import LspIdeSettings, LspLanguageSettings, LspSettings
from agent.lsp.documents import clear_document_store
import agent.lsp.manager as manager_module
from agent.lsp.manager import LspManager
from agent.lsp.servers.jdtls import JdtlsServerAdapter
from agent.runtime.workspace import configure_workspace


def _build_lsp_settings(*, ttl_seconds: int = 60) -> LspSettings:
    return LspSettings(
        enabled=True,
        ide_enabled=False,
        startup_mode="on_demand",
        server_idle_ttl_seconds=ttl_seconds,
        request_timeout_ms=1000,
        max_diagnostics=20,
        max_chars=4000,
        include_severity=("error", "warning"),
        strict_unavailable=False,
        languages={
            "java": LspLanguageSettings(
                enabled=True,
                command=(
                    "/usr/bin/env",
                    "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
                    "jdtls",
                ),
                file_extensions=(".java",),
                workspace_markers=("pom.xml", "build.gradle"),
                init_options={},
                maven_local_repository="",
            ),
            "python": LspLanguageSettings(
                enabled=False,
                command=(),
                file_extensions=(".py",),
                workspace_markers=("pyproject.toml",),
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


def _build_lsp_settings_with_stable_window(*, ttl_seconds: int = 60, stable_window_ms: int = 200) -> LspSettings:
    return replace(
        _build_lsp_settings(ttl_seconds=ttl_seconds),
        diagnostics_stable_window_ms=stable_window_ms,
        diagnostics_max_wait_rounds=4,
    )


class _FakeProcess:
    _next_pid = 100

    def __init__(self):
        self.pid = _FakeProcess._next_pid
        _FakeProcess._next_pid += 1
        self.stdin = object()
        self.stdout = object()
        self.stderr = object()
        self._terminated = False

    def poll(self):
        return 0 if self._terminated else None

    def terminate(self):
        self._terminated = True


class _FakeEndpoint:
    def __init__(self, process, *, notification_handler):
        self.process = process
        self.notification_handler = notification_handler
        self.requests = []
        self.notifications = []
        self.closed = False

    def request(self, method, params, *, timeout_ms):
        self.requests.append((method, params, timeout_ms))
        return {}

    def notify(self, method, params):
        self.notifications.append((method, params))

    def is_alive(self):
        return not self.closed and self.process.poll() is None

    def close(self):
        self.closed = True


class _NotifyingInitializeEndpoint(_FakeEndpoint):
    def request(self, method, params, *, timeout_ms):
        self.requests.append((method, params, timeout_ms))
        if method == "initialize":
            self.notification_handler("language/status", {"type": "Starting", "message": "Init..."})
        return {}


class _EarlyEventInitializeEndpoint(_FakeEndpoint):
    def __init__(self, process, *, notification_handler):
        super().__init__(process, notification_handler=notification_handler)
        self._uri = ""

    def request(self, method, params, *, timeout_ms):
        self.requests.append((method, params, timeout_ms))
        if method == "initialize":
            workspace_folders = params.get("workspaceFolders") or []
            if workspace_folders:
                self._uri = str(workspace_folders[0].get("uri", ""))
            target_uri = f"{self._uri}/Foo.java" if self._uri else "file:///tmp/Foo.java"
            self.notification_handler("language/status", {"type": "Starting", "message": "Init..."})
            self.notification_handler("window/logMessage", {"type": 3, "message": "Booting workspace"})
            self.notification_handler(
                "textDocument/publishDiagnostics",
                {
                    "uri": target_uri,
                    "diagnostics": [
                        {
                            "severity": 2,
                            "message": "early warning",
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 1},
                            },
                        }
                    ],
                },
            )
        return {}


class _BlockingInitializeEndpoint(_FakeEndpoint):
    wait_event = threading.Event()
    created = []

    def __init__(self, process, *, notification_handler):
        super().__init__(process, notification_handler=notification_handler)
        self.__class__.created.append(self)

    def request(self, method, params, *, timeout_ms):
        self.requests.append((method, params, timeout_ms))
        if method == "initialize":
            self.wait_event.wait(timeout=1)
        return {}


class _FailingInitializeEndpoint(_FakeEndpoint):
    def request(self, method, params, *, timeout_ms):
        self.requests.append((method, params, timeout_ms))
        if method == "initialize":
            raise TimeoutError("LSP 请求超时: initialize")
        return {}


@pytest.fixture
def _patched_manager_env(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    processes = []

    def _fake_popen(*args, **kwargs):
        process = _FakeProcess()
        processes.append((args, kwargs, process))
        return process

    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", _fake_popen)
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _FakeEndpoint)
    return processes


def test_lsp_manager_should_reuse_server_in_same_workspace(_patched_manager_env, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pom.xml").write_text("<project/>", encoding="utf-8")
    file_one = project_root / "src" / "Foo.java"
    file_one.parent.mkdir(parents=True, exist_ok=True)
    file_one.write_text("class Foo {}", encoding="utf-8")
    file_two = project_root / "src" / "Bar.java"
    file_two.write_text("class Bar {}", encoding="utf-8")

    first = manager.get_or_start(adapter, file_path=file_one)
    second = manager.get_or_start(adapter, file_path=file_two)

    assert first is second
    assert len(_patched_manager_env) == 1


def test_lsp_manager_should_send_did_open_then_did_change(_patched_manager_env, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    manager.sync_document(server, file_path=file_path, content="class Foo {}")
    manager.sync_document(server, file_path=file_path, content="class Bar {}")

    assert server.endpoint.notifications[1][0] == "textDocument/didOpen"
    assert server.endpoint.notifications[2][0] == "textDocument/didChange"


def test_lsp_manager_should_retry_with_did_save_after_timeout(_patched_manager_env, monkeypatch, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    diagnostics = []
    calls = {"count": 0}

    def fake_wait(server, *, snapshot_uri, previous_sequence, timeout_ms, settle_ms):
        del server, snapshot_uri, previous_sequence, settle_ms
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError(f"等待诊断超时: {file_path.resolve().as_uri()}")
        diagnostics.append(timeout_ms)
        return manager_module._DiagnosticsWaitResult(
            diagnostics=[],
            sequence=1,
            wait_rounds=0,
            wait_ms=1000,
            settled=True,
        )

    monkeypatch.setattr(manager, "_wait_for_diagnostics_or_issue", fake_wait)

    result = manager.collect_diagnostics(adapter, file_path=file_path, content="class Foo {}")

    assert result.status == "filtered_empty"
    assert calls["count"] == 2
    assert server.endpoint.notifications[-1][0] == "textDocument/didSave"
    assert diagnostics == [1000]
    assert result.diagnostics_sequence == 1
    assert result.diagnostics_settled is True


def test_lsp_manager_should_wait_for_java_diagnostics_to_settle(_patched_manager_env, monkeypatch, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    snapshot = manager.sync_document(server, file_path=file_path, content="class Foo {}")
    first = manager_module._PublishedDiagnostics(
        diagnostics=[
            manager_module._convert_diagnostic(
                {
                    "severity": 2,
                    "message": "first warning",
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                }
            )
        ],
        sequence=1,
        updated_at_ns=1,
    )
    second = manager_module._PublishedDiagnostics(
        diagnostics=[
            manager_module._convert_diagnostic(
                {
                    "severity": 1,
                    "message": "later error",
                    "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 1}},
                }
            )
        ],
        sequence=2,
        updated_at_ns=2,
    )

    with server.condition:
        server.diagnostics_by_uri[snapshot.uri] = first

    def publish_later():
        with server.condition:
            server.diagnostics_by_uri[snapshot.uri] = second
            server.condition.notify_all()

    timer = threading.Timer(0.05, publish_later)
    timer.start()
    monkeypatch.setattr(
        "agent.lsp.manager.get_lsp_settings",
        lambda: _build_lsp_settings_with_stable_window(stable_window_ms=200),
    )

    try:
        result = manager._wait_for_diagnostics_or_issue(
            server,
            snapshot_uri=snapshot.uri,
            previous_sequence=0,
            timeout_ms=1000,
            settle_ms=0,
        )
    finally:
        timer.cancel()

    assert isinstance(result, manager_module._DiagnosticsWaitResult)
    assert result.sequence == 2
    assert result.wait_rounds == 1
    assert result.settled is True
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].message == "later error"


def test_lsp_manager_should_not_deadlock_when_initialize_emits_notification(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _NotifyingInitializeEndpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    result_holder = {}
    error_holder = {}

    def run_get_or_start():
        try:
            result_holder["server"] = manager.get_or_start(adapter, file_path=file_path)
        except Exception as exc:  # pragma: no cover - 失败时用于断言
            error_holder["error"] = exc

    thread = threading.Thread(target=run_get_or_start)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert "error" not in error_holder
    assert result_holder["server"].status.server_name == "jdtls"


def test_lsp_manager_should_preserve_initialize_notifications_before_server_registration(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _EarlyEventInitializeEndpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    published = server.get_published(file_path.resolve().as_uri())

    assert len(server.status_events) == 1
    assert server.status_events[0].message == "Init..."
    assert len(server.log_events) == 1
    assert server.log_events[0].message == "Booting workspace"
    assert len(server.publish_events) == 1
    assert server.publish_events[0].uri == file_path.resolve().as_uri()
    assert published.sequence == 1
    assert len(published.diagnostics) == 1
    assert published.diagnostics[0].message == "early warning"


def test_lsp_manager_should_share_single_startup_for_same_workspace(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    processes = []

    def fake_popen(*args, **kwargs):
        process = _FakeProcess()
        processes.append(process)
        return process

    _BlockingInitializeEndpoint.wait_event = threading.Event()
    _BlockingInitializeEndpoint.created = []
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", fake_popen)
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _BlockingInitializeEndpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pom.xml").write_text("<project/>", encoding="utf-8")
    file_one = project_root / "src" / "Foo.java"
    file_one.parent.mkdir(parents=True, exist_ok=True)
    file_one.write_text("class Foo {}", encoding="utf-8")
    file_two = project_root / "src" / "Bar.java"
    file_two.write_text("class Bar {}", encoding="utf-8")

    results = []

    def run(target_file):
        results.append(manager.get_or_start(adapter, file_path=target_file))

    first = threading.Thread(target=run, args=(file_one,))
    second = threading.Thread(target=run, args=(file_two,))
    first.start()
    second.start()

    while len(_BlockingInitializeEndpoint.created) != 1:
        pass
    _BlockingInitializeEndpoint.wait_event.set()

    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(processes) == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_lsp_manager_should_allow_restart_after_initialize_failure(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    processes = []

    def fake_popen(*args, **kwargs):
        process = _FakeProcess()
        processes.append(process)
        return process

    endpoint_factories = [_FailingInitializeEndpoint, _FakeEndpoint]

    def fake_endpoint(process, *, notification_handler):
        endpoint_cls = endpoint_factories.pop(0)
        return endpoint_cls(process, notification_handler=notification_handler)

    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", fake_popen)
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", fake_endpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    with pytest.raises(TimeoutError, match="initialize"):
        manager.get_or_start(adapter, file_path=file_path)

    server = manager.get_or_start(adapter, file_path=file_path)

    assert len(processes) == 2
    assert server.status.server_name == "jdtls"
    assert manager._starting_servers == {}


def test_lsp_manager_should_return_timeout_degraded_after_retry_timeout(_patched_manager_env, monkeypatch, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    server.append_status_event("Starting", "Init...")
    server.append_log_event("1", "still indexing")
    with server.condition:
        server.publish_events.append(
            manager_module._PublishEvent(
                uri=(tmp_path / "Bar.java").resolve().as_uri(),
                diagnostics_count=2,
                sequence=1,
                updated_at_ns=1,
            )
        )

    def fake_wait(server, *, snapshot_uri, previous_sequence, timeout_ms, settle_ms):
        del server, snapshot_uri, previous_sequence, timeout_ms, settle_ms
        raise TimeoutError("等待诊断超时")

    monkeypatch.setattr(manager, "_wait_for_diagnostics_or_issue", fake_wait)

    result = manager.collect_diagnostics(adapter, file_path=file_path, content="class Foo {}")

    assert result.status == "timeout_degraded"
    assert result.lsp_server == "jdtls"
    assert result.lsp_workspace_root == str(tmp_path.resolve())
    assert result.lsp_workspace_selection_reason == "workspace_boundary_fallback"
    assert result.lsp_server_key == server.status.server_key
    assert result.diagnostics_previous_sequence == 0
    assert result.diagnostics_latest_sequence == 0
    assert result.recent_status_summary == "Starting:Init..."
    assert result.recent_log_summary == "1:still indexing"
    assert "Bar.java" in result.recent_publish_uris
    assert result.received_other_file_diagnostics is True
    assert "didSave" in (result.lsp_error or "")
    assert server.endpoint.notifications[-1][0] == "textDocument/didSave"


def test_lsp_manager_should_return_project_import_failed_when_m2_not_writable(_patched_manager_env, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)

    def emit_issue():
        manager._handle_message(
            server.status.server_key,
            "window/logMessage",
            {
                "type": 1,
                "message": (
                    "Initialization failed\n"
                    "java.nio.file.FileSystemException: "
                    "/Users/liurui/.m2/repository/org/apache/maven/plugins/maven-compiler-plugin/3.13.0: "
                    "Operation not permitted"
                ),
            },
        )

    timer = threading.Timer(0.01, emit_issue)
    timer.start()
    try:
        result = manager.collect_diagnostics(adapter, file_path=file_path, content="class Foo {}")
    finally:
        timer.cancel()

    assert result.status == "project_import_failed"
    assert "Maven 本地仓库不可写" in (result.lsp_error or "")
    assert ".m2" in (result.lsp_error or "")


def test_lsp_manager_should_return_project_import_failed_when_file_not_compilation_unit(_patched_manager_env, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)

    def emit_issue():
        manager._handle_message(
            server.status.server_key,
            "window/logMessage",
            {
                "type": 1,
                "message": f"{file_path.resolve().as_uri()} does not resolve to a ICompilationUnit",
            },
        )

    timer = threading.Timer(0.01, emit_issue)
    timer.start()
    try:
        result = manager.collect_diagnostics(adapter, file_path=file_path, content="class Foo {}")
    finally:
        timer.cancel()

    assert result.status == "project_import_failed"
    assert "还未进入编译单元" in (result.lsp_error or "")
    assert result.lsp_workspace_root == str(tmp_path.resolve())
    assert result.lsp_workspace_selection_reason == "workspace_boundary_fallback"
    assert result.lsp_server_key == server.status.server_key
    assert "Foo.java" in (result.lsp_snapshot_uri or "")


def test_lsp_manager_should_override_completed_with_project_import_failed_on_java_model_969(
    _patched_manager_env,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "agent.lsp.manager.get_lsp_settings",
        lambda: _build_lsp_settings_with_stable_window(stable_window_ms=20),
    )
    monkeypatch.setattr(
        "agent.lsp.servers.base.get_lsp_settings",
        lambda: _build_lsp_settings_with_stable_window(stable_window_ms=20),
    )
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")
    server = manager.get_or_start(adapter, file_path=file_path)

    manager._handle_message(
        server.status.server_key,
        "textDocument/publishDiagnostics",
        {"uri": (tmp_path / "Bar.java").resolve().as_uri(), "diagnostics": [{"severity": 2, "message": "warn", "range": {}}]},
    )
    def emit_target_publish():
        manager._handle_message(
            server.status.server_key,
            "textDocument/publishDiagnostics",
            {
                "uri": file_path.resolve().as_uri(),
                "diagnostics": [
                    {
                        "severity": 2,
                        "message": "shallow warning",
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                    }
                ],
            },
        )

    def emit_java_model_issue():
        manager._handle_message(
            server.status.server_key,
            "window/logMessage",
            {
                "type": 1,
                "message": (
                    "src/main/java/com/example [in instruction-service] does not exist Java Model Exception: "
                    "Error in Java Model (code 969): src/main/java/com/example"
                ),
            },
        )

    publish_timer = threading.Timer(0.01, emit_target_publish)
    issue_timer = threading.Timer(0.02, emit_java_model_issue)
    publish_timer.start()
    issue_timer.start()
    try:
        result = manager.collect_diagnostics(adapter, file_path=file_path, content="class Foo {}")
    finally:
        publish_timer.cancel()
        issue_timer.cancel()

    assert result.status == "project_import_failed"
    assert result.diagnostics_total == 0
    assert result.raw_diagnostics_total == 0
    assert result.java_project_issue_code == "java_model_exception_969"
    assert result.java_project_state == "partial_java_model"
    assert "当前源码包尚未进入 Java Model" in (result.lsp_error or "")


def test_lsp_manager_should_auto_detect_maven_profile_before_start(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _FakeEndpoint)

    aggregator_root = tmp_path / "flight-instruction"
    module_root = aggregator_root / "instruction-service"
    java_file = module_root / "src" / "main" / "java" / "com" / "huoli" / "flight" / "channel" / "hna" / "nineh" / "b2c" / "Air9hB2cConvertUtil.java"
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text("class Air9hB2cConvertUtil {}", encoding="utf-8")
    (aggregator_root / "pom.xml").write_text(
        """
        <project>
          <packaging>pom</packaging>
          <modules>
            <module>instruction-service</module>
          </modules>
        </project>
        """.strip(),
        encoding="utf-8",
    )
    (module_root / "pom.xml").write_text(
        """
        <project>
          <profiles>
            <profile>
              <id>airchina</id>
              <activation><activeByDefault>true</activeByDefault></activation>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <excludes>
                        <exclude>**/channel/hna/**</exclude>
                      </excludes>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </profile>
            <profile>
              <id>hna</id>
              <activation><activeByDefault>true</activeByDefault></activation>
            </profile>
          </profiles>
        </project>
        """.strip(),
        encoding="utf-8",
    )

    adapter = JdtlsServerAdapter()
    manager = LspManager()

    result = manager.collect_diagnostics(adapter, file_path=java_file, content="class Air9hB2cConvertUtil {}")

    assert result.status == "timeout_degraded"
    assert result.lsp_workspace_selection_reason == "maven_aggregator_root"
    assert result.java_maven_profiles == ("hna",)
    assert result.java_maven_profiles_source == "auto_detected"


def test_lsp_manager_should_detect_maven_profile_conflict_before_start_when_auto_detect_is_ambiguous(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings())
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())

    aggregator_root = tmp_path / "flight-instruction"
    module_root = aggregator_root / "instruction-service"
    java_file = module_root / "src" / "main" / "java" / "com" / "huoli" / "flight" / "carrier" / "AirCarrierConvertUtil.java"
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text("class AirCarrierConvertUtil {}", encoding="utf-8")
    (aggregator_root / "pom.xml").write_text(
        """
        <project>
          <packaging>pom</packaging>
          <modules>
            <module>instruction-service</module>
          </modules>
        </project>
        """.strip(),
        encoding="utf-8",
    )
    (module_root / "pom.xml").write_text(
        """
        <project>
          <profiles>
            <profile>
              <id>airchina</id>
              <activation><activeByDefault>true</activeByDefault></activation>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <excludes>
                        <exclude>**/carrier/**</exclude>
                      </excludes>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </profile>
            <profile>
              <id>hna</id>
              <activation><activeByDefault>true</activeByDefault></activation>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <excludes>
                        <exclude>**/carrier/**</exclude>
                      </excludes>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </profile>
            <profile>
              <id>ceshi</id>
            </profile>
          </profiles>
        </project>
        """.strip(),
        encoding="utf-8",
    )

    adapter = JdtlsServerAdapter()
    manager = LspManager()

    result = manager.collect_diagnostics(adapter, file_path=java_file, content="class AirCarrierConvertUtil {}")

    assert result.status == "project_import_failed"
    assert result.java_project_issue_code == "maven_profile_conflict"
    assert result.java_project_state == "profile_conflict"
    assert result.lsp_workspace_selection_reason == "maven_aggregator_root"
    assert result.java_maven_profiles == ()
    assert result.java_maven_profiles_source == ""
    assert "自动探测规则" in (result.lsp_error or "")


def test_jdtls_should_include_maven_local_repository_in_server_key_and_initialize_params(monkeypatch, tmp_path):
    settings = replace(
        _build_lsp_settings(),
        languages={
            **_build_lsp_settings().languages,
            "java": replace(
                _build_lsp_settings().languages["java"],
                maven_local_repository="/custom/maven/repository",
            ),
        },
    )
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: settings)
    adapter = JdtlsServerAdapter()
    workspace_root = tmp_path.resolve()

    server_key = adapter.build_server_key(workspace_root)
    params = adapter.build_initialize_params(workspace_root)

    assert "maven_local_repository=/custom/maven/repository" in server_key
    user_settings = (
        params["initializationOptions"]["settings"]["java"]["configuration"]["maven"]["userSettings"]
    )
    assert str(user_settings).endswith("maven-user-settings.xml")
    user_settings_content = Path(user_settings).read_text(encoding="utf-8")
    assert "/custom/maven/repository" in user_settings_content


def test_jdtls_should_isolate_data_dir_by_server_key(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    base_settings = _build_lsp_settings()
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: base_settings)

    default_adapter = JdtlsServerAdapter()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "pom.xml").write_text("<project/>", encoding="utf-8")
    default_server_key = default_adapter.build_server_key(workspace_root)
    default_dir = default_adapter.build_data_dir(workspace_root)

    repository_overridden_settings = replace(
        base_settings,
        languages={
            **base_settings.languages,
            "java": replace(
                base_settings.languages["java"],
                maven_local_repository="/custom/maven/repository",
            ),
        },
    )
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: repository_overridden_settings)

    repository_adapter = JdtlsServerAdapter()
    repository_server_key = repository_adapter.build_server_key(workspace_root)
    repository_dir = repository_adapter.build_data_dir(workspace_root)

    assert default_server_key != repository_server_key
    assert default_dir != repository_dir


def test_jdtls_should_auto_detect_profile_into_server_key_and_initialize_params(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings())
    adapter = JdtlsServerAdapter()
    workspace_root = tmp_path / "flight-instruction"
    module_root = workspace_root / "instruction-service"
    java_file = module_root / "src" / "main" / "java" / "com" / "demo" / "channel" / "hna" / "Foo.java"
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text("class Foo {}", encoding="utf-8")
    (workspace_root / "pom.xml").write_text(
        "<project><packaging>pom</packaging><modules><module>instruction-service</module></modules></project>",
        encoding="utf-8",
    )
    (module_root / "pom.xml").write_text(
        """
        <project>
          <profiles>
            <profile>
              <id>airchina</id>
              <activation><activeByDefault>true</activeByDefault></activation>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <excludes>
                        <exclude>**/channel/hna/**</exclude>
                      </excludes>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </profile>
            <profile><id>hna</id></profile>
          </profiles>
        </project>
        """.strip(),
        encoding="utf-8",
    )

    server_key = adapter.build_server_key(workspace_root, file_path=java_file)
    params = adapter.build_initialize_params(workspace_root, file_path=java_file)
    resolved = adapter.resolve_maven_import_config(file_path=java_file, workspace_root=workspace_root)

    assert resolved.profiles == ("hna",)
    assert resolved.profiles_source == "auto_detected"
    assert "maven_profiles=hna" in server_key
    user_settings = params["initializationOptions"]["settings"]["java"]["configuration"]["maven"]["userSettings"]
    user_settings_content = Path(user_settings).read_text(encoding="utf-8")
    assert "hna" in user_settings_content


def test_jdtls_should_select_topmost_maven_aggregator_root(tmp_path):
    adapter = JdtlsServerAdapter()
    aggregator_root = tmp_path / "ai-workspacec"
    flight_root = aggregator_root / "flight-instruction"
    module_root = flight_root / "instruction-service"
    java_file = module_root / "src" / "main" / "java" / "com" / "example" / "Foo.java"
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text("class Foo {}", encoding="utf-8")

    (aggregator_root / "pom.xml").write_text(
        """
        <project>
          <modelVersion>4.0.0</modelVersion>
          <packaging>pom</packaging>
          <modules>
            <module>flight-instruction</module>
          </modules>
        </project>
        """.strip(),
        encoding="utf-8",
    )
    (flight_root / "pom.xml").write_text(
        """
        <project>
          <modelVersion>4.0.0</modelVersion>
          <packaging>pom</packaging>
          <modules>
            <module>instruction-service</module>
          </modules>
        </project>
        """.strip(),
        encoding="utf-8",
    )
    (module_root / "pom.xml").write_text(
        """
        <project>
          <modelVersion>4.0.0</modelVersion>
          <artifactId>instruction-service</artifactId>
        </project>
        """.strip(),
        encoding="utf-8",
    )

    root, reason = adapter.select_workspace_root_with_reason(java_file, aggregator_root)

    assert root == aggregator_root.resolve()
    assert reason == "maven_aggregator_root"


def test_lsp_manager_should_include_debug_observation_fields_when_enabled(_patched_manager_env, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent.lsp.manager.get_lsp_settings",
        lambda: replace(_build_lsp_settings(), java_debug_observation_enabled=True),
    )
    monkeypatch.setattr(
        "agent.lsp.servers.base.get_lsp_settings",
        lambda: replace(_build_lsp_settings(), java_debug_observation_enabled=True),
    )
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")
    server = manager.get_or_start(adapter, file_path=file_path)

    manager._handle_message(
        server.status.server_key,
        "language/status",
        {"type": "Starting", "message": "Refreshing '/instruction-service/src/main/java'."},
    )
    manager._handle_message(
        server.status.server_key,
        "window/logMessage",
        {
            "type": 1,
            "message": "src/main/java/com/example [in instruction-service] does not exist Java Model Exception: "
            "Error in Java Model (code 969): src/main/java/com/example",
        },
    )
    manager._handle_message(
        server.status.server_key,
        "textDocument/publishDiagnostics",
        {"uri": file_path.resolve().as_uri(), "diagnostics": []},
    )

    fields = manager._build_observation_fields(server, snapshot_uri=file_path.resolve().as_uri())

    assert fields["java_debug_observation_enabled"] is True
    assert "Refreshing '/instruction-service/src/main/java'." in fields["debug_status_events"]
    assert "Error in Java Model (code 969)" in fields["debug_log_events"]
    assert "Foo.java#1(0)" in fields["debug_publish_events"]
    assert "contains_code_969=True" in fields["debug_issue_probe"]


def test_jdtls_should_fallback_to_nearest_maven_module_when_no_aggregator(tmp_path):
    adapter = JdtlsServerAdapter()
    module_root = tmp_path / "instruction-service"
    java_file = module_root / "src" / "main" / "java" / "com" / "example" / "Foo.java"
    java_file.parent.mkdir(parents=True, exist_ok=True)
    java_file.write_text("class Foo {}", encoding="utf-8")
    (module_root / "pom.xml").write_text(
        """
        <project>
          <modelVersion>4.0.0</modelVersion>
          <artifactId>instruction-service</artifactId>
        </project>
        """.strip(),
        encoding="utf-8",
    )

    root, reason = adapter.select_workspace_root_with_reason(java_file, tmp_path)

    assert root == module_root.resolve()
    assert reason == "maven_nearest_module"


def test_lsp_manager_should_raise_runtime_error_when_process_exits_before_diagnostics(_patched_manager_env, monkeypatch, tmp_path):
    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)

    def fake_poll():
        return 17

    monkeypatch.setattr(server.process, "poll", fake_poll)

    with pytest.raises(RuntimeError, match="exit_code=17"):
        server.wait_for_diagnostics(file_path.resolve().as_uri(), previous_sequence=0, timeout_ms=1000, settle_ms=0)


def test_lsp_manager_should_cleanup_idle_server(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings(ttl_seconds=1))
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings(ttl_seconds=1))
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _FakeEndpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    server = manager.get_or_start(adapter, file_path=file_path)
    server.last_used_at_ns = 0
    manager.cleanup_idle_servers()

    assert server.process.poll() == 0


def test_lsp_manager_should_reopen_document_after_idle_cleanup(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    clear_document_store()
    monkeypatch.setattr("agent.lsp.manager.get_lsp_settings", lambda: _build_lsp_settings(ttl_seconds=1))
    monkeypatch.setattr("agent.lsp.servers.base.get_lsp_settings", lambda: _build_lsp_settings(ttl_seconds=1))
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._resolve_executable",
        lambda self, executable, launch_env: "/opt/homebrew/bin/jdtls",
    )
    monkeypatch.setattr(
        "agent.lsp.servers.jdtls.JdtlsServerAdapter._detect_java_major_version",
        lambda self, java_command, launch_env: 21,
    )
    monkeypatch.setattr("agent.lsp.manager.subprocess.Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr("agent.lsp.manager.JsonRpcEndpoint", _FakeEndpoint)

    adapter = JdtlsServerAdapter()
    manager = LspManager()
    file_path = tmp_path / "Foo.java"
    file_path.write_text("class Foo {}", encoding="utf-8")

    first_server = manager.get_or_start(adapter, file_path=file_path)
    manager.sync_document(first_server, file_path=file_path, content="class Foo {}")
    first_server.last_used_at_ns = 0

    second_server = manager.get_or_start(adapter, file_path=file_path)
    manager.sync_document(second_server, file_path=file_path, content="class Bar {}")

    assert first_server is not second_server
    assert second_server.endpoint.notifications[1][0] == "textDocument/didOpen"


def test_jdtls_server_key_should_include_adapter_mode(tmp_path):
    adapter = JdtlsServerAdapter()

    class IdeAdapter(JdtlsServerAdapter):
        adapter_mode = "ide_proxy"

    workspace_root = tmp_path.resolve()
    direct_key = adapter.build_server_key(workspace_root)
    ide_key = IdeAdapter().build_server_key(workspace_root)

    assert direct_key != ide_key


def test_jdtls_should_support_env_wrapped_command(monkeypatch):
    adapter = JdtlsServerAdapter()
    monkeypatch.setattr(
        adapter,
        "_resolve_executable",
        lambda executable, launch_env: "/opt/homebrew/bin/jdtls" if executable == "jdtls" else None,
    )
    monkeypatch.setattr(
        adapter,
        "_detect_java_major_version",
        lambda java_command, launch_env: 21,
    )

    launch_env, executable_tokens = adapter._extract_launch_context(
        [
            "/usr/bin/env",
            "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
            "jdtls",
        ]
    )

    assert launch_env["JAVA_HOME"].endswith("/jdk-21.jdk/Contents/Home")
    assert executable_tokens == ["jdtls"]
    adapter._validate_launch_command(
        [
            "/usr/bin/env",
            "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
            "jdtls",
        ]
    )


def test_jdtls_should_reject_java_lower_than_21(monkeypatch):
    adapter = JdtlsServerAdapter()
    monkeypatch.setattr(adapter, "_resolve_executable", lambda executable, launch_env: "/opt/homebrew/bin/jdtls")
    monkeypatch.setattr(adapter, "_resolve_java_command", lambda launch_env: "/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home/bin/java")
    monkeypatch.setattr(adapter, "_detect_java_major_version", lambda java_command, launch_env: 17)

    with pytest.raises(ValueError, match="JDK 21\\+"):
        adapter._validate_launch_command(["jdtls"])


def test_jdtls_build_command_should_append_writable_configuration_dir(monkeypatch, tmp_path):
    configure_workspace(tmp_path)
    adapter = JdtlsServerAdapter()
    monkeypatch.setattr(adapter, "_validate_launch_command", lambda command: None)

    command = adapter.build_command(tmp_path)

    assert "-configuration" in command
    configuration_index = command.index("-configuration") + 1
    configuration_dir = Path(command[configuration_index])
    assert configuration_dir.exists()
    assert str(configuration_dir).startswith("/private/tmp/my-main-agent-test-home/workspaces/")
