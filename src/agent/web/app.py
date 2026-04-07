from __future__ import annotations

from collections.abc import Generator
import re

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..config.logging_setup import init_logging
from ..config.settings import build_runtime_options
from ..runtime import session as session_runtime
from ..runtime.workspace import configure_workspace, get_workspace
from .path_suggestions import record_path_selection, suggest_workspace_paths
from .schemas import (
    ChatStreamReq,
    ModeSwitchActionReq,
    ModeSwitchActionVO,
    PathSelectionReq,
    PathSelectionVO,
    PathSuggestionsVO,
    QuestionActionVO,
    QuestionAnswerReq,
    RuntimeOptionsVO,
    SessionClearedVO,
    SessionMessagesVO,
    StopSessionVO,
)
from .serializers import message_to_vo, split_stream_event, sse_event

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _normalize_session_id_or_raise(session_id: str) -> str:
    normalized_id = (session_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    if not SESSION_ID_PATTERN.fullmatch(normalized_id):
        raise HTTPException(status_code=400, detail="session_id 格式非法，仅支持字母、数字、下划线和中划线")
    return normalized_id


def _stream_chat(req: ChatStreamReq) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_session_stream_events(
            user_input=req.user_input,
            session_id=req.session_id,
            mode=req.mode,
            provider=req.provider,
            model=req.model,
            provider_specified="provider" in req.model_fields_set,
            model_specified="model" in req.model_fields_set,
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


def _stream_question_answer(session_id: str, request_id: str, req: QuestionAnswerReq) -> Generator[str, None, None]:
    try:
        answer_payload = [item.model_dump() for item in req.answers]
        for event in session_runtime.run_question_answer_stream_events(session_id, request_id, answer_payload):
            serialized = split_stream_event(event)
            if serialized is None:
                continue
            event_type, payload = serialized
            yield sse_event(event_type, payload)
    except ValueError as exc:
        yield sse_event(
            "error",
            {
                "code": "question_conflict",
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


def _stream_question_reject(session_id: str, request_id: str) -> Generator[str, None, None]:
    try:
        for event in session_runtime.run_question_reject_stream_events(session_id, request_id):
            serialized = split_stream_event(event)
            if serialized is None:
                continue
            event_type, payload = serialized
            yield sse_event(event_type, payload)
    except ValueError as exc:
        yield sse_event(
            "error",
            {
                "code": "question_conflict",
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

    @app.get("/api/workspace/path-suggestions", response_model=PathSuggestionsVO)
    def workspace_path_suggestions(q: str = Query(default="", max_length=256)) -> PathSuggestionsVO:
        normalized_query = (q or "").strip()
        try:
            suggestions = suggest_workspace_paths(normalized_query)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PathSuggestionsVO(
            query=normalized_query,
            suggestions=[
                {
                    "path": item.path,
                    "name": item.name,
                    "relative_path": item.relative_path,
                    "kind": item.kind,
                }
                for item in suggestions
            ],
        )

    @app.post("/api/workspace/path-selections", response_model=PathSelectionVO)
    def workspace_path_selection(req: PathSelectionReq) -> PathSelectionVO:
        normalized_relative_path = (req.relative_path or "").strip()
        if not normalized_relative_path:
            raise HTTPException(status_code=400, detail="relative_path 不能为空")
        try:
            record_path_selection(normalized_relative_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PathSelectionVO(recorded=True, relative_path=normalized_relative_path)

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

    @app.post("/api/sessions/{session_id}/stop", response_model=StopSessionVO)
    def stop_session(session_id: str) -> StopSessionVO:
        normalized_id = _normalize_session_id_or_raise(session_id)
        session_runtime.request_session_stop(normalized_id)
        return StopSessionVO(session_id=normalized_id)

    @app.get("/api/sessions/{session_id}/messages", response_model=SessionMessagesVO)
    def get_session_messages(session_id: str, limit: int = Query(default=50, ge=1, le=200)) -> SessionMessagesVO:
        normalized_id = _normalize_session_id_or_raise(session_id)

        messages = session_runtime.SESSION_MEMORY_STORE.load(normalized_id)
        selected = messages[-limit:]
        return SessionMessagesVO(
            session_id=normalized_id,
            messages=[message_to_vo(msg) for msg in selected],
        )

    @app.post("/api/sessions/{session_id}/mode-switch", response_model=ModeSwitchActionVO)
    def apply_mode_switch(session_id: str, req: ModeSwitchActionReq) -> ModeSwitchActionVO:
        normalized_id = _normalize_session_id_or_raise(session_id)
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
        normalized_id = _normalize_session_id_or_raise(session_id)
        return StreamingResponse(
            _stream_mode_switch(normalized_id, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/sessions/{session_id}/questions/{request_id}/answer", response_model=QuestionActionVO)
    def apply_question_answer(session_id: str, request_id: str, req: QuestionAnswerReq) -> QuestionActionVO:
        normalized_id = _normalize_session_id_or_raise(session_id)
        normalized_request_id = (request_id or "").strip()
        if not normalized_request_id:
            raise HTTPException(status_code=400, detail="request_id 不能为空")
        try:
            answer_payload = [item.model_dump() for item in req.answers]
            message = session_runtime.apply_question_answer(normalized_id, normalized_request_id, answer_payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        current_mode = str(message["info"].get("agent", "build")).strip().lower()
        if current_mode not in {"build", "plan"}:
            current_mode = "build"
        return QuestionActionVO(
            session_id=normalized_id,
            status=str(message["info"].get("status", "")),
            current_mode=current_mode,  # type: ignore[arg-type]
            message=message_to_vo(message),
        )

    @app.post("/api/sessions/{session_id}/questions/{request_id}/answer/stream")
    def apply_question_answer_stream(session_id: str, request_id: str, req: QuestionAnswerReq) -> StreamingResponse:
        normalized_id = _normalize_session_id_or_raise(session_id)
        normalized_request_id = (request_id or "").strip()
        if not normalized_request_id:
            raise HTTPException(status_code=400, detail="request_id 不能为空")
        return StreamingResponse(
            _stream_question_answer(normalized_id, normalized_request_id, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/sessions/{session_id}/questions/{request_id}/reject", response_model=QuestionActionVO)
    def apply_question_reject(session_id: str, request_id: str) -> QuestionActionVO:
        normalized_id = _normalize_session_id_or_raise(session_id)
        normalized_request_id = (request_id or "").strip()
        if not normalized_request_id:
            raise HTTPException(status_code=400, detail="request_id 不能为空")
        try:
            message = session_runtime.apply_question_reject(normalized_id, normalized_request_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        current_mode = str(message["info"].get("agent", "build")).strip().lower()
        if current_mode not in {"build", "plan"}:
            current_mode = "build"
        return QuestionActionVO(
            session_id=normalized_id,
            status=str(message["info"].get("status", "")),
            current_mode=current_mode,  # type: ignore[arg-type]
            message=message_to_vo(message),
        )

    @app.post("/api/sessions/{session_id}/questions/{request_id}/reject/stream")
    def apply_question_reject_stream(session_id: str, request_id: str) -> StreamingResponse:
        normalized_id = _normalize_session_id_or_raise(session_id)
        normalized_request_id = (request_id or "").strip()
        if not normalized_request_id:
            raise HTTPException(status_code=400, detail="request_id 不能为空")
        return StreamingResponse(
            _stream_question_reject(normalized_id, normalized_request_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.delete("/api/sessions/{session_id}", response_model=SessionClearedVO)
    def clear_session(session_id: str) -> SessionClearedVO:
        normalized_id = _normalize_session_id_or_raise(session_id)
        session_runtime.clear_session_memory(normalized_id)
        return SessionClearedVO(session_id=normalized_id)

    return app


app = create_app()
