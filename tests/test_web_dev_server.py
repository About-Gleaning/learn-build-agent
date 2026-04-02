import json
import subprocess

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


def test_spawn_logged_process_should_detach_stdin_from_terminal(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _CapturedPopen:
        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            self.pid = 123

    log_path = tmp_path / "backend.log"
    monkeypatch.setattr(web_dev_server_module.subprocess, "Popen", _CapturedPopen)

    process = web_dev_server_module._spawn_logged_process(
        ["echo", "hello"],
        cwd=tmp_path,
        log_path=log_path,
    )

    assert process.pid == 123
    assert captured["command"] == ["echo", "hello"]
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


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
    start_args: dict[str, object] = {}

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(web_dev_server_module, "get_web_dev_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(web_dev_server_module, "_ensure_not_running", lambda: None)
    monkeypatch.setattr(
        web_dev_server_module,
        "find_available_port",
        lambda host, preferred_port, attempts=50: preferred_port if preferred_port != 5173 else 5180,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, log_path: start_args.update({"backend_host": host, "backend_port": port})
        or backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, host, port, backend_url, workspace_root, log_path: start_args.update(
            {
                "frontend_host": host,
                "frontend_port": port,
                "frontend_backend_url": backend_url,
                "frontend_workspace_root": str(workspace_root),
            }
        )
        or frontend_process,
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
    assert payload["frontend_port"] == 5180
    assert start_args == {
        "backend_host": "0.0.0.0",
        "backend_port": 8000,
        "frontend_host": "127.0.0.1",
        "frontend_port": 5180,
        "frontend_backend_url": "http://127.0.0.1:8000",
        "frontend_workspace_root": str(tmp_path),
    }


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
    monkeypatch.setattr(
        web_dev_server_module,
        "find_available_port",
        lambda host, preferred_port, attempts=50: preferred_port if preferred_port != 5173 else 5180,
    )

    def fake_start_backend_dev_server(*, workspace_root, host, port, log_path):
        log_path.write_text("backend failed\n", encoding="utf-8")
        return backend_process

    def fake_start_frontend_dev_server(*, frontend_dir, pnpm_binary, host, port, backend_url, workspace_root, log_path):
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


def test_start_web_dev_stack_should_expose_only_frontend_when_share_frontend_enabled(monkeypatch, tmp_path):
    backend_process = _FakeProcess(pid=101)
    frontend_process = _FakeProcess(pid=202)
    start_args: dict[str, object] = {}

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(web_dev_server_module, "get_web_dev_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(web_dev_server_module, "_ensure_not_running", lambda: None)
    monkeypatch.setattr(
        web_dev_server_module,
        "find_available_port",
        lambda host, preferred_port, attempts=50: preferred_port if preferred_port != 5173 else 5180,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, log_path: start_args.update({"backend_host": host, "backend_port": port})
        or backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, host, port, backend_url, workspace_root, log_path: start_args.update(
            {
                "frontend_host": host,
                "frontend_port": port,
                "frontend_backend_url": backend_url,
                "frontend_workspace_root": str(workspace_root),
            }
        )
        or frontend_process,
    )
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", lambda process, endpoint, timeout_seconds=15.0: None)
    monkeypatch.setattr(web_dev_server_module, "resolve_network_host", lambda: "192.168.102.18")
    monkeypatch.setattr(web_dev_server_module.time, "time", lambda: 123.0)

    state = web_dev_server_module.start_web_dev_stack(
        workspace_root=tmp_path,
        host="0.0.0.0",
        port=8000,
        share_frontend=True,
    )

    assert start_args == {
        "backend_host": "127.0.0.1",
        "backend_port": 8000,
        "frontend_host": "0.0.0.0",
        "frontend_port": 5180,
        "frontend_backend_url": "http://127.0.0.1:8000",
        "frontend_workspace_root": str(tmp_path),
    }
    assert state.backend_url == "http://127.0.0.1:8000"
    assert state.frontend_local_url == "http://127.0.0.1:5180"
    assert state.frontend_network_url == "http://192.168.102.18:5180"
    assert state.share_frontend is True


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
        frontend_port=5180,
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
        frontend_port=5180,
    )

    monkeypatch.setattr(web_dev_server_module, "_load_state", lambda: state)
    monkeypatch.setattr(web_dev_server_module, "_is_process_alive", lambda pid: pid == 101)
    monkeypatch.setattr(web_dev_server_module, "is_tcp_port_open", lambda host, port: port in {8000, 5180})

    status, _ = web_dev_server_module.get_web_stack_status()

    assert status == "degraded"


def test_get_web_stack_status_should_return_stale_when_state_only_remains(monkeypatch):
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
        frontend_port=5180,
    )

    monkeypatch.setattr(web_dev_server_module, "_load_state", lambda: state)
    monkeypatch.setattr(web_dev_server_module, "_is_process_alive", lambda pid: False)
    monkeypatch.setattr(web_dev_server_module, "is_tcp_port_open", lambda host, port: False)

    status, loaded_state = web_dev_server_module.get_web_stack_status()

    assert status == "stale"
    assert loaded_state == state


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
        frontend_port=5180,
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


def test_prune_web_dev_stacks_should_remove_only_degraded_and_stale_instances(monkeypatch, tmp_path):
    running_state = web_dev_server_module.WebStackState(
        workspace_root="/tmp/running",
        host="0.0.0.0",
        port=8000,
        backend_pid=101,
        frontend_pid=201,
        backend_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:5173",
        backend_log_path="/tmp/running-backend.log",
        frontend_log_path="/tmp/running-frontend.log",
        started_at=123.0,
        status="running",
        frontend_port=5173,
    )
    degraded_state = web_dev_server_module.WebStackState(
        workspace_root="/tmp/degraded",
        host="0.0.0.0",
        port=8001,
        backend_pid=102,
        frontend_pid=202,
        backend_url="http://127.0.0.1:8001",
        frontend_url="http://127.0.0.1:5174",
        backend_log_path="/tmp/degraded-backend.log",
        frontend_log_path="/tmp/degraded-frontend.log",
        started_at=123.0,
        status="running",
        frontend_port=5174,
    )
    stale_state = web_dev_server_module.WebStackState(
        workspace_root="/tmp/stale",
        host="0.0.0.0",
        port=8002,
        backend_pid=103,
        frontend_pid=203,
        backend_url="http://127.0.0.1:8002",
        frontend_url="http://127.0.0.1:5175",
        backend_log_path="/tmp/stale-backend.log",
        frontend_log_path="/tmp/stale-frontend.log",
        started_at=123.0,
        status="running",
        frontend_port=5175,
    )

    running_path = tmp_path / "running" / web_dev_server_module.STATE_FILENAME
    degraded_path = tmp_path / "degraded" / web_dev_server_module.STATE_FILENAME
    stale_path = tmp_path / "stale" / web_dev_server_module.STATE_FILENAME
    states = {
        running_path: running_state,
        degraded_path: degraded_state,
        stale_path: stale_state,
    }
    status_map = {
        "/tmp/running": "running",
        "/tmp/degraded": "degraded",
        "/tmp/stale": "stale",
    }
    stop_calls: list[int] = []
    removed_paths: list[str] = []

    monkeypatch.setattr(web_dev_server_module, "iter_web_dev_state_paths", lambda: list(states.keys()))
    monkeypatch.setattr(web_dev_server_module, "_load_state_from_path", lambda path: states[path])

    def fake_inspect(state, *, state_path=None):
        return web_dev_server_module.WebStackInspection(
            state_path=state_path,
            state=state,
            status=status_map[state.workspace_root],
            backend_alive=status_map[state.workspace_root] == "running",
            frontend_alive=status_map[state.workspace_root] == "running",
            backend_ready=status_map[state.workspace_root] == "running",
            frontend_ready=status_map[state.workspace_root] == "running",
        )

    monkeypatch.setattr(web_dev_server_module, "inspect_web_stack_state", fake_inspect)
    monkeypatch.setattr(web_dev_server_module, "_stop_pid", lambda pid: stop_calls.append(pid))
    monkeypatch.setattr(web_dev_server_module, "_remove_state_file", lambda state_path=None: removed_paths.append(str(state_path)))

    results = web_dev_server_module.prune_web_dev_stacks()

    assert [(item.inspection.state.workspace_root, item.action) for item in results] == [
        ("/tmp/running", "kept"),
        ("/tmp/degraded", "removed"),
        ("/tmp/stale", "removed"),
    ]
    assert stop_calls == [202, 102, 203, 103]
    assert removed_paths == [str(degraded_path), str(stale_path)]


def test_format_web_stack_prune_report_should_include_summary_and_health(monkeypatch, tmp_path):
    inspection = web_dev_server_module.WebStackInspection(
        state_path=tmp_path / "state.json",
        state=web_dev_server_module.WebStackState(
            workspace_root="/tmp/degraded",
            host="0.0.0.0",
            port=8001,
            backend_pid=102,
            frontend_pid=202,
            backend_url="http://127.0.0.1:8001",
            frontend_url="http://127.0.0.1:5174",
            backend_log_path="/tmp/degraded-backend.log",
            frontend_log_path="/tmp/degraded-frontend.log",
            started_at=123.0,
            status="running",
            frontend_port=5174,
        ),
        status="degraded",
        backend_alive=True,
        frontend_alive=False,
        backend_ready=True,
        frontend_ready=False,
    )

    output = web_dev_server_module.format_web_stack_prune_report(
        [web_dev_server_module.WebStackPruneResult(inspection=inspection, action="removed")]
    )

    assert "扫描完成：共 1 个实例，已清理 1 个，保留 0 个，失败 0 个。" in output
    assert "已清理 | degraded | 工作区: /tmp/degraded" in output
    assert "backend_pid=up" in output
    assert "frontend_port=closed" in output


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
        frontend_local_url="http://127.0.0.1:5173",
        frontend_network_url="http://192.168.102.18:5173",
        share_frontend=True,
        frontend_port=5180,
    )

    monkeypatch.setattr(web_dev_server_module, "get_web_dev_state_path", lambda: tmp_path / "state.json")

    output = web_dev_server_module.format_web_stack_status("running", state)

    assert "状态: 运行中" in output
    assert "状态文件" in output
    assert "后端日志" in output
    assert "前端局域网访问地址" in output
    assert "仅前端页面对局域网开放" in output


def test_find_available_port_should_skip_occupied_port(monkeypatch):
    calls: list[int] = []

    class _SocketStub:
        def __init__(self, *args, **kwargs):
            self.bound_port: int | None = None

        def setsockopt(self, *args, **kwargs):
            return None

        def bind(self, address):
            port = int(address[1])
            calls.append(port)
            if port == 8000:
                raise OSError("occupied")
            self.bound_port = port

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(web_dev_server_module.socket, "socket", lambda *args, **kwargs: _SocketStub())

    selected = web_dev_server_module.find_available_port("127.0.0.1", 8000)

    assert selected == 8001
    assert calls == [8000, 8001]
