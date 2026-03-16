from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from .settings import LOG_LEVEL

_LOGGER_INITIALIZED = False
_DEFAULT_AGENT = "unknown"
_DEFAULT_MODEL = "unknown"
_DEFAULT_LOGGER_NAME = "main"
_QUIET_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "openai")
_SENSITIVE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
]


class RuntimeContextFilter(logging.Filter):
    """为所有日志补齐统一格式要求的上下文字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "agent") or not str(getattr(record, "agent", "")).strip():
            record.agent = _DEFAULT_AGENT
        if not hasattr(record, "model") or not str(getattr(record, "model", "")).strip():
            record.model = _DEFAULT_MODEL
        if not hasattr(record, "logger_name") or not str(getattr(record, "logger_name", "")).strip():
            record.logger_name = record.name or _DEFAULT_LOGGER_NAME
        return True


def get_daily_log_path(base_dir: Path | None = None) -> Path:
    root_dir = base_dir or (Path.cwd() / "logs")
    file_name = f"app-{datetime.now().strftime('%Y-%m-%d')}.log"
    return root_dir / file_name


def sanitize_log_text(text: object, limit: int = 500) -> str:
    raw_text = "" if text is None else str(text)
    cleaned = raw_text.replace("\r", "\\r").replace("\n", "\\n")
    for pattern in _SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "...<truncated>"


def build_log_extra(*, agent: str | None = None, model: str | None = None) -> dict[str, str]:
    return {
        "agent": (agent or "").strip() or _DEFAULT_AGENT,
        "model": (model or "").strip() or _DEFAULT_MODEL,
    }


def init_logging(base_dir: Path | None = None) -> Path:
    """初始化统一日志输出，重复调用时保持幂等。"""
    global _LOGGER_INITIALIZED

    log_path = get_daily_log_path(base_dir=base_dir)
    if _LOGGER_INITIALIZED:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(agent)s %(model)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    context_filter = RuntimeContextFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 压低第三方 SDK 的成功访问日志，只保留异常级别，避免污染业务主链路日志。
    for logger_name in _QUIET_THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _LOGGER_INITIALIZED = True
    return log_path
