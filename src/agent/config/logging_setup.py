from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..runtime.workspace import get_workspace
from .settings import LOG_LEVEL, resolve_logging_settings

_LOGGER_INITIALIZED = False
_DEFAULT_AGENT = "unknown"
_DEFAULT_MODEL = "unknown"
_DEFAULT_LOGGER_NAME = "main"
_QUIET_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "openai")
_SENSITIVE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(
        r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|password|secret|cookie|set-cookie)\b"
        r"[\"']?\s*[:=]\s*[\"']?)"
        r"([^\"'\s,;}{]{4,})([\"']?)"
    ),
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


class DailySizeRotatingFileHandler(logging.Handler):
    """按自然日切换文件，并用大小轮转兜底限制单文件体积。"""

    terminator = "\n"

    def __init__(
        self,
        base_dir: Path,
        *,
        max_bytes: int,
        backup_count: int,
        retention_days: int,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.retention_days = retention_days
        self.encoding = encoding
        self._active_date = ""
        self._stream = None
        self._path: Path | None = None
        self._open_for_date(self._current_date())
        self._cleanup_old_logs()

    @staticmethod
    def _current_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _build_path(self, date_text: str) -> Path:
        return self.base_dir / f"app-{date_text}.log"

    def _open_for_date(self, date_text: str) -> None:
        self._close_stream()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._active_date = date_text
        self._path = self._build_path(date_text)
        self._stream = self._path.open("a", encoding=self.encoding)

    def _next_rotated_path(self, date_text: str) -> Path:
        index = 1
        while True:
            candidate = self.base_dir / f"app-{date_text}.{index}.log"
            if not candidate.exists():
                return candidate
            index += 1

    def _rotate_size(self) -> None:
        if self._path is None:
            return
        active_path = self._path
        active_date = self._active_date
        self._close_stream()
        if active_path.exists() and active_path.stat().st_size > 0:
            os.replace(active_path, self._next_rotated_path(active_date))
        self._open_for_date(active_date)
        self._cleanup_old_logs()

    def _should_rotate_size(self, message_size: int) -> bool:
        if self._path is None or self.max_bytes <= 0 or not self._path.exists():
            return False
        current_size = self._path.stat().st_size
        return current_size > 0 and current_size + message_size > self.max_bytes

    def _cleanup_old_logs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        active_path = self._path.resolve() if self._path is not None else None
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        candidates = [path for path in self.base_dir.glob("app-*.log") if path.is_file()]

        for path in candidates:
            if active_path is not None and path.resolve() == active_path:
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                continue

        remaining = [
            path
            for path in self.base_dir.glob("app-*.log")
            if path.is_file() and (active_path is None or path.resolve() != active_path)
        ]
        remaining.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for path in remaining[self.backup_count :]:
            try:
                path.unlink()
            except OSError:
                continue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            date_text = self._current_date()
            if date_text != self._active_date:
                self._open_for_date(date_text)
                self._cleanup_old_logs()

            message = self.format(record) + self.terminator
            message_size = len(message.encode(self.encoding, errors="replace"))
            if self._should_rotate_size(message_size):
                self._rotate_size()
            if self._stream is not None:
                self._stream.write(message)
                self.flush()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if self._stream is not None:
            self._stream.flush()

    def close(self) -> None:
        self._close_stream()
        super().close()

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()


def get_daily_log_path(base_dir: Path | None = None) -> Path:
    root_dir = base_dir or get_workspace().logs_dir
    file_name = f"app-{datetime.now().strftime('%Y-%m-%d')}.log"
    return root_dir / file_name


def sanitize_log_text(text: object, limit: int | None = None) -> str:
    raw_text = "" if text is None else str(text)
    cleaned = raw_text.replace("\r", "\\r").replace("\n", "\\n")
    logging_settings = resolve_logging_settings()
    if logging_settings.redact_enabled:
        for pattern in _SENSITIVE_PATTERNS:
            cleaned = pattern.sub(_redact_sensitive_match, cleaned)
    if not logging_settings.truncate_enabled:
        return cleaned

    effective_limit = logging_settings.truncate_limit if limit is None else min(logging_settings.truncate_limit, limit)
    if len(cleaned) <= effective_limit:
        return cleaned
    return cleaned[:effective_limit] + "...<truncated>"


def _redact_sensitive_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 3:
        return f"{match.group(1)}[REDACTED]{match.group(3)}"
    return "[REDACTED]"


def build_log_extra(*, agent: str | None = None, model: str | None = None) -> dict[str, str]:
    return {
        "agent": (agent or "").strip() or _DEFAULT_AGENT,
        "model": (model or "").strip() or _DEFAULT_MODEL,
    }


def init_logging(base_dir: Path | None = None, *, console_enabled: bool = True) -> Path:
    """初始化统一日志输出，重复调用时保持幂等。"""
    global _LOGGER_INITIALIZED

    logging_settings = resolve_logging_settings()
    log_path = get_daily_log_path(base_dir=base_dir)
    if _LOGGER_INITIALIZED:
        return log_path

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(agent)s %(model)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    context_filter = RuntimeContextFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root_logger.handlers.clear()

    if logging_settings.file_enabled:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if logging_settings.rotation_enabled:
            file_handler: logging.Handler = DailySizeRotatingFileHandler(
                log_path.parent,
                max_bytes=logging_settings.max_bytes,
                backup_count=logging_settings.backup_count,
                retention_days=logging_settings.retention_days,
                encoding="utf-8",
            )
        else:
            file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root_logger.addHandler(file_handler)

    effective_console_enabled = (
        console_enabled if logging_settings.console_enabled is None else logging_settings.console_enabled
    )
    if effective_console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.addFilter(context_filter)
        root_logger.addHandler(console_handler)
    if not root_logger.handlers:
        root_logger.addHandler(logging.NullHandler())

    # 压低第三方 SDK 的成功访问日志，只保留异常级别，避免污染业务主链路日志。
    for logger_name in _QUIET_THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _LOGGER_INITIALIZED = True
    return log_path
