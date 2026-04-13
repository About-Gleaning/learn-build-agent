import pytest

from agent.runtime.delegation_hooks import (
    DelegationHook,
    DelegationHookContext,
    clear_global_delegation_hooks,
    register_global_delegation_hook,
    resolve_effective_delegation_hooks,
    run_delegation_hooks,
)


class RecorderDelegationHook(DelegationHook):
    def __init__(self, name: str, records: list[str], *, order: int = 1000) -> None:
        super().__init__(name, order=order)
        self.records = records

    def on_delegation_requested(self, ctx: DelegationHookContext) -> None:
        self.records.append(f"{self.name}.requested:{ctx.get('delegation_id')}")

    def on_delegation_started(self, ctx: DelegationHookContext) -> None:
        self.records.append(f"{self.name}.started:{ctx.get('delegation_id')}")

    def on_delegation_completed(self, ctx: DelegationHookContext) -> None:
        self.records.append(f"{self.name}.completed:{ctx.get('delegation_id')}")

    def on_delegation_finally(self, ctx: DelegationHookContext) -> None:
        self.records.append(f"{self.name}.finally:{ctx.get('delegation_id')}")


class BrokenDelegationHook(DelegationHook):
    def __init__(self, stage: str, *, fail_fast: bool = False) -> None:
        super().__init__("broken_delegation", fail_fast=fail_fast)
        self.stage = stage

    def _raise_if_stage(self, stage: str) -> None:
        if self.stage == stage:
            raise RuntimeError(f"{stage} boom")

    def on_delegation_requested(self, ctx: DelegationHookContext) -> None:
        self._raise_if_stage("requested")

    def on_delegation_started(self, ctx: DelegationHookContext) -> None:
        self._raise_if_stage("started")

    def on_delegation_completed(self, ctx: DelegationHookContext) -> None:
        self._raise_if_stage("after")

    def on_delegation_failed(self, ctx: DelegationHookContext, error: Exception, normalized_error: dict) -> None:
        self._raise_if_stage("error")

    def on_delegation_interrupted(self, ctx: DelegationHookContext) -> None:
        self._raise_if_stage("interrupted")

    def on_delegation_finally(self, ctx: DelegationHookContext) -> None:
        self._raise_if_stage("finally")


def test_delegation_hooks_should_sort_and_reverse_finally():
    records: list[str] = []
    ctx: DelegationHookContext = {
        "session_id": "s1",
        "delegation_id": "dg1",
        "agent": "explore",
        "agent_kind": "subagent",
    }
    clear_global_delegation_hooks()
    register_global_delegation_hook(RecorderDelegationHook("late", records, order=200))
    register_global_delegation_hook(RecorderDelegationHook("early", records, order=100))

    hooks = resolve_effective_delegation_hooks()
    run_delegation_hooks(hooks, "requested", ctx=ctx)
    run_delegation_hooks(hooks, "started", ctx=ctx)
    run_delegation_hooks(hooks, "after", ctx=ctx)
    run_delegation_hooks(hooks, "finally", ctx=ctx)

    assert records == [
        "early.requested:dg1",
        "late.requested:dg1",
        "early.started:dg1",
        "late.started:dg1",
        "late.completed:dg1",
        "early.completed:dg1",
        "late.finally:dg1",
        "early.finally:dg1",
    ]

    clear_global_delegation_hooks()


def test_delegation_hooks_should_fail_open_for_requested_interrupted_and_finally():
    ctx: DelegationHookContext = {
        "session_id": "s1",
        "delegation_id": "dg1",
        "agent": "explore",
        "agent_kind": "subagent",
    }

    for stage in ["requested", "interrupted", "finally"]:
        run_delegation_hooks([BrokenDelegationHook(stage, fail_fast=False)], stage, ctx=ctx)


def test_delegation_hooks_should_fail_fast_for_requested_interrupted_and_finally():
    ctx: DelegationHookContext = {
        "session_id": "s1",
        "delegation_id": "dg1",
        "agent": "explore",
        "agent_kind": "subagent",
    }

    for stage in ["requested", "interrupted", "finally"]:
        with pytest.raises(RuntimeError, match="Hook 'broken_delegation' failed"):
            run_delegation_hooks([BrokenDelegationHook(stage, fail_fast=True)], stage, ctx=ctx)
