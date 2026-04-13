from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from ..core.hooks import BaseHook, HookContext, HookFilter, HookResult, fail_hook_result

PluginHookType = Literal["command", "http", "prompt", "agent"]


@dataclass
class PluginHookConfig:
    """插件 Hook 配置。

    插件 Hook 统一消费 HookContext，并返回 HookResult JSON。具体类型只决定
    “如何执行扩展逻辑”，不改变 Hook 协议本身。
    """

    name: str
    type: PluginHookType
    scope: str
    event: str
    stage: str
    order: int = 1000
    fail_fast: bool = False
    enabled: bool = True
    timeout_ms: int | None = None
    filters: HookFilter = field(default_factory=HookFilter)
    command: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    agent: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginHook(BaseHook):
    """配置化插件 Hook，支持 command/http/prompt/agent 四类执行器。"""

    def __init__(self, config: PluginHookConfig) -> None:
        super().__init__(
            name=config.name,
            source="plugin",
            order=config.order,
            fail_fast=config.fail_fast,
            enabled=config.enabled,
            timeout_ms=config.timeout_ms,
            filters=config.filters,
            metadata=config.metadata,
        )
        self.config = config

    def should_run(self, ctx: Mapping[str, Any]) -> bool:
        if not super().should_run(ctx):
            return False
        event = ctx.get("event", {}) if isinstance(ctx.get("event"), Mapping) else {}
        return (
            str(event.get("scope", "")).strip() == self.config.scope
            and str(event.get("name", "")).strip() == self.config.event
            and str(event.get("stage", "")).strip() == self.config.stage
        )

    def handle(self, ctx: HookContext) -> HookResult:
        if self.config.type == "command":
            return _run_command_hook(self.config, ctx)
        if self.config.type == "http":
            return _run_http_hook(self.config, ctx)
        if self.config.type == "prompt":
            return _run_prompt_hook(self.config, ctx)
        if self.config.type == "agent":
            return _run_agent_hook(self.config, ctx)
        return fail_hook_result(f"Unsupported plugin hook type: {self.config.type}")


def _parse_hook_result(raw: str) -> HookResult:
    text = raw.strip()
    if not text:
        return HookResult({"decision": "allow"})
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return fail_hook_result(f"Hook result is not valid JSON: {exc}")
    if not isinstance(value, dict):
        return fail_hook_result("Hook result must be a JSON object")
    return HookResult(value)


def _context_json(ctx: HookContext) -> bytes:
    return json.dumps(ctx, ensure_ascii=False, default=str).encode("utf-8")


def _timeout_seconds(timeout_ms: int | None) -> float | None:
    if timeout_ms is None:
        return None
    return max(timeout_ms, 1) / 1000


def _run_command_hook(config: PluginHookConfig, ctx: HookContext) -> HookResult:
    if not config.command:
        return fail_hook_result("Command hook requires command argv")
    try:
        completed = subprocess.run(
            config.command,
            input=_context_json(ctx),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_timeout_seconds(config.timeout_ms),
            check=False,
        )
    except Exception as exc:
        return fail_hook_result(f"Command hook failed: {type(exc).__name__}: {exc}")
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        return fail_hook_result(f"Command hook exited with {completed.returncode}: {stderr}")
    return _parse_hook_result(completed.stdout.decode("utf-8", errors="replace"))


def _run_http_hook(config: PluginHookConfig, ctx: HookContext) -> HookResult:
    if not config.url:
        return fail_hook_result("HTTP hook requires url")
    request = urllib.request.Request(
        config.url,
        data=_context_json(ctx),
        headers={"Content-Type": "application/json", **config.headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds(config.timeout_ms)) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return fail_hook_result(f"HTTP hook failed with {exc.code}: {detail}")
    except Exception as exc:
        return fail_hook_result(f"HTTP hook failed: {type(exc).__name__}: {exc}")
    return _parse_hook_result(body)


def _run_prompt_hook(config: PluginHookConfig, ctx: HookContext) -> HookResult:
    if not config.prompt:
        return fail_hook_result("Prompt hook requires prompt")
    try:
        from ..core.message import append_text_part, create_message
        from ..adapters.llm.client import create_chat_completion
        from ..config.settings import resolve_llm_config

        session_id = str(ctx.get("identity", {}).get("session_id", "")).strip() or "hook"
        model_config = resolve_llm_config("build", model_name=config.model) if config.model else resolve_llm_config("build")
        user_message = create_message("user", session_id, status="completed")
        append_text_part(
            user_message,
            config.prompt + "\n\nHookContext JSON:\n" + json.dumps(ctx, ensure_ascii=False, default=str),
        )
        message = create_chat_completion(
            [user_message],
            tools=[],
            hooks=[],
            llm_config=model_config,
            agent="hook",
        )
        content = "\n".join(
            str(part.get("content", ""))
            for part in message.get("parts", [])
            if part.get("type") in {"text", "error"}
        )
        return _parse_hook_result(content)
    except Exception as exc:
        return fail_hook_result(f"Prompt hook failed: {type(exc).__name__}: {exc}")


def _run_agent_hook(config: PluginHookConfig, ctx: HookContext) -> HookResult:
    if not config.agent:
        return fail_hook_result("Agent hook requires agent")
    try:
        from .session import subagent_loop

        session_id = str(ctx.get("identity", {}).get("session_id", "")).strip() or "hook"
        prompt = config.prompt or "请根据 HookContext 判断是否放行，并只返回 HookResult JSON。"
        output = subagent_loop(
            prompt + "\n\nHookContext JSON:\n" + json.dumps(ctx, ensure_ascii=False, default=str),
            agent=config.agent,
            session_id=session_id,
        )
        return _parse_hook_result(output)
    except Exception as exc:
        return fail_hook_result(f"Agent hook failed: {type(exc).__name__}: {exc}")


def build_plugin_hooks(configs: Sequence[PluginHookConfig]) -> list[PluginHook]:
    return [PluginHook(config) for config in configs]
