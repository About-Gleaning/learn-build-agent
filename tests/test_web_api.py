import json
import re
import threading
import time

from fastapi.testclient import TestClient

from agent.core.message import append_text_part, append_tool_call_part, append_tool_result_part, append_reasoning_part, create_message
from agent.runtime import session as session_runtime
from agent.runtime.run_manager import RunManager
from agent.runtime.session import clear_session_memory, configure_session_memory_store, generate_session_id
from agent.runtime.session_memory import InMemorySessionMemoryStore
from agent.web.app import RUN_MANAGER, _stream_active_run, _stream_chat, _stream_session, create_app
from agent.web.path_suggestions import PathSuggestion
from agent.web.schemas import ChatStreamReq, SessionStreamReq
from agent.web.serializers import message_to_vo, split_stream_event


def test_index_should_return_api_overview():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["chat_stream"] == "/api/chat/stream"


def _stream_events(body_text: str) -> list[tuple[str, dict]]:
    pattern = re.compile(r"event:\s*(?P<event>[a-zA-Z_]+)\s*data:\s*(?P<data>\{.*?\})(?:\n|$)", re.DOTALL)
    parsed: list[tuple[str, dict]] = []
    for match in pattern.finditer(body_text.replace("\r", "")):
        event_type = match.group("event").strip()
        data_payload = json.loads(match.group("data").strip())
        parsed.append((event_type, data_payload))
    return parsed


def test_run_manager_should_wait_for_depth_zero_done():
    manager = RunManager(heartbeat_interval_seconds=0.05)
    depth_one_seen = threading.Event()
    release_top_done = threading.Event()

    def event_source():
        yield {"type": "start", "event_id": "evt_depth_start", "session_id": "s_depth", "depth": 0}
        yield {"type": "done", "event_id": "evt_depth_inner_done", "session_id": "s_depth", "depth": 1, "status": "completed"}
        depth_one_seen.set()
        release_top_done.wait(timeout=1)
        yield {"type": "done", "event_id": "evt_depth_top_done", "session_id": "s_depth", "depth": 0, "status": "completed"}

    run = manager.create_run(session_id="s_depth", event_source=event_source)
    assert depth_one_seen.wait(timeout=1)
    assert manager.get_active_run("s_depth") is not None

    release_top_done.set()
    run.thread.join(timeout=1)

    assert manager.get_active_run("s_depth") is None
    assert manager.get_run(run.run_id).status == "completed"


