from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..core.message import Message, get_message_text
from ..runtime import session as session_runtime
from .schemas import ChatStreamReq, MessageVO, SessionClearedVO, SessionMessagesVO


def _to_message_vo(message: Message) -> MessageVO:
    info = message.get("info", {})
    return MessageVO(
        message_id=str(info.get("message_id", "")),
        role=str(info.get("role", "")),
        text=get_message_text(message),
        created_at=str(info.get("created_at", "")),
        status=str(info.get("status", "")),
        agent=str(info.get("agent", "")),
    )


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_chat(req: ChatStreamReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_session_stream_events(
            user_input=req.user_input,
            session_id=req.session_id,
            mode=req.mode,
        ):
            event_type = str(event.get("type", "")).strip()
            if not event_type:
                continue
            payload: dict[str, Any] = {k: v for k, v in event.items() if k != "type"}
            yield _sse_event(event_type, payload)
    except Exception as exc:  # pragma: no cover - 兜底分支
        yield _sse_event(
            "error",
            {
                "code": "internal_error",
                "message": str(exc),
            },
        )


def create_app() -> FastAPI:
    app = FastAPI(title="my-main-agent web api", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5175",
            "http://localhost:5173",
            "http://localhost:5175",
        ],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1):\d+$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/")
    def index() -> dict[str, str]:
        return {
            "name": "my-main-agent web api",
            "status": "ok",
            "healthz": "/healthz",
            "chat_stream": "/api/chat/stream",
            "docs": "/docs",
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/chat/stream")
    def chat_stream(req: ChatStreamReq) -> StreamingResponse:
        return StreamingResponse(
            _stream_chat(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/sessions/{session_id}/messages", response_model=SessionMessagesVO)
    def get_session_messages(session_id: str, limit: int = Query(default=50, ge=1, le=200)) -> SessionMessagesVO:
        normalized_id = (session_id or "").strip()
        if not normalized_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")

        messages = session_runtime.SESSION_MEMORY_STORE.load(normalized_id)
        selected = messages[-limit:]
        return SessionMessagesVO(
            session_id=normalized_id,
            messages=[_to_message_vo(msg) for msg in selected],
        )

    @app.delete("/api/sessions/{session_id}", response_model=SessionClearedVO)
    def clear_session(session_id: str) -> SessionClearedVO:
        normalized_id = (session_id or "").strip()
        if not normalized_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        session_runtime.clear_session_memory(normalized_id)
        return SessionClearedVO(session_id=normalized_id)

    return app


app = create_app()
