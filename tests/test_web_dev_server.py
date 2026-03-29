import pytest

from agent.runtime import web_dev_server as web_dev_server_module


class _FakeProcess:
    def __init__(self, return_code: int | None = None):
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


def test_run_web_dev_stack_should_be_silent_by_default(monkeypatch, tmp_path, capsys):
    backend_process = _FakeProcess()
    frontend_process = _FakeProcess()
    stop_calls: list[str] = []

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, verbose: backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, verbose: frontend_process,
    )
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", lambda process, endpoint, timeout_seconds=15.0: None)
    monkeypatch.setattr(
        web_dev_server_module,
        "wait_for_web_stack_forever",
        lambda backend_process, frontend_process: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "stop_process",
        lambda process, *, name: stop_calls.append(name),
    )

    web_dev_server_module.run_web_dev_stack(workspace_root=tmp_path, host="127.0.0.1", port=8000)

    output = capsys.readouterr().out
    assert output == ""
    assert stop_calls == ["前端服务", "后端服务"]


def test_run_web_dev_stack_should_print_progress_when_verbose(monkeypatch, tmp_path, capsys):
    backend_process = _FakeProcess()
    frontend_process = _FakeProcess()
    stop_calls: list[str] = []

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, verbose: backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, verbose: frontend_process,
    )
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", lambda process, endpoint, timeout_seconds=15.0: None)
    monkeypatch.setattr(
        web_dev_server_module,
        "wait_for_web_stack_forever",
        lambda backend_process, frontend_process: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "stop_process",
        lambda process, *, name: stop_calls.append(name),
    )

    web_dev_server_module.run_web_dev_stack(workspace_root=tmp_path, host="127.0.0.1", port=8000, verbose=True)

    output = capsys.readouterr().out
    assert "后端服务已就绪" in output
    assert "前端服务已就绪" in output
    assert "正在停止前后端服务" in output
    assert stop_calls == ["前端服务", "后端服务"]


def test_run_web_dev_stack_should_cleanup_started_processes_when_frontend_bootstrap_fails(monkeypatch, tmp_path):
    backend_process = _FakeProcess()
    frontend_process = _FakeProcess()
    stop_calls: list[str] = []
    wait_calls: list[str] = []

    def fake_wait_for_process_port(process, endpoint, timeout_seconds=15.0):
        wait_calls.append(endpoint.name)
        if endpoint.name == "前端服务":
            raise web_dev_server_module.WebStackError("前端端口未就绪")

    monkeypatch.setattr(web_dev_server_module, "resolve_frontend_dir", lambda: tmp_path / "frontend")
    monkeypatch.setattr(web_dev_server_module, "ensure_frontend_dev_prerequisites", lambda frontend_dir: "pnpm")
    monkeypatch.setattr(
        web_dev_server_module,
        "start_backend_dev_server",
        lambda *, workspace_root, host, port, verbose: backend_process,
    )
    monkeypatch.setattr(
        web_dev_server_module,
        "start_frontend_dev_server",
        lambda *, frontend_dir, pnpm_binary, verbose: frontend_process,
    )
    monkeypatch.setattr(web_dev_server_module, "wait_for_process_port", fake_wait_for_process_port)
    monkeypatch.setattr(
        web_dev_server_module,
        "stop_process",
        lambda process, *, name: stop_calls.append(name),
    )

    with pytest.raises(web_dev_server_module.WebStackError) as exc_info:
        web_dev_server_module.run_web_dev_stack(workspace_root=tmp_path, host="127.0.0.1", port=8000)

    assert "前端端口未就绪" in str(exc_info.value)
    assert wait_calls == ["后端服务", "前端服务"]
    assert stop_calls == ["前端服务", "后端服务"]


def test_build_subprocess_stdio_should_silence_children_by_default():
    stdio = web_dev_server_module._build_subprocess_stdio(verbose=False)

    assert stdio["stdout"] is web_dev_server_module.subprocess.DEVNULL
    assert stdio["stderr"] is web_dev_server_module.subprocess.DEVNULL


def test_build_subprocess_stdio_should_inherit_children_output_when_verbose():
    stdio = web_dev_server_module._build_subprocess_stdio(verbose=True)

    assert stdio == {}
