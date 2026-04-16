import logging
import os
from datetime import datetime as real_datetime
from datetime import timedelta

from agent.config import logging_setup
from agent.config.settings import LoggingSettings


def test_init_logging_should_write_daily_rotating_log_file_with_append_mode(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    original_httpx_level = logging.getLogger("httpx").level
    original_httpcore_level = logging.getLogger("httpcore").level
    original_openai_level = logging.getLogger("openai").level
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(max_bytes=1024 * 1024, backup_count=30, retention_days=30),
    )

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
        assert any(isinstance(handler, logging_setup.DailySizeRotatingFileHandler) for handler in root_logger.handlers)
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


def test_init_logging_should_allow_disabling_console_handler(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(max_bytes=1024 * 1024, backup_count=30, retention_days=30),
    )

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        logging_setup.init_logging(tmp_path / "logs", console_enabled=False)

        assert len(logging.getLogger().handlers) == 1
        assert isinstance(logging.getLogger().handlers[0], logging_setup.DailySizeRotatingFileHandler)
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag


def test_init_logging_should_allow_disabling_file_handler(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(file_enabled=False, console_enabled=False),
    )

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        logging_setup.init_logging(tmp_path / "logs", console_enabled=False)

        assert len(root_logger.handlers) == 1
        assert isinstance(root_logger.handlers[0], logging.NullHandler)
        assert not (tmp_path / "logs").exists()
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag


def test_daily_size_rotating_handler_should_rotate_when_file_exceeds_limit(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(max_bytes=160, backup_count=30, retention_days=30),
    )

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        log_path = logging_setup.init_logging(tmp_path / "logs", console_enabled=False)
        logger = logging.getLogger("test.logging.rotate")
        logger.info("first %s", "x" * 120, extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        logger.info("second %s", "y" * 120, extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        for handler in root_logger.handlers:
            handler.flush()

        rotated_path = log_path.with_name(f"{log_path.stem}.1{log_path.suffix}")
        assert rotated_path.exists()
        assert "first" in rotated_path.read_text(encoding="utf-8")
        assert "second" in log_path.read_text(encoding="utf-8")
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag


def test_daily_size_rotating_handler_should_switch_file_when_date_changes(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED

    class FakeDatetime:
        current = real_datetime(2026, 4, 16, 23, 59, 0)

        @classmethod
        def now(cls):
            return cls.current

        @classmethod
        def fromtimestamp(cls, value):
            return real_datetime.fromtimestamp(value)

    monkeypatch.setattr(logging_setup, "datetime", FakeDatetime)
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(max_bytes=1024 * 1024, backup_count=30, retention_days=30),
    )

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        logging_setup.init_logging(tmp_path / "logs", console_enabled=False)
        logger = logging.getLogger("test.logging.date")
        logger.info("before midnight", extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        FakeDatetime.current = real_datetime(2026, 4, 17, 0, 1, 0)
        logger.info("after midnight", extra=logging_setup.build_log_extra(agent="build", model="model-a"))
        for handler in root_logger.handlers:
            handler.flush()

        first_path = tmp_path / "logs" / "app-2026-04-16.log"
        second_path = tmp_path / "logs" / "app-2026-04-17.log"
        assert "before midnight" in first_path.read_text(encoding="utf-8")
        assert "after midnight" in second_path.read_text(encoding="utf-8")
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag


def test_daily_size_rotating_handler_should_cleanup_expired_and_excess_logs(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    old_path = logs_dir / "app-2026-01-01.log"
    old_path.write_text("old", encoding="utf-8")
    old_time = real_datetime.now() - timedelta(days=10)
    os.utime(old_path, (old_time.timestamp(), old_time.timestamp()))

    history_paths = []
    for index in range(3):
        path = logs_dir / f"app-2026-04-1{index}.log"
        path.write_text(str(index), encoding="utf-8")
        stamp = real_datetime.now() - timedelta(minutes=index)
        os.utime(path, (stamp.timestamp(), stamp.timestamp()))
        history_paths.append(path)

    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_flag = logging_setup._LOGGER_INITIALIZED
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(max_bytes=1024 * 1024, backup_count=2, retention_days=2),
    )

    try:
        root_logger.handlers.clear()
        logging_setup._LOGGER_INITIALIZED = False

        active_path = logging_setup.init_logging(logs_dir, console_enabled=False)

        assert not old_path.exists()
        remaining_history = sorted(path for path in history_paths if path.exists())
        assert len(remaining_history) == 2
        assert active_path.exists()
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        logging_setup._LOGGER_INITIALIZED = original_flag


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


def test_sanitize_log_text_should_redact_common_secret_fields(monkeypatch):
    monkeypatch.setattr(
        logging_setup,
        "resolve_logging_settings",
        lambda: LoggingSettings(truncate_enabled=False, redact_enabled=True),
    )

    sanitized = logging_setup.sanitize_log_text(
        '{"api_key":"abcd1234","password":"p@ssw0rd","authorization":"Bearer abcdefghijklmnop"}'
    )

    assert "abcd1234" not in sanitized
    assert "p@ssw0rd" not in sanitized
    assert "abcdefghijklmnop" not in sanitized
    assert "[REDACTED]" in sanitized
