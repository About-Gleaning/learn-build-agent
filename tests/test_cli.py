import pytest

from agent import cli as cli_module


def test_main_should_print_help_and_skip_workspace_configuration(monkeypatch, capsys):
    called = {"configured": False}

    monkeypatch.setattr(
        cli_module,
        "configure_workspace",
        lambda *args, **kwargs: called.update({"configured": True}),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert called == {"configured": False}
    assert "my-agent 命令总览" in output
    assert "-h, --help" in output
    assert "my-agent web prune" in output
    assert "--workdir WORKDIR" in output
    assert "--session SESSION" in output
    assert "--mode {build,plan}" in output
    assert "--host HOST" in output
    assert "--port PORT" in output
    assert "--share-frontend" in output
    assert "--verbose" in output
    assert "顶层参数用于 my-agent ...；Web 参数用于 my-agent web ..." in output


def test_main_should_print_web_help(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["web", "--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "usage: my-agent web" in output
    assert "--host HOST" in output
    assert "--port PORT" in output
    assert "--share-frontend" in output
    assert "--verbose" in output


def test_main_should_reject_removed_help_subcommand(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["help"])

    error_output = capsys.readouterr().err
    assert exc_info.value.code == 2
    assert "invalid choice" in error_output
    assert "'help'" in error_output


def test_help_text_should_cover_parser_arguments():
    output = cli_module._format_help_text()

    assert "-h, --help" in output
    assert "my-agent --help" in output
    assert "--workdir WORKDIR" in output
    assert "--session SESSION" in output
    assert "--mode {build,plan}" in output
    assert "my-agent web status" in output
    assert "my-agent web stop" in output
    assert "my-agent web prune" in output
    assert "--host HOST" in output
    assert "--port PORT" in output
    assert "--share-frontend" in output
    assert "--verbose" in output
    assert "  web" in output


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
        lambda *, host, port, share_frontend=False, verbose=False: captured.update(
            {"host": host, "port": port, "share_frontend": share_frontend, "verbose": verbose}
        ),
    )

    cli_module.main(["--workdir", str(tmp_path), "web"])

    assert captured == {"host": "0.0.0.0", "port": 8000, "share_frontend": False, "verbose": False}


def test_main_should_pass_explicit_web_start_arguments(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "run_web_start",
        lambda *, host, port, share_frontend=False, verbose=False: captured.update(
            {"host": host, "port": port, "share_frontend": share_frontend, "verbose": verbose}
        ),
    )

    cli_module.main(
        ["--workdir", str(tmp_path), "web", "start", "--host", "0.0.0.0", "--port", "9000", "--share-frontend", "--verbose"]
    )

    assert captured == {"host": "0.0.0.0", "port": 9000, "share_frontend": True, "verbose": True}


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


def test_main_should_route_web_prune(monkeypatch, tmp_path):
    captured: dict[str, bool] = {}

    monkeypatch.setattr(cli_module, "configure_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "run_web_prune", lambda: captured.update({"prune": True}))

    cli_module.main(["--workdir", str(tmp_path), "web", "prune"])

    assert captured == {"prune": True}