def test_chat_stream_should_return_chunk_and_done(monkeypatch):
    app = create_app()
    client = TestClient(app)
    session_id = generate_session_id("test_web")

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        assert user_input == "你好"
        assert session_id
        assert kwargs["provider"] == "gpt"
        assert kwargs["model"] == "gpt-4.1"
        assert kwargs["provider_specified"] is True
        assert kwargs["model_specified"] is True
        yield {
            "type": "start",
            "event_id": "evt_1",
            "session_id": session_id,
            "agent": mode or "build",
            "agent_kind": "primary",
            "depth": 0,
            "mode": mode or "build",
            "provider": "gpt",
            "model": "gpt-4.1",
            "started_at": "t1",
        }
        yield {
            "type": "round_start",
            "event_id": "evt_2",
            "round": 1,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "provider": "gpt",
            "model": "gpt-4.1",
            "started_at": "t2",
        }
        yield {"type": "text_delta", "event_id": "evt_3", "round": 1, "agent": "build", "agent_kind": "primary", "depth": 0, "delta": "回答"}
        yield {"type": "text_delta", "event_id": "evt_4", "round": 1, "agent": "build", "agent_kind": "primary", "depth": 0, "delta": ": 你好"}
        yield {
            "type": "round_end",
            "event_id": "evt_5",
            "round": 1,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "status": "completed",
            "finish_reason": "stop",
            "completed_at": "t3",
        }
        yield {
            "type": "done",
            "event_id": "evt_6",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_1",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t3",
            "response_meta": {
                "round_count": 1,
                "tool_call_count": 0,
                "tool_names": [],
                "delegation_count": 0,
                "delegated_agents": [],
                "duration_ms": 1200,
            },
            "process_items": [
                {
                    "id": "evt_1",
                    "kind": "start",
                    "title": "build 会话开始",
                    "detail": "主代理 · build",
                    "created_at": "t1",
                    "agent": "build",
                    "agent_kind": "primary",
                    "depth": 0,
                    "round": 0,
                    "status": "",
                    "delegation_id": "",
                    "parent_tool_call_id": "",
                    "tool_name": "",
                    "tool_call_id": "",
                }
            ],
            "display_parts": [
                {
                    "id": "disp_1",
                    "kind": "assistant_text",
                    "title": "build 回复",
                    "detail": "",
                    "text": "回答: 你好",
                    "created_at": "t2",
                    "agent": "build",
                    "agent_kind": "primary",
                    "depth": 0,
                    "round": 1,
                    "status": "completed",
                    "delegation_id": "",
                    "parent_tool_call_id": "",
                    "tool_name": "",
                    "tool_call_id": "",
                }
            ],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"session_id": session_id, "user_input": "你好", "mode": "build", "provider": "gpt", "model": "gpt-4.1"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    assert any(evt == "start" for evt, _ in events)
    assert any(evt == "round_start" for evt, _ in events)
    assert any(evt == "text_delta" for evt, _ in events)
    assert any(evt == "done" for evt, _ in events)
    assert any(payload.get("event_id") == "evt_1" for evt, payload in events if evt == "start")
    done_payload = next(payload for evt, payload in events if evt == "done")
    assert done_payload["response_meta"]["duration_ms"] == 1200
    assert done_payload["process_items"][0]["kind"] == "start"
    assert done_payload["display_parts"][0]["kind"] == "assistant_text"


def test_session_stream_should_treat_empty_provider_model_as_unspecified(monkeypatch):
    captured = {}

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        captured.update(
            {
                "user_input": user_input,
                "session_id": session_id,
                "mode": mode,
                "provider": kwargs["provider"],
                "model": kwargs["model"],
                "provider_specified": kwargs["provider_specified"],
                "model_specified": kwargs["model_specified"],
            }
        )
        yield {
            "type": "done",
            "event_id": "evt_empty_runtime_done",
            "session_id": session_id,
            "agent": mode or "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_empty_runtime",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)
    RUN_MANAGER.clear()

    body = "".join(
        _stream_session(
            "s_empty_runtime",
            SessionStreamReq(
                client_run_id="run_empty_runtime",
                user_input="继续",
                mode="build",
                provider="",
                model="",
            ),
        )
    )

    assert "event: done" in body
    assert captured["provider"] == ""
    assert captured["model"] == ""
    assert captured["provider_specified"] is False
    assert captured["model_specified"] is False


def test_chat_stream_should_passthrough_runtime_alert_event(monkeypatch):
    app = create_app()
    client = TestClient(app)
    session_id = generate_session_id("test_web_alert")

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del user_input, mode, kwargs
        yield {
            "type": "runtime_alert",
            "event_id": "evt_alert_1",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "scope": "mcp",
            "severity": "error",
            "code": "mcp_server_unavailable",
            "message": "MCP server `github` 当前不可用：未设置 GITHUB_TOKEN",
            "server_alias": "github",
        }
        yield {
            "type": "done",
            "event_id": "evt_done_1",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_1",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {
                "round_count": 1,
                "tool_call_count": 0,
                "tool_names": [],
                "delegation_count": 0,
                "delegated_agents": [],
                "duration_ms": 10,
            },
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"session_id": session_id, "user_input": "你好", "mode": "build"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    runtime_alert_payload = next(payload for evt, payload in events if evt == "runtime_alert")

    assert runtime_alert_payload["scope"] == "mcp"
    assert runtime_alert_payload["server_alias"] == "github"
    assert "GITHUB_TOKEN" in runtime_alert_payload["message"]


def test_chat_stream_disconnect_should_not_stop_background_run(monkeypatch):
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_disconnect")
    allow_continue = threading.Event()
    completed = threading.Event()

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del user_input, mode, kwargs
        yield {
            "type": "start",
            "event_id": "evt_disconnect_start",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
        }
        yield {
            "type": "tool_call",
            "event_id": "evt_disconnect_tool_call",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "tool_call_id": "call_write",
            "name": "write_file",
            "arguments": '{"filePath":"/tmp/demo.md","content":"hello"}',
        }
        allow_continue.wait(timeout=2)
        yield {
            "type": "tool_result",
            "event_id": "evt_disconnect_tool_result",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "tool_call_id": "call_write",
            "name": "write_file",
            "status": "completed",
            "output_preview": "创建成功",
        }
        yield {
            "type": "done",
            "event_id": "evt_disconnect_done",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }
        completed.set()

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    stream = _stream_chat(ChatStreamReq(session_id=session_id, user_input="创建文件", mode="build"))
    seen_tool_call = False
    try:
        for chunk in stream:
            if "event: tool_call" in chunk:
                seen_tool_call = True
                break
    finally:
        stream.close()

    assert seen_tool_call is True
    allow_continue.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not completed.is_set():
        time.sleep(0.01)

    assert completed.is_set() is True
    run_records = [run for run in RUN_MANAGER._runs.values() if run.session_id == session_id]
    assert run_records
    assert run_records[-1].status == "completed"
    assert any(event.get("type") == "tool_result" for event in run_records[-1].events)


def test_active_run_stream_should_resume_after_disconnect(monkeypatch):
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_resume")
    allow_continue = threading.Event()

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del user_input, mode, kwargs
        yield {
            "type": "start",
            "event_id": "evt_resume_start",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
        }
        yield {
            "type": "tool_call",
            "event_id": "evt_resume_tool_call",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "tool_call_id": "call_resume",
            "name": "write_file",
            "arguments": "{}",
        }
        allow_continue.wait(timeout=2)
        yield {
            "type": "tool_result",
            "event_id": "evt_resume_tool_result",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "tool_call_id": "call_resume",
            "name": "write_file",
            "status": "completed",
            "output_preview": "创建成功",
        }
        yield {
            "type": "done",
            "event_id": "evt_resume_done",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_resume_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    stream = _stream_chat(ChatStreamReq(session_id=session_id, user_input="创建文件", mode="build"))
    try:
        for chunk in stream:
            if "event: tool_call" in chunk:
                break
    finally:
        stream.close()

    assert RUN_MANAGER.get_active_run(session_id) is not None
    allow_continue.set()

    body = "".join(_stream_active_run(session_id))
    events = _stream_events(body)
    event_names = [event_name for event_name, _payload in events]

    assert event_names[:2] == ["start", "tool_call"]
    assert "tool_result" in event_names
    assert event_names[-1] == "done"
    assert RUN_MANAGER.get_active_run(session_id) is None


def test_session_stream_should_reuse_client_run_without_duplicate_execution(monkeypatch):
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_client_run")
    allow_continue = threading.Event()
    calls = {"count": 0}

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del mode, kwargs
        calls["count"] += 1
        assert user_input == "创建文件"
        yield {
            "type": "start",
            "event_id": "evt_client_start",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
        }
        allow_continue.wait(timeout=2)
        yield {
            "type": "done",
            "event_id": "evt_client_done",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_client_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    first_stream = _stream_session(
        session_id,
        SessionStreamReq(client_run_id="run_test_client_once", user_input="创建文件", mode="build"),
    )
    try:
        first_chunk = next(first_stream)
        assert "event: start" in first_chunk

        second_stream = _stream_session(
            session_id,
            SessionStreamReq(client_run_id="run_test_client_once", mode="build"),
        )
        try:
            replay_chunk = next(second_stream)
            assert "event: start" in replay_chunk
        finally:
            second_stream.close()

        assert calls["count"] == 1
    finally:
        allow_continue.set()
        first_stream.close()


def test_session_stream_should_require_user_input_when_client_run_missing():
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_missing_input")

    body = "".join(_stream_session(session_id, SessionStreamReq(client_run_id="run_missing_input", mode="build")))
    events = _stream_events(body)

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "missing_user_input"
    assert events[-1][1]["client_run_id"] == "run_missing_input"


def test_session_stream_should_replay_completed_client_run_without_duplicate_execution(monkeypatch):
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_client_done")
    calls = {"count": 0}

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del mode, kwargs
        calls["count"] += 1
        assert user_input == "创建文件"
        yield {
            "type": "start",
            "event_id": "evt_client_done_start",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
        }
        yield {
            "type": "done",
            "event_id": "evt_client_done_done",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_client_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    first_body = "".join(
        _stream_session(
            session_id,
            SessionStreamReq(client_run_id="run_test_client_done", user_input="创建文件", mode="build"),
        )
    )
    second_body = "".join(
        _stream_session(
            session_id,
            SessionStreamReq(client_run_id="run_test_client_done", mode="build"),
        )
    )

    first_events = _stream_events(first_body)
    second_events = _stream_events(second_body)

    assert calls["count"] == 1
    assert [event_name for event_name, _payload in first_events] == ["start", "done"]
    assert [event_name for event_name, _payload in second_events] == ["start", "done"]


def test_run_manager_heartbeat_should_not_pollute_cached_events():
    manager = type(RUN_MANAGER)(heartbeat_interval_seconds=0.01)
    allow_continue = threading.Event()

    def events():
        yield {"type": "start", "event_id": "evt_hb_start", "session_id": "s_hb"}
        allow_continue.wait(timeout=0.1)
        yield {"type": "done", "event_id": "evt_hb_done", "session_id": "s_hb", "status": "completed"}

    run = manager.create_or_get_run(session_id="s_hb", client_run_id="run_hb", event_source=events)
    subscription = manager.subscribe(run.run_id)
    try:
        first_event = next(subscription)
        heartbeat = next(subscription)
    finally:
        allow_continue.set()
        subscription.close()

    assert first_event["type"] == "start"
    assert heartbeat["type"] == "heartbeat"
    assert all(event["type"] != "heartbeat" for event in manager.get_run(run.run_id).events)


def test_active_run_resume_should_keep_session_runtime_in_history_api(monkeypatch):
    RUN_MANAGER.clear()
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    session_id = generate_session_id("test_web_resume_runtime")
    clear_session_memory(session_id)
    allow_continue = threading.Event()

    user_msg = create_message("user", session_id, status="completed")
    append_text_part(user_msg, "读取长文档", meta={"agent": "plan"})
    assistant_msg = create_message("assistant", session_id, status="completed")
    assistant_msg["info"]["agent"] = "plan"
    assistant_msg["info"]["provider"] = "qwen"
    assistant_msg["info"]["model"] = "kimi-k2.5"
    append_text_part(assistant_msg, "开始处理")
    session_runtime.SESSION_MEMORY_STORE.save(session_id, [user_msg, assistant_msg])
    session_runtime.SESSION_MEMORY_STORE.save_runtime(
        session_id,
        {
            "mode": "plan",
            "provider": "qwen",
            "model": "kimi-k2.5",
            "provider_explicit": True,
            "model_explicit": True,
        },
    )

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del user_input, mode, kwargs
        yield {
            "type": "start",
            "event_id": "evt_resume_runtime_start",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "provider": "qwen",
            "model": "kimi-k2.5",
        }
        yield {
            "type": "tool_call",
            "event_id": "evt_resume_runtime_tool_call",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "tool_call_id": "call_resume_runtime",
            "name": "read_file",
            "arguments": "{}",
            "provider": "qwen",
            "model": "kimi-k2.5",
        }
        allow_continue.wait(timeout=2)
        yield {
            "type": "done",
            "event_id": "evt_resume_runtime_done",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_resume_runtime_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "provider": "qwen",
            "model": "kimi-k2.5",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    stream = _stream_chat(ChatStreamReq(session_id=session_id, user_input="继续", mode="plan"))
    try:
        for chunk in stream:
            if "event: tool_call" in chunk:
                break
    finally:
        stream.close()

    assert RUN_MANAGER.get_active_run(session_id) is not None
    allow_continue.set()
    body = "".join(_stream_active_run(session_id))
    events = _stream_events(body)
    assert events[-1][0] == "done"

    app = create_app()
    client = TestClient(app)
    resp = client.get(f"/api/sessions/{session_id}/messages?limit=20")

    assert resp.status_code == 200
    assert resp.json()["runtime"] == {
        "mode": "plan",
        "provider": "qwen",
        "model": "kimi-k2.5",
        "provider_explicit": True,
        "model_explicit": True,
    }


def test_active_run_stream_should_return_no_active_run_when_idle():
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_no_active")

    body = "".join(_stream_active_run(session_id))
    events = _stream_events(body)

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "no_active_run"
    assert events[-1][1]["session_id"] == session_id


def test_chat_stream_should_return_conflict_when_session_has_active_run(monkeypatch):
    RUN_MANAGER.clear()
    session_id = generate_session_id("test_web_active_conflict")
    allow_continue = threading.Event()

    def fake_stream_events(user_input: str, session_id: str, mode: str | None = None, **kwargs):
        del user_input, mode, kwargs
        yield {
            "type": "start",
            "event_id": "evt_conflict_start",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
        }
        allow_continue.wait(timeout=2)
        yield {
            "type": "done",
            "event_id": "evt_conflict_done",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "msg_conflict_done",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {},
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    first_stream = _stream_chat(ChatStreamReq(session_id=session_id, user_input="第一个任务", mode="build"))
    try:
        first_chunk = next(first_stream)
        assert "event: start" in first_chunk

        conflict_body = "".join(_stream_chat(ChatStreamReq(session_id=session_id, user_input="第二个任务", mode="build")))
        events = _stream_events(conflict_body)

        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "session_run_conflict"
        assert events[-1][1]["active_run_id"]
        run_records = [run for run in RUN_MANAGER._runs.values() if run.session_id == session_id]
        assert len(run_records) == 1
    finally:
        allow_continue.set()
        first_stream.close()


def test_runtime_options_should_return_backend_config():
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/runtime/options")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["default_agent"] == "build"
    assert any(item["name"] == "build" for item in payload["agents"])
    assert any(item["name"] == "qwen" for item in payload["providers"])
    assert any(item["name"] == "kimi" for item in payload["providers"])
    assert any(item["vendor"] == "kimi" for item in payload["providers"])
    assert any(item["vendor"] == "qwen" for item in payload["providers"])
    assert any("qwen3-max" in item["models"] for item in payload["providers"] if item["name"] == "qwen")
    assert any("kimi/kimi-k2.5" in item["models"] for item in payload["providers"] if item["name"] == "qwen")
    assert any(item["models"] for item in payload["providers"] if item["name"] == "qwen")
    assert any(item["api_mode"] == "responses" for item in payload["providers"] if item["name"] == "gpt")
    assert any(item["api_mode"] in {"responses", "chat_completions"} for item in payload["agents"] if item["name"] == "build")
    assert any(item["name"] == "init" for item in payload["slash_commands"])
    assert any(item["usage"] == "/init" for item in payload["slash_commands"])
    assert any(item["name"] == "analyze" for item in payload["slash_commands"])
    assert any(item["usage"] == "/analyze" for item in payload["slash_commands"])
    assert payload["workspace_root"]
    assert payload["workspace_name"]
    assert payload["launch_mode"] == "web"


def test_workspace_path_suggestions_should_return_matches(monkeypatch):
    app = create_app()
    client = TestClient(app)

    monkeypatch.setattr(
        "agent.web.app.suggest_workspace_paths",
        lambda query, limit=50: [
            PathSuggestion(
                path="/tmp/project/src/test_app.py",
                name="test_app.py",
                relative_path="src/test_app.py",
                kind="file",
            ),
            PathSuggestion(
                path="/tmp/project/tests",
                name="tests",
                relative_path="tests",
                kind="directory",
            ),
        ],
    )

    resp = client.get("/api/workspace/path-suggestions?q=test")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["query"] == "test"
    assert payload["suggestions"][0]["relative_path"] == "src/test_app.py"
    assert payload["suggestions"][1]["kind"] == "directory"


def test_workspace_path_suggestions_should_forward_limit(monkeypatch):
    app = create_app()
    client = TestClient(app)
    captured: dict[str, int | str] = {}

    def fake_suggest_workspace_paths(query: str, limit: int = 50):
        captured.update(query=query, limit=limit)
        return []

    monkeypatch.setattr("agent.web.app.suggest_workspace_paths", fake_suggest_workspace_paths)

    resp = client.get("/api/workspace/path-suggestions?q=test&limit=2")

    assert resp.status_code == 200
    assert captured == {"query": "test", "limit": 2}


def test_workspace_path_suggestions_should_return_empty_list_for_empty_query():
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/workspace/path-suggestions?q= ")

    assert resp.status_code == 200
    assert resp.json() == {"query": "", "suggestions": []}


def test_workspace_path_selection_should_record_relative_path(monkeypatch):
    app = create_app()
    client = TestClient(app)
    recorded: list[str] = []

    monkeypatch.setattr("agent.web.app.record_path_selection", lambda relative_path: recorded.append(relative_path))

    resp = client.post("/api/workspace/path-selections", json={"relative_path": "src/test_app.py"})

    assert resp.status_code == 200
    assert resp.json()["recorded"] is True
    assert recorded == ["src/test_app.py"]


def test_workspace_path_selection_should_reject_invalid_relative_path(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def fake_record_path_selection(relative_path: str):
        raise ValueError("relative_path 超出工作区范围")

    monkeypatch.setattr("agent.web.app.record_path_selection", fake_record_path_selection)

    resp = client.post("/api/workspace/path-selections", json={"relative_path": "../secret.txt"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "relative_path 超出工作区范围"


def test_message_to_vo_should_normalize_missing_optional_fields():
    assistant = create_message("assistant", "s_msg", status="completed")
    append_text_part(assistant, "hello")

    message_vo = message_to_vo(assistant)

    assert message_vo.text == "hello"
    assert message_vo.response_meta.duration_ms == 0
    assert message_vo.process_items == []
    assert message_vo.display_parts[0].kind == "assistant_text"
    assert message_vo.display_parts[0].text == "hello"
    assert message_vo.confirmation is None
    assert message_vo.question is None


def test_message_to_vo_should_rebuild_response_meta_from_compact_meta():
    assistant = create_message("assistant", "s_compact_meta", status="completed")
    append_text_part(assistant, "完成")
    assistant["info"]["turn_started_at"] = "2026-03-14T00:00:00+00:00"
    assistant["info"]["turn_completed_at"] = "2026-03-14T00:00:02+00:00"
    assistant["info"]["round_count"] = 1
    assistant["info"]["tool_call_count"] = 1
    assistant["info"]["tool_names"] = ["write_file"]

    message_vo = message_to_vo(assistant)

    assert message_vo.response_meta.round_count == 1
    assert message_vo.response_meta.tool_call_count == 1
    assert message_vo.response_meta.tool_names == ["write_file"]
    assert message_vo.response_meta.duration_ms == 2000


def test_get_session_messages_should_rebuild_tool_display_parts_from_blocks():
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_hist_compact_projection")

    user_msg = create_message("user", "s_hist_compact_projection", status="completed")
    append_text_part(user_msg, "创建文件")
    assistant_msg = create_message("assistant", "s_hist_compact_projection", status="completed")
    append_text_part(assistant_msg, "我来创建文件。")
    append_tool_call_part(
        assistant_msg,
        tool_call_id="call_write_1",
        name="write_file",
        arguments='{"filePath":"hello.py","content":"print(1)"}',
    )
    assistant_msg["info"]["agent"] = "build"
    assistant_msg["info"]["round_count"] = 1
    assistant_msg["info"]["tool_call_count"] = 1
    assistant_msg["info"]["tool_names"] = ["write_file"]
    tool_msg = create_message("tool", "s_hist_compact_projection", status="completed")
    append_tool_result_part(tool_msg, tool_call_id="call_write_1", name="write_file", content="创建成功: hello.py")
    session_runtime.SESSION_MEMORY_STORE.save("s_hist_compact_projection", [user_msg, assistant_msg, tool_msg])

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/sessions/s_hist_compact_projection/messages?limit=20")

    assert resp.status_code == 200
    assistant_payload = next(item for item in resp.json()["messages"] if item["role"] == "assistant")
    display_kinds = [item["kind"] for item in assistant_payload["display_parts"]]
    assert display_kinds == ["assistant_text", "tool_call", "tool_result"]
    assert assistant_payload["display_parts"][1]["detail"] == '{"filePath":"hello.py","content":"print(1)"}'
    assert "创建成功: hello.py" in assistant_payload["display_parts"][2]["detail"]


def test_message_to_vo_should_keep_reasoning_display_part_kind():
    assistant = create_message("assistant", "s_reasoning_vo", status="completed")
    append_reasoning_part(assistant, "先确认上下文。")
    assistant["info"]["display_parts"] = [
        {
            "id": "disp_reasoning_1",
            "kind": "reasoning",
            "title": "build 思考",
            "detail": "",
            "text": "先确认上下文。",
            "created_at": "2026-03-23T00:00:00+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        }
    ]

    message_vo = message_to_vo(assistant)

    assert message_vo.display_parts[0].kind == "reasoning"
    assert message_vo.display_parts[0].text == "先确认上下文。"


def test_split_stream_event_should_remove_type_field():
    event_type, payload = split_stream_event({"type": "done", "message_id": "m1", "status": "completed"}) or ("", {})

    assert event_type == "done"
    assert payload == {"message_id": "m1", "status": "completed"}


def test_message_to_vo_should_keep_question_payload():
    assistant = create_message("assistant", "s_question_vo", status="interrupted")
    append_text_part(assistant, "等待用户回答问题后再继续。")
    assistant["info"]["question"] = {
        "tool": "question",
        "request_id": "question_123",
        "title": "等待用户回答 1 个问题",
        "questions": [
            {
                "question": "选择方案？",
                "header": "方案",
                "options": [
                    {"label": "A", "description": "快"},
                    {"label": "B", "description": "稳"},
                ],
                "multiple": False,
                "custom": True,
            }
        ],
    }

    message_vo = message_to_vo(assistant)

    assert message_vo.question is not None
    assert message_vo.question.request_id == "question_123"
    assert message_vo.question.questions[0].options[0].label == "A"
    assert message_vo.question.questions[0].custom is True


def test_get_session_messages_and_clear():
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_hist")

    user_msg = create_message("user", "s_hist", status="completed")
    append_text_part(user_msg, "第一轮")
    assistant_msg = create_message("assistant", "s_hist", status="completed")
    append_text_part(assistant_msg, "第一轮回答")
    assistant_msg["info"]["finish_reason"] = "stop"
    assistant_msg["info"]["turn_started_at"] = "2026-03-14T00:00:00+00:00"
    assistant_msg["info"]["turn_completed_at"] = "2026-03-14T00:00:02+00:00"
    assistant_msg["info"]["response_meta"] = {
        "round_count": 2,
        "tool_call_count": 1,
        "tool_names": ["todo_read"],
        "delegation_count": 0,
        "delegated_agents": [],
        "duration_ms": 2000,
    }
    assistant_msg["info"]["process_items"] = [
        {
            "id": "evt_1",
            "kind": "tool_call",
            "title": "build 调用工具: todo_read",
            "detail": "{}",
            "created_at": "2026-03-14T00:00:01+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "todo_read",
            "tool_call_id": "call_1",
        }
    ]
    assistant_msg["info"]["display_parts"] = [
        {
            "id": "disp_1",
            "kind": "assistant_text",
            "title": "build 回复",
            "detail": "",
            "text": "第一轮回答",
            "created_at": "2026-03-14T00:00:00+00:00",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        }
    ]
    assistant_msg["info"]["confirmation"] = {
        "tool": "plan_enter",
        "question": "是否切换到 plan 模式？",
        "target_agent": "plan",
        "current_agent": "build",
        "action_type": "enter_plan",
        "plan_path": "/tmp/p.md",
    }
    session_runtime.SESSION_MEMORY_STORE.save("s_hist", [user_msg, assistant_msg])

    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/sessions/s_hist/messages?limit=20")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session_id"] == "s_hist"
    assert len(payload["messages"]) >= 2
    assistant_payload = next(item for item in payload["messages"] if item["role"] == "assistant")
    assert assistant_payload["finish_reason"] == "stop"
    assert assistant_payload["response_meta"]["tool_call_count"] == 1
    assert assistant_payload["process_items"][0]["tool_name"] == "todo_read"
    assert assistant_payload["display_parts"][0]["text"] == "第一轮回答"
    assert assistant_payload["confirmation"]["target_agent"] == "plan"

    clear_resp = client.delete("/api/sessions/s_hist")
    assert clear_resp.status_code == 200

    resp_after_clear = client.get("/api/sessions/s_hist/messages?limit=20")
    assert resp_after_clear.status_code == 200
    assert resp_after_clear.json()["messages"] == []


def test_get_session_messages_should_return_session_runtime():
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_hist_runtime")
    app = create_app()
    client = TestClient(app)

    user_msg = create_message("user", "s_hist_runtime", status="completed")
    append_text_part(user_msg, "你好", meta={"agent": "plan", "provider": "qwen", "model": "kimi-k2.5"})
    assistant_msg = create_message("assistant", "s_hist_runtime", status="completed")
    assistant_msg["info"]["agent"] = "plan"
    assistant_msg["info"]["provider"] = "qwen"
    assistant_msg["info"]["model"] = "kimi-k2.5"
    append_text_part(assistant_msg, "ok")
    session_runtime.SESSION_MEMORY_STORE.save("s_hist_runtime", [user_msg, assistant_msg])
    session_runtime.SESSION_MEMORY_STORE.save_runtime(
        "s_hist_runtime",
        {
            "mode": "plan",
            "provider": "qwen",
            "model": "kimi-k2.5",
            "provider_explicit": True,
            "model_explicit": True,
        },
    )

    resp = client.get("/api/sessions/s_hist_runtime/messages?limit=20")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runtime"] == {
        "mode": "plan",
        "provider": "qwen",
        "model": "kimi-k2.5",
        "provider_explicit": True,
        "model_explicit": True,
    }


def test_chat_stream_should_validate_session_id():
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "invalid id", "user_input": "hi", "mode": "build"},
    )
    assert resp.status_code == 422


def test_session_routes_should_validate_session_id():
    app = create_app()
    client = TestClient(app)

    invalid_session_id = "invalid id"

    for method, path, kwargs in [
        ("GET", f"/api/sessions/{invalid_session_id}/messages?limit=20", {}),
        ("POST", f"/api/sessions/{invalid_session_id}/stop", {}),
        ("DELETE", f"/api/sessions/{invalid_session_id}", {}),
        ("POST", f"/api/sessions/{invalid_session_id}/mode-switch", {"json": {"action": "confirm"}}),
        ("POST", f"/api/sessions/{invalid_session_id}/mode-switch/stream", {"json": {"action": "confirm"}}),
        ("POST", f"/api/sessions/{invalid_session_id}/questions/question_1/answer", {"json": {"answers": []}}),
        ("POST", f"/api/sessions/{invalid_session_id}/questions/question_1/answer/stream", {"json": {"answers": []}}),
        ("POST", f"/api/sessions/{invalid_session_id}/questions/question_1/reject", {}),
        ("POST", f"/api/sessions/{invalid_session_id}/questions/question_1/reject/stream", {}),
    ]:
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 400
        assert "session_id 格式非法" in resp.json()["detail"]


def test_apply_mode_switch_should_return_message(monkeypatch):
    app = create_app()
    client = TestClient(app)

    assistant = create_message("assistant", "s_mode", status="completed")
    append_text_part(assistant, "已切换到 plan 模式")
    assistant["info"]["agent"] = "plan"
    assistant["info"]["finish_reason"] = "stop"

    monkeypatch.setattr("agent.web.app.session_runtime.apply_mode_switch_action", lambda session_id, action: assistant)

    resp = client.post("/api/sessions/s_mode/mode-switch", json={"action": "confirm"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session_id"] == "s_mode"
    assert payload["current_mode"] == "plan"
    assert payload["message"]["text"] == "已切换到 plan 模式"


def test_apply_mode_switch_stream_should_return_sse_events(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def fake_mode_switch_stream(session_id: str, action: str):
        assert session_id == "s_mode"
        assert action == "confirm"
        yield {
            "type": "start",
            "event_id": "evt_mode_1",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "mode": "plan",
            "provider": "qwen",
            "model": "qwen3-max",
            "started_at": "t1",
        }
        yield {
            "type": "text_delta",
            "event_id": "evt_mode_2",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "delta": "开始制定计划",
        }
        yield {
            "type": "done",
            "event_id": "evt_mode_3",
            "session_id": session_id,
            "agent": "plan",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_mode_1",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {
                "round_count": 1,
                "tool_call_count": 0,
                "tool_names": [],
                "delegation_count": 0,
                "delegated_agents": [],
                "duration_ms": 500,
            },
            "process_items": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_mode_switch_stream_events", fake_mode_switch_stream)

    with client.stream(
        "POST",
        "/api/sessions/s_mode/mode-switch/stream",
        json={"action": "confirm"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    assert [evt for evt, _ in events] == ["start", "text_delta", "done"]
    done_payload = next(payload for evt, payload in events if evt == "done")
    assert done_payload["agent"] == "plan"
    assert done_payload["response_meta"]["duration_ms"] == 500


def test_apply_mode_switch_stream_should_return_conflict_error_event(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def raise_no_pending(session_id: str, action: str):
        del session_id, action
        raise ValueError("当前没有待确认的模式切换。")

    monkeypatch.setattr("agent.web.app.session_runtime.run_mode_switch_stream_events", raise_no_pending)

    with client.stream(
        "POST",
        "/api/sessions/s_mode_stream_conflict/mode-switch/stream",
        json={"action": "confirm"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    error_payload = next(payload for evt, payload in events if evt == "error")
    assert error_payload["code"] == "mode_switch_conflict"
    assert error_payload["message"] == "当前没有待确认的模式切换。"


def test_apply_mode_switch_should_return_conflict_when_no_pending(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def raise_no_pending(session_id, action):
        raise ValueError("当前没有待确认的模式切换。")

    monkeypatch.setattr("agent.web.app.session_runtime.apply_mode_switch_action", raise_no_pending)

    resp = client.post("/api/sessions/s_mode/mode-switch", json={"action": "confirm"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "当前没有待确认的模式切换。"


def test_apply_question_answer_should_return_message(monkeypatch):
    app = create_app()
    client = TestClient(app)

    assistant = create_message("assistant", "s_question", status="completed")
    append_text_part(assistant, "已根据回答继续处理")
    assistant["info"]["agent"] = "build"
    assistant["info"]["finish_reason"] = "stop"

    monkeypatch.setattr(
        "agent.web.app.session_runtime.apply_question_answer",
        lambda session_id, request_id, answers: assistant,
    )

    resp = client.post(
        "/api/sessions/s_question/questions/question_1/answer",
        json={"answers": [{"answers": ["方案A"], "notes": "优先兼容旧逻辑"}]},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session_id"] == "s_question"
    assert payload["message"]["text"] == "已根据回答继续处理"


def test_apply_question_reject_should_return_message(monkeypatch):
    app = create_app()
    client = TestClient(app)

    assistant = create_message("assistant", "s_question_reject", status="completed")
    append_text_part(assistant, "已按拒绝分支继续")
    assistant["info"]["agent"] = "build"
    assistant["info"]["finish_reason"] = "stop"

    monkeypatch.setattr(
        "agent.web.app.session_runtime.apply_question_reject",
        lambda session_id, request_id: assistant,
    )

    resp = client.post("/api/sessions/s_question_reject/questions/question_1/reject")

    assert resp.status_code == 200
    assert resp.json()["message"]["text"] == "已按拒绝分支继续"


def test_apply_question_answer_stream_should_return_sse_events(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def fake_question_stream(session_id: str, request_id: str, answers: list[dict[str, object]]):
        assert session_id == "s_question"
        assert request_id == "question_1"
        assert answers == [{"answers": ["方案A"], "notes": "补充说明"}]
        yield {"type": "text_delta", "event_id": "evt_q_1", "delta": "继续处理"}
        yield {
            "type": "done",
            "event_id": "evt_q_2",
            "session_id": session_id,
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_q_1",
            "status": "completed",
            "finish_reason": "stop",
            "turn_started_at": "t1",
            "turn_completed_at": "t2",
            "response_meta": {
                "round_count": 1,
                "tool_call_count": 0,
                "tool_names": [],
                "delegation_count": 0,
                "delegated_agents": [],
                "duration_ms": 300,
            },
            "process_items": [],
            "display_parts": [],
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_question_answer_stream_events", fake_question_stream)

    with client.stream(
        "POST",
        "/api/sessions/s_question/questions/question_1/answer/stream",
        json={"answers": [{"answers": ["方案A"], "notes": "补充说明"}]},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    assert [evt for evt, _ in events] == ["text_delta", "done"]


def test_apply_question_reject_should_return_conflict_when_no_pending(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def raise_no_pending(session_id, request_id):
        raise ValueError("当前没有待回答的问题。")

    monkeypatch.setattr("agent.web.app.session_runtime.apply_question_reject", raise_no_pending)

    resp = client.post("/api/sessions/s_question/questions/question_1/reject")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "当前没有待回答的问题。"


def test_apply_question_answer_should_return_conflict_when_answers_invalid(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def raise_invalid(session_id, request_id, answers):
        raise ValueError("第 1 个问题至少需要一个答案；若用户拒绝回答，请调用 reject 接口。")

    monkeypatch.setattr("agent.web.app.session_runtime.apply_question_answer", raise_invalid)

    resp = client.post(
        "/api/sessions/s_question/questions/question_1/answer",
        json={"answers": [{"answers": [], "notes": ""}]},
    )

    assert resp.status_code == 409
    assert "至少需要一个答案" in resp.json()["detail"]


def test_stop_session_should_return_requested(monkeypatch):
    app = create_app()
    client = TestClient(app)
    captured: list[str] = []

    monkeypatch.setattr("agent.web.app.session_runtime.request_session_stop", lambda session_id: captured.append(session_id))

    resp = client.post("/api/sessions/s_stop/stop")

    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": "s_stop",
        "stopped": True,
        "status": "requested",
    }
    assert captured == ["s_stop"]


def test_clear_session_should_also_clear_stop_state():
    session_runtime.request_session_stop("s_clear_stop")

    app = create_app()
    client = TestClient(app)

    resp = client.delete("/api/sessions/s_clear_stop")

    assert resp.status_code == 200
    assert session_runtime.is_session_stop_requested("s_clear_stop") is False


def test_session_messages_should_keep_display_parts_on_original_assistant(monkeypatch):
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_web_history_projection")
    user = create_message("user", "s_web_history_projection", status="completed")
    append_text_part(user, "测试加载历史归属")
    assistant_1 = create_message("assistant", "s_web_history_projection", status="completed")
    append_text_part(assistant_1, "先执行工具")
    assistant_1["info"]["display_parts"] = [
        {
            "id": "disp_call_1",
            "kind": "tool_call",
            "title": "build 调用工具: glob",
            "detail": '{"pattern":"*.py"}',
            "text": "",
            "created_at": "t1",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "glob",
            "tool_call_id": "call_web_1",
        },
        {
            "id": "disp_result_1",
            "kind": "tool_result",
            "title": "build 工具结果: glob",
            "detail": "completed []",
            "text": "",
            "created_at": "t2",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "glob",
            "tool_call_id": "call_web_1",
        },
        {
            "id": "disp_text_1",
            "kind": "assistant_text",
            "title": "build 回复",
            "detail": "",
            "text": "先执行工具",
            "created_at": "t3",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 1,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        },
    ]
    assistant_2 = create_message("assistant", "s_web_history_projection", status="completed")
    append_text_part(assistant_2, "最终总结")
    assistant_2["info"]["display_parts"] = [
        {
            "id": "disp_text_2",
            "kind": "assistant_text",
            "title": "build 回复",
            "detail": "",
            "text": "最终总结",
            "created_at": "t4",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "round": 2,
            "status": "completed",
            "delegation_id": "",
            "parent_tool_call_id": "",
            "tool_name": "",
            "tool_call_id": "",
        }
    ]
    session_runtime.SESSION_MEMORY_STORE.save("s_web_history_projection", [user, assistant_1, assistant_2])

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/sessions/s_web_history_projection/messages?limit=20")

    assert resp.status_code == 200
    payload = resp.json()
    assistants = [message for message in payload["messages"] if message["role"] == "assistant"]
    assert len(assistants) == 2
    first_display_kinds = [item["kind"] for item in assistants[0]["display_parts"]]
    second_display_kinds = [item["kind"] for item in assistants[1]["display_parts"]]
    assert "tool_call" in first_display_kinds
    assert "tool_result" in first_display_kinds
    assert second_display_kinds == ["assistant_text"]
