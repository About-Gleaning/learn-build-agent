from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..config.logging_setup import init_logging
from ..config.settings import build_runtime_options
from ..core.message import Message, get_message_text
from ..runtime import session as session_runtime
from .schemas import (
    ChatStreamReq,
    DisplayPartVO,
    MessageVO,
    ModeSwitchActionReq,
    ModeSwitchActionVO,
    RuntimeOptionsVO,
    SessionClearedVO,
    SessionMessagesVO,
)


def _to_message_vo(message: Message) -> MessageVO:
    info = message.get("info", {})
    response_meta = info.get("response_meta") if isinstance(info.get("response_meta"), dict) else {}
    process_items = info.get("process_items") if isinstance(info.get("process_items"), list) else []
    display_parts = info.get("display_parts") if isinstance(info.get("display_parts"), list) else []
    confirmation = info.get("confirmation") if isinstance(info.get("confirmation"), dict) else None
    return MessageVO(
        message_id=str(info.get("message_id", "")),
        role=str(info.get("role", "")),
        text=get_message_text(message),
        created_at=str(info.get("created_at", "")),
        status=str(info.get("status", "")),
        agent=str(info.get("agent", "")),
        provider=str(info.get("provider", "")),
        model=str(info.get("model", "")),
        finish_reason=str(info.get("finish_reason", "")),
        turn_started_at=str(info.get("turn_started_at", "")),
        turn_completed_at=str(info.get("turn_completed_at", "")),
        response_meta={
            "round_count": int(response_meta.get("round_count", 0) or 0),
            "tool_call_count": int(response_meta.get("tool_call_count", 0) or 0),
            "tool_names": [str(item) for item in response_meta.get("tool_names", []) if str(item).strip()],
            "delegation_count": int(response_meta.get("delegation_count", 0) or 0),
            "delegated_agents": [str(item) for item in response_meta.get("delegated_agents", []) if str(item).strip()],
            "duration_ms": int(response_meta.get("duration_ms", 0) or 0),
        },
        process_items=[
            {
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", "")),
                "title": str(item.get("title", "")),
                "detail": str(item.get("detail", "")),
                "created_at": str(item.get("created_at", "")),
                "agent": str(item.get("agent", "")),
                "agent_kind": str(item.get("agent_kind", "")),
                "depth": int(item.get("depth", 0) or 0),
                "round": int(item.get("round", 0) or 0),
                "status": str(item.get("status", "")),
                "delegation_id": str(item.get("delegation_id", "")),
                "parent_tool_call_id": str(item.get("parent_tool_call_id", "")),
                "tool_name": str(item.get("tool_name", "")),
                "tool_call_id": str(item.get("tool_call_id", "")),
            }
            for item in process_items
            if isinstance(item, dict)
        ],
        display_parts=[
            DisplayPartVO(
                id=str(item.get("id", "")),
                kind=str(item.get("kind", "")),
                title=str(item.get("title", "")),
                detail=str(item.get("detail", "")),
                text=str(item.get("text", "")),
                created_at=str(item.get("created_at", "")),
                agent=str(item.get("agent", "")),
                agent_kind=str(item.get("agent_kind", "")),
                depth=int(item.get("depth", 0) or 0),
                round=int(item.get("round", 0) or 0),
                status=str(item.get("status", "")),
                delegation_id=str(item.get("delegation_id", "")),
                parent_tool_call_id=str(item.get("parent_tool_call_id", "")),
                tool_name=str(item.get("tool_name", "")),
                tool_call_id=str(item.get("tool_call_id", "")),
            )
            for item in display_parts
            if isinstance(item, dict)
        ],
        confirmation=(
            {
                "tool": str(confirmation.get("tool", "")),
                "question": str(confirmation.get("question", "")),
                "target_agent": str(confirmation.get("target_agent", "")),
                "current_agent": str(confirmation.get("current_agent", "")),
                "action_type": str(confirmation.get("action_type", "")),
                "plan_path": str(confirmation.get("plan_path", "")),
            }
            if confirmation is not None
            else None
        ),
    )


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_chat(req: ChatStreamReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_session_stream_events(
            user_input=req.user_input,
            session_id=req.session_id,
            mode=req.mode,
            provider=req.provider,
            provider_specified="provider" in req.model_fields_set,
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


def _stream_mode_switch(session_id: str, req: ModeSwitchActionReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_mode_switch_stream_events(session_id, req.action):
            event_type = str(event.get("type", "")).strip()
            if not event_type:
                continue
            payload: dict[str, Any] = {k: v for k, v in event.items() if k != "type"}
            yield _sse_event(event_type, payload)
    except ValueError as exc:
        yield _sse_event(
            "error",
            {
                "code": "mode_switch_conflict",
                "message": str(exc),
            },
        )
    except Exception as exc:  # pragma: no cover - 兜底分支
        yield _sse_event(
            "error",
            {
                "code": "internal_error",
                "message": str(exc),
            },
        )


def create_app() -> FastAPI:
    init_logging()
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
            messages=[_to_message_vo(msg) for msg in selected],
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
            message=_to_message_vo(message),
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
