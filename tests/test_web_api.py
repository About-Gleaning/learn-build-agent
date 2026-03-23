import json
import re

from fastapi.testclient import TestClient

from agent.core.message import append_reasoning_part, append_text_part, create_message
from agent.runtime import session as session_runtime
from agent.runtime.session import clear_session_memory, configure_session_memory_store, generate_session_id
from agent.runtime.session_memory import InMemorySessionMemoryStore
from agent.web.app import create_app
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
    assert any(item["api_mode"] == "responses" for item in payload["providers"] if item["name"] == "gpt")
    assert any(item["api_mode"] == "responses" for item in payload["agents"] if item["name"] == "build")
    assert payload["workspace_root"]
    assert payload["workspace_name"]
    assert payload["launch_mode"] == "web"


def test_message_to_vo_should_normalize_missing_optional_fields():
    assistant = create_message("assistant", "s_msg", status="completed")
    append_text_part(assistant, "hello")

    message_vo = message_to_vo(assistant)

    assert message_vo.text == "hello"
    assert message_vo.response_meta.duration_ms == 0
    assert message_vo.process_items == []
    assert message_vo.display_parts == []
    assert message_vo.confirmation is None


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


def test_chat_stream_should_validate_session_id():
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "invalid id", "user_input": "hi", "mode": "build"},
    )
    assert resp.status_code == 422


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


def test_apply_mode_switch_should_return_conflict_when_no_pending(monkeypatch):
    app = create_app()
    client = TestClient(app)

    def raise_no_pending(session_id, action):
        raise ValueError("当前没有待确认的模式切换。")

    monkeypatch.setattr("agent.web.app.session_runtime.apply_mode_switch_action", raise_no_pending)

    resp = client.post("/api/sessions/s_mode/mode-switch", json={"action": "confirm"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "当前没有待确认的模式切换。"


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
