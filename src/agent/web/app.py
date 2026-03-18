from __future__ import annotations

from collections.abc import Generator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..config.logging_setup import init_logging
from ..config.settings import build_runtime_options
from ..runtime import session as session_runtime
from ..runtime.workspace import configure_workspace, get_workspace
from .schemas import (
    ChatStreamReq,
    ModeSwitchActionReq,
    ModeSwitchActionVO,
    RuntimeOptionsVO,
    SessionClearedVO,
    SessionMessagesVO,
)
from .serializers import message_to_vo, split_stream_event, sse_event


def _stream_chat(req: ChatStreamReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_session_stream_events(
            user_input=req.user_input,
            session_id=req.session_id,
            mode=req.mode,
            provider=req.provider,
            provider_specified="provider" in req.model_fields_set,
        ):
            serialized = split_stream_event(event)
            if serialized is None:
                continue
            event_type, payload = serialized
            yield sse_event(event_type, payload)
    except Exception as exc:  # pragma: no cover - 兜底分支
        yield sse_event(
            "error",
            {
                "code": "internal_error",
                "message": str(exc),
            },
        )


def _stream_mode_switch(session_id: str, req: ModeSwitchActionReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_mode_switch_stream_events(session_id, req.action):
            serialized = split_stream_event(event)
            if serialized is None:
                continue
            event_type, payload = serialized
            yield sse_event(event_type, payload)
    except ValueError as exc:
        yield sse_event(
            "error",
            {
                "code": "mode_switch_conflict",
                "message": str(exc),
            },
        )
    except Exception as exc:  # pragma: no cover - 兜底分支
        yield sse_event(
            "error",
            {
                "code": "internal_error",
                "message": str(exc),
            },
        )


def create_app() -> FastAPI:
    configure_workspace(get_workspace().root, launch_mode="web")
    init_logging(get_workspace().logs_dir)
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

    @app.get("/api/runtime/options", response_model=RuntimeOptionsVO)
    def runtime_options() -> RuntimeOptionsVO:
        return RuntimeOptionsVO(**build_runtime_options())

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
            messages=[message_to_vo(msg) for msg in selected],
        )

    @app.post("/api/sessions/{session_id}/mode-switch", response_model=ModeSwitchActionVO)
    def apply_mode_switch(session_id: str, req: ModeSwitchActionReq) -> ModeSwitchActionVO:
        normalized_id = (session_id or "").strip()
        if not normalized_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        try:
            message = session_runtime.apply_mode_switch_action(normalized_id, req.action)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        current_mode = str(message["info"].get("agent", "build")).strip().lower()
        if current_mode not in {"build", "plan"}:
            current_mode = "build"
        return ModeSwitchActionVO(
            session_id=normalized_id,
            status=str(message["info"].get("status", "")),
            current_mode=current_mode,  # type: ignore[arg-type]
            message=message_to_vo(message),
        )

    @app.post("/api/sessions/{session_id}/mode-switch/stream")
    def apply_mode_switch_stream(session_id: str, req: ModeSwitchActionReq) -> StreamingResponse:
        normalized_id = (session_id or "").strip()
        if not normalized_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        return StreamingResponse(
            _stream_mode_switch(normalized_id, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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
