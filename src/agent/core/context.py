from contextvars import ContextVar
from typing import Optional

_UNSET_SESSION_ID = "__unset_session_id__"
_SESSION_ID_CTX: ContextVar[str] = ContextVar("session_id", default=_UNSET_SESSION_ID)


def set_session_id(session_id: Optional[str]) -> str:
    """设置当前会话 ID，要求调用方显式提供非空会话号。"""
    normalized = (session_id or "").strip()
    if not normalized:
        raise ValueError("session_id 不能为空")
    _SESSION_ID_CTX.set(normalized)
    return normalized


def get_session_id() -> str:
    """获取当前会话 ID；未初始化时直接报错，避免静默落默认会话。"""
    session_id = _SESSION_ID_CTX.get()
    if session_id == _UNSET_SESSION_ID:
        raise ValueError("session_id 尚未初始化")
    return session_id
