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
