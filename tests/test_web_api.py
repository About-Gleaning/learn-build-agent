import json
import re

from fastapi.testclient import TestClient

from agent.core.message import append_text_part, create_message
from agent.runtime import session as session_runtime
from agent.runtime.session import clear_session_memory, configure_session_memory_store
from agent.runtime.session_memory import InMemorySessionMemoryStore
from agent.web.app import create_app


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


def test_chat_stream_should_return_chunk_and_done(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def fake_stream_events(user_input: str, session_id: str | None = None, mode: str | None = None, **kwargs):
        assert user_input == "你好"
        assert kwargs["provider"] == "gpt"
        assert kwargs["provider_specified"] is True
        yield {
            "type": "start",
            "event_id": "evt_1",
            "session_id": session_id or "default",
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
            "session_id": session_id or "default",
            "agent": "build",
            "agent_kind": "primary",
            "depth": 0,
            "message_id": "m_1",
            "status": "completed",
        }

    monkeypatch.setattr("agent.web.app.session_runtime.run_session_stream_events", fake_stream_events)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"session_id": "s_web", "user_input": "你好", "mode": "build", "provider": "gpt"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _stream_events(body)
    assert any(evt == "start" for evt, _ in events)
    assert any(evt == "round_start" for evt, _ in events)
    assert any(evt == "text_delta" for evt, _ in events)
    assert any(evt == "done" for evt, _ in events)
    assert any(payload.get("event_id") == "evt_1" for evt, payload in events if evt == "start")


def test_runtime_options_should_return_backend_config():
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/runtime/options")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["default_agent"] == "build"
    assert any(item["name"] == "build" for item in payload["agents"])
    assert any(item["name"] == "qwen" for item in payload["providers"])


def test_get_session_messages_and_clear():
    configure_session_memory_store(InMemorySessionMemoryStore(max_messages=24))
    clear_session_memory("s_hist")

    user_msg = create_message("user", "s_hist", status="completed")
    append_text_part(user_msg, "第一轮")
    assistant_msg = create_message("assistant", "s_hist", status="completed")
    append_text_part(assistant_msg, "第一轮回答")
    session_runtime.SESSION_MEMORY_STORE.save("s_hist", [user_msg, assistant_msg])

    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/sessions/s_hist/messages?limit=20")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session_id"] == "s_hist"
    assert len(payload["messages"]) >= 2

    clear_resp = client.delete("/api/sessions/s_hist")
    assert clear_resp.status_code == 200

    resp_after_clear = client.get("/api/sessions/s_hist/messages?limit=20")
    assert resp_after_clear.status_code == 200
    assert resp_after_clear.json()["messages"] == []


def test_chat_stream_should_validate_session_id():
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "invalid id", "user_input": "hi", "mode": "build"},
    )
    assert resp.status_code == 422
