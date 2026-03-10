from contextvars import ContextVar
from typing import Optional

DEFAULT_SESSION_ID = "default_session"
_SESSION_ID_CTX: ContextVar[str] = ContextVar("session_id", default=DEFAULT_SESSION_ID)


def set_session_id(session_id: Optional[str]) -> str:
    """设置当前会话 ID，空值会回退到默认会话。"""
    normalized = (session_id or "").strip() or DEFAULT_SESSION_ID
    _SESSION_ID_CTX.set(normalized)
    return normalized


def get_session_id() -> str:
    """获取当前会话 ID。"""
    return _SESSION_ID_CTX.get()
