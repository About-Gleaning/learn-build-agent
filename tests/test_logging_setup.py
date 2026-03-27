import logging

from agent.config import logging_setup
from agent.config.settings import LoggingSettings


def test_init_logging_should_write_daily_log_file_with_append_mode(tmp_path):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    original_httpx_level = logging.getLogger("httpx").level
    original_httpcore_level = logging.getLogger("httpcore").level
    original_openai_level = logging.getLogger("openai").level

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        log_path = logging_setup.init_logging(tmp_path / "logs")
        assert log_path == tmp_path / "logs" / f"app-{logging_setup.datetime.now().strftime('%Y-%m-%d')}.log"

        logger = logging.getLogger("test.logging")
        logger.info("first line", extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        for handler in logging.getLogger().handlers:
            handler.flush()

        logging_setup._LOGGER_INITIALIZED = False
        root_logger.handlers.clear()
        logging_setup.init_logging(tmp_path / "logs")
        logger.info("second line", extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_path.read_text(encoding="utf-8")
        assert "first line" in content
        assert "second line" in content
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("openai").level == logging.WARNING
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag
        logging.getLogger("httpx").setLevel(original_httpx_level)
        logging.getLogger("httpcore").setLevel(original_httpcore_level)
        logging.getLogger("openai").setLevel(original_openai_level)


def test_sanitize_log_text_should_not_truncate_by_default(monkeypatch):
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(truncate_enabled=False, truncate_limit=500),
    )

    sanitized = logging_setup.sanitize_log_text("line1\n" + ("x" * 800))

    assert "\\n" in sanitized
    assert "...<truncated>" not in sanitized
    assert len(sanitized) > 500


def test_sanitize_log_text_should_truncate_when_enabled(monkeypatch):
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(truncate_enabled=True, truncate_limit=50),
    )

    sanitized = logging_setup.sanitize_log_text("x" * 80)

    assert sanitized == ("x" * 50) + "...<truncated>"


def test_sanitize_log_text_should_use_stricter_callsite_limit_when_enabled(monkeypatch):
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(truncate_enabled=True, truncate_limit=50),
    )

    sanitized = logging_setup.sanitize_log_text("x" * 80, limit=20)

    assert sanitized == ("x" * 20) + "...<truncated>"
