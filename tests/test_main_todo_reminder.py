import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.main as main_module


def build_response(content, tool_names=None):
    """构造与 OpenAI SDK 结构兼容的最小响应对象。"""
    tool_calls = None
    if tool_names:
        tool_calls = []
        for i, name in enumerate(tool_names, start=1):
            tool_calls.append(
                SimpleNamespace(
                    id=f"call_{i}",
                    type="function",
                    function=SimpleNamespace(name=name, arguments="{}"),
                )
            )
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        if not self._responses:
            raise AssertionError("测试响应已耗尽，请检查循环是否未按预期结束。")
        return self._responses.pop(0)


def reminder_count(messages):
    reminder_text = (
        "提醒：你已经连续多轮未更新 todo，请尽快使用 "
        "todo_manager_update 同步当前计划与进度。"
    )
    return sum(
        1
        for m in messages
        if m.get("role") == "user" and m.get("content") == reminder_text
    )


def patch_handlers(monkeypatch):
    monkeypatch.setitem(main_module.TOOL_HANDLERS, "bash", lambda **kw: "ok")
    monkeypatch.setitem(
        main_module.TOOL_HANDLERS, "todo_manager_update", lambda **kw: "todo ok"
    )


def test_third_round_triggers_reminder(monkeypatch):
    patch_handlers(monkeypatch)
    responses = [
        build_response("r1", ["bash"]),
        build_response("r2", ["bash"]),
        build_response("r3", ["bash"]),
        build_response("done", None),
    ]
    monkeypatch.setattr(main_module, "client", FakeClient(responses))

    messages = main_module.agent_loop("test")

    assert reminder_count(messages) == 1


def test_reminder_continues_after_threshold(monkeypatch):
    patch_handlers(monkeypatch)
    responses = [
        build_response("r1", ["bash"]),
        build_response("r2", ["bash"]),
        build_response("r3", ["bash"]),
        build_response("r4", ["bash"]),
        build_response("done", None),
    ]
    monkeypatch.setattr(main_module, "client", FakeClient(responses))

    messages = main_module.agent_loop("test")

    assert reminder_count(messages) == 2


def test_todo_call_resets_streak(monkeypatch):
    patch_handlers(monkeypatch)
    responses = [
        build_response("r1", ["bash"]),
        build_response("r2", ["bash"]),
        build_response("r3", ["bash"]),
        build_response("todo", ["todo_manager_update"]),
        build_response("r4", ["bash"]),
        build_response("done", None),
    ]
    monkeypatch.setattr(main_module, "client", FakeClient(responses))

    messages = main_module.agent_loop("test")

    assert reminder_count(messages) == 1
