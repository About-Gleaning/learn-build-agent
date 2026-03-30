from agent import cli as cli_module


def test_main_should_generate_random_session_id_when_session_not_provided(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "generate_session_id", lambda prefix="session": "cli_random_123")
    monkeypatch.setattr(
        cli_module,
        "run_cli_session",
        lambda *, session_id, mode: captured.update({"session_id": session_id, "mode": mode}),
    )

    cli_module.main(["--workdir", str(tmp_path)])

    assert captured == {"session_id": "cli_random_123", "mode": "build"}


def test_main_should_keep_explicit_session_id(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "run_cli_session",
        lambda *, session_id, mode: captured.update({"session_id": session_id, "mode": mode}),
    )

    cli_module.main(["--workdir", str(tmp_path), "--session", "cli_fixed"])

    assert captured == {"session_id": "cli_fixed", "mode": "build"}


def test_main_should_default_web_command_to_start(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "run_web_start",
        lambda *, host, port, verbose=False: captured.update({"host": host, "port": port, "verbose": verbose}),
    )

    cli_module.main(["--workdir", str(tmp_path), "web"])

    assert captured == {"host": "0.0.0.0", "port": 8000, "verbose": False}


def test_main_should_pass_explicit_web_start_arguments(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "run_web_start",
        lambda *, host, port, verbose=False: captured.update({"host": host, "port": port, "verbose": verbose}),
    )

    cli_module.main(["--workdir", str(tmp_path), "web", "start", "--host", "0.0.0.0", "--port", "9000", "--verbose"])

    assert captured == {"host": "0.0.0.0", "port": 9000, "verbose": True}


def test_main_should_route_web_status(monkeypatch, tmp_path):
    captured: dict[str, bool] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "run_web_status", lambda: captured.update({"status": True}))

    cli_module.main(["--workdir", str(tmp_path), "web", "status"])

    assert captured == {"status": True}


def test_main_should_route_web_stop(monkeypatch, tmp_path):
    captured: dict[str, bool] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "run_web_stop", lambda: captured.update({"stop": True}))

    cli_module.main(["--workdir", str(tmp_path), "web", "stop"])

    assert captured == {"stop": True}
