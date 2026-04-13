import sys

from agent.core.hooks import (
    BaseHook,
    HookContext,
    HookDispatcher,
    HookFilter,
    allow_hook_result,
    build_hook_context,
    deny_hook_result,
)
from agent.runtime.plugin_hooks import PluginHook, PluginHookConfig


class RecorderHook(BaseHook):
    def __init__(
        self,
        name: str,
        records: list[str],
        *,
        order: int = 1000,
        fail_fast: bool = False,
        filters: HookFilter | None = None,
        decision: str = "allow",
    ) -> None:
        super().__init__(name, order=order, fail_fast=fail_fast, filters=filters)
        self.records = records
        self.decision = decision

    def handle(self, ctx: HookContext):
        self.records.append(f"{self.name}:{ctx['event']['name']}")
        if self.decision == "deny":
            return deny_hook_result("blocked")
        return allow_hook_result()


def test_dispatch_event_should_sort_by_order_and_keep_stable_order():
    records: list[str] = []
    dispatcher = HookDispatcher(logger=__import__("logging").getLogger(__name__), name="test")
    ctx = build_hook_context(scope="tool", name="tool.call_detected", stage="before")

    dispatcher.dispatch_event(
        [
            RecorderHook("late", records, order=200),
            RecorderHook("early", records, order=100),
            RecorderHook("same", records, order=200),
        ],
        ctx=ctx,
    )

    assert records == [
        "early:tool.call_detected",
        "late:tool.call_detected",
        "same:tool.call_detected",
    ]


def test_dispatch_event_should_stop_on_deny():
    records: list[str] = []
    dispatcher = HookDispatcher(logger=__import__("logging").getLogger(__name__), name="test")
    ctx = build_hook_context(scope="tool", name="tool.call_detected", stage="before")

    results = dispatcher.dispatch_event(
        [
            RecorderHook("first", records, order=100, decision="deny"),
            RecorderHook("second", records, order=200),
        ],
        ctx=ctx,
    )

    assert records == ["first:tool.call_detected"]
    assert results[0]["decision"] == "deny"


def test_dispatch_event_should_match_filters():
    records: list[str] = []
    dispatcher = HookDispatcher(logger=__import__("logging").getLogger(__name__), name="test")
    ctx = build_hook_context(
        scope="tool",
        name="tool.call_detected",
        stage="before",
        agent={"name": "build", "kind": "primary", "depth": 0},
        data={"tool_name": "todo_read"},
    )

    dispatcher.dispatch_event(
        [
            RecorderHook(
                "matched",
                records,
                filters=HookFilter(agent_names=frozenset({"build"}), tool_names=frozenset({"todo_read"})),
            ),
            RecorderHook(
                "skipped",
                records,
                filters=HookFilter(agent_names=frozenset({"explore"})),
            ),
        ],
        ctx=ctx,
    )

    assert records == ["matched:tool.call_detected"]


def test_command_plugin_hook_should_receive_context_and_return_hook_result():
    ctx = build_hook_context(
        scope="tool",
        name="tool.call_detected",
        stage="before",
        data={"tool_name": "todo_read"},
    )
    code = (
        "import json,sys;"
        "ctx=json.load(sys.stdin);"
        "print(json.dumps({'decision':'allow','metadata':{'event':ctx['event']['name']}}))"
    )
    hook = PluginHook(
        PluginHookConfig(
            name="command-demo",
            type="command",
            scope="tool",
            event="tool.call_detected",
            stage="before",
            command=[sys.executable, "-c", code],
        )
    )

    result = hook.handle(ctx)

    assert result["decision"] == "allow"
    assert result["metadata"]["event"] == "tool.call_detected"


def test_command_plugin_hook_should_report_invalid_json():
    ctx = build_hook_context(scope="tool", name="tool.call_detected", stage="before")
    hook = PluginHook(
        PluginHookConfig(
            name="bad-command",
            type="command",
            scope="tool",
            event="tool.call_detected",
            stage="before",
            command=[sys.executable, "-c", "print('not-json')"],
        )
    )

    result = hook.handle(ctx)

    assert result["decision"] == "fail"
    assert "valid JSON" in result["reason"]
