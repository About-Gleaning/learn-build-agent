import logging

from agent.config import logging_setup


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
