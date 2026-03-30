import json

import pytest

from agent.runtime import web_dev_server as web_dev_server_module


class _FakeProcess:
    def __init__(self, pid: int, return_code: int | None = None):
        self.pid = pid
        self.return_code = return_code
        self.terminated = False
        self.killed = False
        self.wait_timeout = None

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.return_code = 0

    def kill(self):
        self.killed = True
        self.return_code = -9

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        return self.return_code


def test_ensure_frontend_dev_prerequisites_should_fail_when_pnpm_missing(monkeypatch, tmp_path):
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "node_modules").mkdir()

    monkeypatch.setattr(web_dev_server_module.shutil, "which", lambda name: None)

    with pytest.raises(web_dev_server_module.WebStackError) as exc_info:
        web_dev_server_module.ensure_frontend_dev_prerequisites(frontend_dir)

    assert "pnpm" in str(exc_info.value)


def test_ensure_frontend_dev_prerequisites_should_fail_when_node_modules_missing(monkeypatch, tmp_path):
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()

    monkeypatch.setattr(web_dev_server_module.shutil, "which", lambda name: "/opt/homebrew/bin/pnpm")

    with pytest.raises(web_dev_server_module.WebStackError) as exc_info:
        web_dev_server_module.ensure_frontend_dev_prerequisites(frontend_dir)

    assert "pnpm install" in str(exc_info.value)


def test_start_web_dev_stack_should_write_state_and_keep_silent_by_default(monkeypatch, tmp_path, capsys):
    backend_process = _FakeProcess(pid=101)
    frontend_process = _FakeProcess(pid=202)

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(web_dev_server_module, "get_web_dev_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(web_dev_server_module, "_ensure_not_running", lambda: None)
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, log_path: backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, log_path: frontend_process,
    )
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", lambda process, endpoint, timeout_seconds=15.0: None)
    monkeypatch.setattr(web_dev_server_module.time, "time", lambda: 123.0)

    state = web_dev_server_module.start_web_dev_stack(workspace_root=tmp_path, host="0.0.0.0", port=8000)

    state_path = tmp_path / "runtime" / web_dev_server_module.STATE_FILENAME
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    output = capsys.readouterr().out

    assert output == ""
    assert state.backend_pid == 101
    assert state.frontend_pid == 202
    assert payload["status"] == "running"
    assert payload["host"] == "0.0.0.0"
    assert payload["port"] == 8000


def test_start_web_dev_stack_should_cleanup_processes_and_append_log_excerpt_on_failure(monkeypatch, tmp_path):
    backend_process = _FakeProcess(pid=101)
    frontend_process = _FakeProcess(pid=202)
    stop_calls: list[str] = []
    runtime_dir = tmp_path / "runtime"

    def fake_wait_for_process_port(process, endpoint, timeout_seconds=15.0):
        if endpoint.name == "前端服务":
            raise web_dev_server_module.WebStackError("前端端口未就绪")

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(web_dev_server_module, "get_web_dev_runtime_dir", lambda: runtime_dir)
    monkeypatch.setattr(web_dev_server_module, "_ensure_not_running", lambda: None)
    def fake_start_backend_dev_server(*, workspace_root, host, port, log_path):
        log_path.write_text("backend failed\n", encoding="utf-8")
        return backend_process

    def fake_start_frontend_dev_server(*, frontend_dir, pnpm_binary, log_path):
        log_path.write_text("frontend failed\n", encoding="utf-8")
        return frontend_process

    monkeypatch.setattr(web_dev_server_module, "start_backend_dev_server", fake_start_backend_dev_server)
    monkeypatch.setattr(web_dev_server_module, "start_frontend_dev_server", fake_start_frontend_dev_server)
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", fake_wait_for_process_port)
    monkeypatch.setattr(
        web_dev_server_module,
        "stop_process",
        lambda process, *, name: stop_calls.append(name),
    )

    with pytest.raises(web_dev_server_module.WebStackError) as exc_info:
        web_dev_server_module.start_web_dev_stack(workspace_root=tmp_path, host="0.0.0.0", port=8000)

    assert "前端端口未就绪" in str(exc_info.value)
    assert "日志摘要" in str(exc_info.value)
    assert stop_calls == ["前端服务", "后端服务"]


def test_get_web_stack_status_should_return_running(monkeypatch):
    state = web_dev_server_module.WebStackState(
        workspace_root="/tmp/project",
        host="0.0.0.0",
        port=8000,
        backend_pid=101,
        frontend_pid=202,
        backend_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:5173",
        backend_log_path="/tmp/backend.log",
        frontend_log_path="/tmp/frontend.log",
        started_at=123.0,
        status="running",
    )

    monkeypatch.setattr(web_dev_server_module, "_load_state", lambda: state)
    monkeypatch.setattr(web_dev_server_module, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(web_dev_server_module, "is_tcp_port_open", lambda host, port: True)

    status, loaded_state = web_dev_server_module.get_web_stack_status()

    assert status == "running"
    assert loaded_state == state


def test_get_web_stack_status_should_return_degraded_when_only_backend_alive(monkeypatch):
    state = web_dev_server_module.WebStackState(
        workspace_root="/tmp/project",
        host="0.0.0.0",
        port=8000,
        backend_pid=101,
        frontend_pid=202,
        backend_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:5173",
        backend_log_path="/tmp/backend.log",
        frontend_log_path="/tmp/frontend.log",
        started_at=123.0,
        status="running",
    )

    monkeypatch.setattr(web_dev_server_module, "_load_state", lambda: state)
    monkeypatch.setattr(web_dev_server_module, "_is_process_alive", lambda pid: pid == 101)
    monkeypatch.setattr(web_dev_server_module, "is_tcp_port_open", lambda host, port: port == 8000)

    status, _ = web_dev_server_module.get_web_stack_status()

    assert status == "degraded"


def test_stop_web_dev_stack_should_stop_both_processes_and_remove_state(monkeypatch, tmp_path):
    state = web_dev_server_module.WebStackState(
        workspace_root=str(tmp_path),
        host="0.0.0.0",
        port=8000,
        backend_pid=101,
        frontend_pid=202,
        backend_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:5173",
        backend_log_path="/tmp/backend.log",
        frontend_log_path="/tmp/frontend.log",
        started_at=123.0,
        status="running",
    )
    stop_calls: list[int] = []
    removed = {"called": False}

    monkeypatch.setattr(web_dev_server_module, "get_web_stack_status", lambda: ("running", state))
    monkeypatch.setattr(web_dev_server_module, "_stop_pid", lambda pid: stop_calls.append(pid))
    monkeypatch.setattr(web_dev_server_module, "_remove_state_file", lambda: removed.update({"called": True}))

    status, stopped_state = web_dev_server_module.stop_web_dev_stack()

    assert status == "stopped"
    assert stopped_state == state
    assert stop_calls == [202, 101]
    assert removed["called"] is True


def test_format_web_stack_status_should_include_runtime_file_paths(monkeypatch, tmp_path):
    state = web_dev_server_module.WebStackState(
        workspace_root=str(tmp_path),
        host="0.0.0.0",
        port=8000,
        backend_pid=101,
        frontend_pid=202,
        backend_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:5173",
        backend_log_path="/tmp/backend.log",
        frontend_log_path="/tmp/frontend.log",
        started_at=123.0,
        status="running",
    )

    monkeypatch.setattr(web_dev_server_module, "get_web_dev_state_path", lambda: tmp_path / "state.json")

    output = web_dev_server_module.format_web_stack_status("running", state)

    assert "状态: 运行中" in output
    assert "状态文件" in output
    assert "后端日志" in output
