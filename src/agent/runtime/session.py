import logging
import re
import subprocess
import uuid
import json
import inspect
from datetime import datetime
from pathlib import Path
from collections.abc import Generator
from typing import Any, Callable, Literal, TypedDict

from ..adapters.llm.client import create_chat_completion, create_chat_completion_stream
from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..config.settings import ResolvedLLMConfig, resolve_llm_config
from ..core.context import set_session_id
from ..core.message import (
    DisplayPart,
    Message,
    ProcessItem,
    ResponseMeta,
    append_tool_part,
    append_text_part,
    create_message,
    extract_tool_calls,
    get_role,
    get_message_text,
    utc_now_iso,
)
from ..runtime.agents import get_agent
from ..skills.runtime import SkillRegistry
from ..tools.handlers import (
    build_plan_placeholder_path,
    is_allowed_plan_write_path,
    run_bash,
    run_edit,
    run_plan_enter,
    run_plan_exit,
    run_read,
    run_write,
    validate_readonly_bash,
)
from ..tools.specs import build_agent_tools, build_base_tools
from ..tools.todo_manager import TodoManager
from ..tools.webfetch import webfetch
from ..tools.websearch import websearch
from .compaction import compact
from .session_memory import InMemorySessionMemoryStore, SessionMemoryStore
from .tool_executor import ToolExecutor, ToolHook, ToolResult, get_global_tool_hooks

logger = logging.getLogger(__name__)

MainAgentMode = Literal["build", "plan"]

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
registry = SkillRegistry(SKILLS_ROOT)
registry.discover()

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

TODO = TodoManager()
SESSION_MEMORY_STORE: SessionMemoryStore = InMemorySessionMemoryStore(max_messages=24)
ModeSwitchAction = Literal["confirm", "cancel"]


class PendingModeSwitch(TypedDict):
    tool_name: str
    question: str
    target_agent: str
    current_agent: str
    action_type: str
    plan_path: str
    plan_exists: bool
    model: str
    requested_at: str


PENDING_MODE_SWITCHES: dict[str, PendingModeSwitch] = {}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
]


def _normalize_prompt_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower()).strip("._-")
    return normalized or "default"


def _get_workdir() -> Path:
    return Path.cwd()


def _resolve_build_prompt_path(vendor: str) -> Path:
    normalized_vendor = _normalize_prompt_key(vendor)
    candidate = PROMPTS_DIR / f"build.{normalized_vendor}.txt"
    if candidate.exists():
        return candidate
    return PROMPTS_DIR / "build.default.txt"


def _resolve_prompt_path(agent: str, vendor: str) -> Path:
    agent_name = agent.strip().lower()
    if agent_name == "build":
        return _resolve_build_prompt_path(vendor)
    if agent_name == "plan":
        return PROMPTS_DIR / "plan.txt"
    candidate = PROMPTS_DIR / f"{agent_name}.txt"
    if candidate.exists():
        return candidate
    raise ValueError(f"未知的 prompt agent: {agent}")


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"未找到 prompt 文件: {path}")
    return path.read_text(encoding="utf-8").strip()


def _read_local_agent_appendix() -> str:
    agent_md_path = _get_workdir() / "AGENTS.md"
    if not agent_md_path.exists():
        return ""
    try:
        content = agent_md_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("prompt.agent_md.read_failed path=%s error=%s", agent_md_path, exc)
        return ""
    if not content:
        return ""
    return f"以下是当前工作目录下的 AGENTS.md 内容，请一并遵守：\n\n{content}"


def _detect_git_repository(workdir: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, result.stdout.strip()


def _build_environment_appendix(*, agent: str, model: str, provider: str, vendor: str) -> str:
    workdir = _get_workdir()
    is_git_repo, git_root = _detect_git_repository(workdir)
    now_text = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "以下是当前运行环境信息：",
        f"- agent: {agent}",
        f"- vendor: {vendor or 'unknown'}",
        f"- provider: {provider or 'unknown'}",
        f"- model: {model or 'unknown'}",
        f"- workdir: {workdir}",
        f"- is_git_repo: {'true' if is_git_repo else 'false'}",
        f"- git_root: {git_root or 'N/A'}",
        f"- current_datetime: {now_text}",
    ]
    return "\n".join(lines)


def build_system_prompt(*, agent: str, model: str, provider: str, vendor: str) -> str:
    base_prompt = _read_prompt_file(_resolve_prompt_path(agent, vendor))
    parts = [
        base_prompt,
        _read_local_agent_appendix(),
        _build_environment_appendix(agent=agent, model=model, provider=provider, vendor=vendor),
    ]
    return "\n\n".join(part for part in parts if part)


def _get_system_prompt_for_mode(mode: MainAgentMode, *, model: str, provider: str, vendor: str) -> str:
    return build_system_prompt(agent=mode, model=model, provider=provider, vendor=vendor)


def _get_tools_for_mode(mode: MainAgentMode) -> list[dict]:
    return build_agent_tools(mode, registry.list_briefs())


def _ensure_system_prompt(messages: list[Message], prompt: str, session_id: str) -> list[Message]:
    system_msg = _build_text_message("system", prompt, session_id)
    if messages and get_role(messages[0]) == "system":
        messages[0] = system_msg
        return messages
    return [system_msg, *messages]


def _latest_model(messages: list[Message]) -> str:
    for msg in reversed(messages):
        model = str(msg["info"].get("model", "")).strip()
        if model:
            return model
    return ""


def _latest_provider(messages: list[Message]) -> str:
    for msg in reversed(messages):
        provider = str(msg["info"].get("provider", "")).strip().lower()
        if provider:
            return provider
    return ""


def _iter_user_text_meta(messages: list[Message]) -> Generator[dict[str, Any], None, None]:
    for msg in reversed(messages):
        if get_role(msg) != "user":
            continue
        for part in reversed(msg["parts"]):
            if part.get("type") != "text":
                continue
            meta = part.get("meta") or {}
            if isinstance(meta, dict):
                yield meta


def _resolve_mode_from_messages(messages: list[Message], fallback: MainAgentMode = "build") -> MainAgentMode:
    for meta in _iter_user_text_meta(messages):
        agent = str(meta.get("agent", "")).strip().lower()
        if agent in {"build", "plan"}:
            return agent  # type: ignore[return-value]
    return fallback


def _resolve_provider_preference_from_messages(messages: list[Message]) -> str | None:
    for meta in _iter_user_text_meta(messages):
        if bool(meta.get("provider_reset_to_default")):
            return ""
        provider = str(meta.get("provider", "")).strip().lower()
        if provider and bool(meta.get("provider_explicit")):
            return provider
    return None


def _resolve_provider_selection(
    messages: list[Message],
    *,
    mode: MainAgentMode,
    provider: str | None,
    provider_specified: bool,
) -> tuple[str, bool]:
    normalized_provider = (provider or "").strip().lower()
    if provider_specified:
        if normalized_provider:
            return normalized_provider, True
        return resolve_llm_config(mode).provider, False

    inherited_provider = _resolve_provider_preference_from_messages(messages)
    if inherited_provider:
        return inherited_provider, True
    return resolve_llm_config(mode).provider, False


def _resolve_runtime_config(
    messages: list[Message],
    *,
    mode: MainAgentMode,
    provider: str | None,
    provider_specified: bool,
) -> tuple[ResolvedLLMConfig, bool]:
    provider_name, is_explicit = _resolve_provider_selection(
        messages,
        mode=mode,
        provider=provider,
        provider_specified=provider_specified,
    )
    return resolve_llm_config(mode, provider_name), is_explicit


def _build_confirmation_interrupted_message(
    session_id: str,
    *,
    tool_name: str,
    output_text: str,
    question: str,
    metadata: dict[str, Any],
) -> Message:
    message = create_message(
        role="assistant",
        session_id=session_id,
        status="interrupted",
        finish_reason="confirmation_required",
    )
    text = f"{output_text}\n{question}".strip()
    append_text_part(
        message,
        text,
        meta={
            "tool": tool_name,
            "confirmation_required": True,
            "tool_metadata": metadata,
        },
    )
    message["info"]["confirmation"] = {
        "tool": tool_name,
        "question": question,
        "target_agent": str(metadata.get("target_agent", "")).strip(),
        "current_agent": str(metadata.get("current_agent", "")).strip(),
        "action_type": str(metadata.get("action_type", "")).strip(),
        "plan_path": str(metadata.get("plan_path", "")).strip(),
    }
    return message


def _save_pending_mode_switch(session_id: str, metadata: dict[str, Any]) -> None:
    target_agent = str(metadata.get("target_agent", "")).strip().lower()
    current_agent = str(metadata.get("current_agent", "")).strip().lower()
    action_type = str(metadata.get("action_type", "")).strip().lower()
    if target_agent not in {"build", "plan"}:
        raise ValueError("无效的目标模式")
    if current_agent not in {"build", "plan"}:
        raise ValueError("无效的当前模式")
    if action_type not in {"enter_plan", "exit_plan"}:
        raise ValueError("无效的模式切换动作")
    PENDING_MODE_SWITCHES[session_id] = PendingModeSwitch(
        tool_name=str(metadata.get("tool_name", "")).strip() or ("plan_enter" if action_type == "enter_plan" else "plan_exit"),
        question=str(metadata.get("confirmation_question", "是否继续？")).strip() or "是否继续？",
        target_agent=target_agent,
        current_agent=current_agent,
        action_type=action_type,
        plan_path=str(metadata.get("plan_path", "")).strip(),
        plan_exists=bool(metadata.get("plan_exists")),
        model=str(metadata.get("model", "")).strip(),
        requested_at=utc_now_iso(),
    )


def get_pending_mode_switch(session_id: str) -> PendingModeSwitch | None:
    return PENDING_MODE_SWITCHES.get(session_id)


def _clear_pending_mode_switch(session_id: str | None = None) -> None:
    normalized = (session_id or "").strip()
    if not normalized:
        PENDING_MODE_SWITCHES.clear()
        return
    PENDING_MODE_SWITCHES.pop(normalized, None)


def _build_mode_switch_confirm_input(pending: PendingModeSwitch) -> str:
    action_type = str(pending.get("action_type", "")).strip().lower()
    plan_path = str(pending.get("plan_path", "")).strip()
    plan_exists = bool(pending.get("plan_exists"))
    if action_type == "enter_plan":
        extra = "已有计划文件，请继续完善它。" if plan_exists else "当前还没有计划文件，请创建计划。"
        return f"用户已确认切换到 plan 模式。请切换到 plan 模式并开始制定计划。{extra}"
    extra = f"计划文件在 {plan_path}，请按计划开始实施。" if plan_exists and plan_path else "请按已确认的计划开始实施。"
    return f"用户已确认计划已完成，请切换到 build 模式并开始执行。{extra}"


def _build_mode_switch_cancel_text(pending: PendingModeSwitch) -> str:
    target_agent = str(pending.get('target_agent', '')).strip().lower()
    if target_agent == "plan":
        return "已取消切换到 plan 模式，继续保持当前 build 模式。"
    return "已取消切换到 build 模式，继续保持当前 plan 模式。"


def _append_synthetic_user_message(
    messages: list[Message],
    *,
    session_id: str,
    agent: MainAgentMode,
    text: str,
    plan_path: str,
    provider: str,
    provider_explicit: bool,
    model: str,
) -> None:
    synthetic = _build_text_message(
        "user",
        text,
        session_id,
        model=model,
        provider=provider,
        text_meta={
            "agent": agent,
            "synthetic": True,
            "plan_path": plan_path,
            "provider": provider,
            "provider_explicit": provider_explicit,
            "model": model,
        },
    )
    messages.append(synthetic)

def _build_text_message(
    role: str,
    content: str,
    session_id: str,
    *,
    model: str = "",
    provider: str = "",
    text_meta: dict[str, Any] | None = None,
) -> Message:
    message = create_message(role=role, session_id=session_id, model=model, provider=provider)
    append_text_part(message, content, meta=text_meta)
    return message


def _supports_keyword_arg(func: Callable[..., Any], arg_name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == arg_name:
            return True
    return False


def _call_chat_completion(
    *,
    messages: list[Message],
    tools: list[dict[str, Any]],
    llm_config: ResolvedLLMConfig,
    agent: str,
) -> Message:
    kwargs: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "llm_config": llm_config,
    }
    if _supports_keyword_arg(create_chat_completion, "agent"):
        kwargs["agent"] = agent
    return create_chat_completion(**kwargs)


def _call_chat_completion_stream(
    *,
    messages: list[Message],
    tools: list[dict[str, Any]],
    llm_config: ResolvedLLMConfig,
    agent: str,
) -> Generator[dict[str, Any], None, Message]:
    kwargs: dict[str, Any] = {
        "messages": messages,
        "tools": tools,
        "llm_config": llm_config,
    }
    if _supports_keyword_arg(create_chat_completion_stream, "agent"):
        kwargs["agent"] = agent
    return create_chat_completion_stream(**kwargs)


def _set_message_runtime_info(
    message: Message,
    *,
    agent: str,
    model: str | None = None,
    provider: str | None = None,
    turn_started_at: str,
    turn_completed_at: str | None = None,
) -> None:
    message["info"]["agent"] = agent
    if model:
        message["info"]["model"] = model
    if provider:
        message["info"]["provider"] = provider
    message["info"]["turn_started_at"] = turn_started_at
    if turn_completed_at:
        message["info"]["turn_completed_at"] = turn_completed_at


def _build_tool_message(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: str,
    result: ToolResult,
    *,
    agent: str,
    turn_started_at: str,
) -> Message:
    message = create_message(role="tool", session_id=session_id)
    status = str((result.get("metadata") or {}).get("status", "completed")).strip().lower()
    if status not in {"completed", "failed"}:
        status = "completed"
    append_tool_part(
        message,
        tool_call_id=tool_call_id,
        name=tool_name,
        status=status,  # type: ignore[arg-type]
        arguments=arguments,
        output=result,
    )
    _set_message_runtime_info(
        message,
        agent=agent,
        turn_started_at=turn_started_at,
        turn_completed_at=utc_now_iso(),
    )
    return message


def _sanitize_preview(text: str, *, limit: int = 220) -> str:
    normalized = text.replace("\r", " ").replace("\n", " ").strip()
    if not normalized:
        return ""
    masked = normalized
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[MASKED]", masked)
    if len(masked) > limit:
        return masked[:limit] + "...<truncated>"
    return masked


def _tool_result_preview(result: ToolResult) -> str:
    raw_output = result.get("output", "")
    if isinstance(raw_output, str):
        return _sanitize_preview(raw_output)
    try:
        return _sanitize_preview(json.dumps(raw_output, ensure_ascii=False))
    except Exception:
        return _sanitize_preview(str(raw_output))


def _new_stream_event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _new_delegation_id() -> str:
    return f"delegation_{uuid.uuid4().hex[:12]}"


def _resolve_agent_kind(agent_name: str) -> str:
    definition = get_agent(agent_name)
    if definition is None:
        return "primary"
    return definition.model


def _build_stream_event(
    event_type: str,
    *,
    session_id: str,
    agent: str,
    agent_kind: str,
    depth: int,
    delegation_id: str | None = None,
    parent_tool_call_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type,
        "event_id": _new_stream_event_id(),
        "timestamp": utc_now_iso(),
        "session_id": session_id,
        "agent": agent,
        "agent_kind": agent_kind,
        "depth": depth,
    }
    if delegation_id:
        event["delegation_id"] = delegation_id
    if parent_tool_call_id:
        event["parent_tool_call_id"] = parent_tool_call_id
    event.update(payload)
    return event


def _describe_runtime(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "")).strip()
    agent = str(payload.get("agent", "")).strip()
    provider = str(payload.get("provider", "")).strip()
    model = str(payload.get("model", "")).strip()
    tags = [mode or agent, provider, model]
    return " / ".join([tag for tag in tags if tag])


def _describe_agent(payload: dict[str, Any]) -> str:
    agent = str(payload.get("agent", "unknown")).strip() or "unknown"
    agent_kind = "子代理" if str(payload.get("agent_kind", "primary")).strip() == "subagent" else "主代理"
    return f"{agent_kind} · {agent}"


def _build_process_item(event: dict[str, Any]) -> ProcessItem | None:
    event_type = str(event.get("type", "")).strip()
    if not event_type or event_type == "text_delta":
        return None

    payload = dict(event)
    created_at = str(payload.get("timestamp", "")).strip() or utc_now_iso()
    agent = str(payload.get("agent", "unknown")).strip() or "unknown"
    agent_kind = str(payload.get("agent_kind", "primary")).strip() or "primary"
    depth = int(payload.get("depth", 0) or 0)
    round_no = int(payload.get("round", 0) or 0)
    delegation_id = str(payload.get("delegation_id", "")).strip()
    parent_tool_call_id = str(payload.get("parent_tool_call_id", "")).strip()
    status = str(payload.get("status", "")).strip()
    tool_name = str(payload.get("name", "")).strip()
    tool_call_id = str(payload.get("tool_call_id", "")).strip()

    title = f"{agent} 事件: {event_type}"
    detail = json.dumps(payload, ensure_ascii=False)
    if event_type == "start":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 会话开始"
        detail = f"{_describe_agent(payload)}{f' · {runtime_desc}' if runtime_desc else ''}"
    elif event_type == "round_start":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 第 {round_no} 轮开始"
        detail = runtime_desc or _describe_agent(payload)
    elif event_type == "tool_call":
        title = f"{agent} 调用工具: {tool_name or 'unknown'}"
        detail = str(payload.get("arguments", "{}"))
    elif event_type == "tool_result":
        title = f"{agent} 工具结果: {tool_name or 'unknown'}"
        if tool_name == "task":
            title = f"{agent} 委派结果"
        detail = f"{status or 'completed'} {str(payload.get('output_preview', '')).strip()}".strip()
    elif event_type == "round_end":
        title = f"{agent} 第 {round_no} 轮结束"
        detail = f"状态: {status or 'completed'}"
    elif event_type == "done":
        runtime_desc = _describe_runtime(payload)
        title = f"{agent} 会话完成"
        detail = f"{status or 'completed'} {runtime_desc}".strip()
    elif event_type == "error":
        title = f"{agent} 会话异常"
        detail = str(payload.get("message", "未知错误"))

    return {
        "id": str(payload.get("event_id", "")),
        "kind": event_type,
        "title": title,
        "detail": detail,
        "created_at": created_at,
        "agent": agent,
        "agent_kind": agent_kind,
        "depth": depth,
        "round": round_no,
        "status": status,
        "delegation_id": delegation_id,
        "parent_tool_call_id": parent_tool_call_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
    }


def _should_hide_display_event(event_type: str) -> bool:
    return event_type in {"start", "round_start", "round_end", "done"}


def _build_display_part_from_event(event: dict[str, Any]) -> DisplayPart | None:
    process_item = _build_process_item(event)
    if process_item is None:
        return None

    if _should_hide_display_event(str(process_item.get("kind", "")).strip()):
        return None

    return {
        "id": str(process_item.get("id", "")),
        "kind": str(process_item.get("kind", "")),
        "title": str(process_item.get("title", "")),
        "detail": str(process_item.get("detail", "")),
        "text": "",
        "created_at": str(process_item.get("created_at", "")),
        "agent": str(process_item.get("agent", "")),
        "agent_kind": str(process_item.get("agent_kind", "")),
        "depth": int(process_item.get("depth", 0) or 0),
        "round": int(process_item.get("round", 0) or 0),
        "status": str(process_item.get("status", "")),
        "delegation_id": str(process_item.get("delegation_id", "")),
        "parent_tool_call_id": str(process_item.get("parent_tool_call_id", "")),
        "tool_name": str(process_item.get("tool_name", "")),
        "tool_call_id": str(process_item.get("tool_call_id", "")),
    }


def _append_display_text_part(
    display_parts: list[DisplayPart],
    *,
    delta: str,
    created_at: str,
    agent: str,
    agent_kind: str,
    depth: int,
    round_no: int,
    delegation_id: str | None,
    parent_tool_call_id: str | None,
    merge_allowed: bool,
) -> None:
    if not delta:
        return

    if merge_allowed and display_parts:
        last_part = display_parts[-1]
        if (
            str(last_part.get("kind", "")) == "assistant_text"
            and str(last_part.get("agent", "")) == agent
            and str(last_part.get("agent_kind", "")) == agent_kind
            and int(last_part.get("depth", 0) or 0) == depth
            and int(last_part.get("round", 0) or 0) == round_no
            and str(last_part.get("delegation_id", "")) == str(delegation_id or "")
            and str(last_part.get("parent_tool_call_id", "")) == str(parent_tool_call_id or "")
        ):
            last_part["text"] = f"{str(last_part.get('text', ''))}{delta}"
            return

    display_parts.append(
        {
            "id": _new_stream_event_id(),
            "kind": "assistant_text",
            "title": f"{agent} 回复",
            "detail": "",
            "text": delta,
            "created_at": created_at,
            "agent": agent,
            "agent_kind": agent_kind,
            "depth": depth,
            "round": round_no,
            "status": "completed",
            "delegation_id": str(delegation_id or ""),
            "parent_tool_call_id": str(parent_tool_call_id or ""),
            "tool_name": "",
            "tool_call_id": "",
        }
    )


def _append_display_event_part(
    display_parts: list[DisplayPart],
    *,
    event: dict[str, Any],
) -> None:
    display_part = _build_display_part_from_event(event)
    if display_part is None:
        return
    display_parts.append(display_part)


def _build_display_parts_from_message(message: Message) -> list[DisplayPart]:
    info = message.get("info", {})
    agent = str(info.get("agent", "")).strip()
    created_at = str(info.get("created_at", "")).strip() or utc_now_iso()
    parts: list[DisplayPart] = []
    for part in message.get("parts", []):
        part_type = str(part.get("type", "")).strip()
        if part_type not in {"text", "compaction", "compact_summary", "reasoning", "error"}:
            continue
        content = str(part.get("content", ""))
        if not content:
            continue
        parts.append(
            {
                "id": str(part.get("part_id", "")) or _new_stream_event_id(),
                "kind": "assistant_text" if part_type != "error" else "error",
                "title": f"{agent or 'assistant'} 回复" if part_type != "error" else f"{agent or 'assistant'} 会话异常",
                "detail": "" if part_type != "error" else content,
                "text": content if part_type != "error" else "",
                "created_at": str(part.get("created_at", "")) or created_at,
                "agent": agent,
                "agent_kind": "primary",
                "depth": 0,
                "round": 0,
                "status": str(info.get("status", "")),
                "delegation_id": "",
                "parent_tool_call_id": "",
                "tool_name": "",
                "tool_call_id": "",
            }
        )
    return parts


def _merge_display_parts_with_message(display_parts: list[DisplayPart], message: Message) -> list[DisplayPart]:
    merged = [dict(item) for item in display_parts]
    fallback_parts = _build_display_parts_from_message(message)
    if not fallback_parts:
        return merged
    if not merged:
        return fallback_parts

    for fallback_part in fallback_parts:
        last_part = merged[-1] if merged else None
        if (
            last_part
            and str(last_part.get("kind", "")) == str(fallback_part.get("kind", ""))
            and str(last_part.get("text", "")) == str(fallback_part.get("text", ""))
            and str(last_part.get("detail", "")) == str(fallback_part.get("detail", ""))
            and str(last_part.get("agent", "")) == str(fallback_part.get("agent", ""))
        ):
            continue
        merged.append(fallback_part)
    return merged


def _compute_duration_ms(started_at: str, completed_at: str) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        start_dt = datetime.fromisoformat(started_at)
        completed_dt = datetime.fromisoformat(completed_at)
    except ValueError:
        return 0
    duration = int((completed_dt - start_dt).total_seconds() * 1000)
    return max(duration, 0)


def _build_response_meta(process_items: list[ProcessItem], *, turn_started_at: str, turn_completed_at: str) -> ResponseMeta:
    tool_names: list[str] = []
    delegated_agents: list[str] = []
    delegation_ids: set[str] = set()
    round_count = 0
    tool_call_count = 0

    for item in process_items:
        kind = str(item.get("kind", "")).strip()
        if kind == "round_start":
            round_count += 1
        if kind == "tool_call":
            tool_call_count += 1
            tool_name = str(item.get("tool_name", "")).strip()
            if tool_name and tool_name not in tool_names:
                tool_names.append(tool_name)
        if kind == "start" and str(item.get("agent_kind", "primary")).strip() == "subagent":
            delegated_agent = str(item.get("agent", "")).strip()
            if delegated_agent and delegated_agent not in delegated_agents:
                delegated_agents.append(delegated_agent)
        delegation_id = str(item.get("delegation_id", "")).strip()
        if delegation_id:
            delegation_ids.add(delegation_id)

    return {
        "round_count": round_count,
        "tool_call_count": tool_call_count,
        "tool_names": tool_names,
        "delegation_count": len(delegation_ids),
        "delegated_agents": delegated_agents,
        "duration_ms": _compute_duration_ms(turn_started_at, turn_completed_at),
    }


def _attach_response_summary(
    message: Message,
    *,
    process_items: list[ProcessItem],
    display_parts: list[DisplayPart],
    turn_started_at: str,
    turn_completed_at: str,
) -> ResponseMeta:
    response_meta = _build_response_meta(process_items, turn_started_at=turn_started_at, turn_completed_at=turn_completed_at)
    message["info"]["process_items"] = [dict(item) for item in process_items]
    message["info"]["display_parts"] = _merge_display_parts_with_message(display_parts, message)
    message["info"]["response_meta"] = dict(response_meta)
    return response_meta


def configure_session_memory_store(store: SessionMemoryStore) -> None:
    """配置会话记忆存储实现，便于替换为 Redis 等后端。"""
    global SESSION_MEMORY_STORE
    SESSION_MEMORY_STORE = store


def clear_session_memory(session_id: str | None = None) -> None:
    SESSION_MEMORY_STORE.clear(session_id)
    _clear_pending_mode_switch(session_id)


def apply_mode_switch_action(session_id: str, action: ModeSwitchAction) -> Message:
    normalized_session_id = (session_id or "").strip()
    pending = get_pending_mode_switch(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待确认的模式切换。")

    action_name = (action or "").strip().lower()
    if action_name not in {"confirm", "cancel"}:
        raise ValueError("不支持的模式切换动作。")

    if action_name == "confirm":
        synthetic_input = _build_mode_switch_confirm_input(pending)
        target_agent = str(pending.get("target_agent", "build")).strip().lower()
        _clear_pending_mode_switch(normalized_session_id)
        return run_session(
            synthetic_input,
            session_id=normalized_session_id,
            mode=target_agent,  # type: ignore[arg-type]
        )

    history_messages = SESSION_MEMORY_STORE.load(normalized_session_id)
    turn_started_at = utc_now_iso()
    current_agent = str(pending.get("current_agent", "build")).strip().lower() or "build"

    user_message = _build_text_message(
        "user",
        f"取消切换到 {str(pending.get('target_agent', '')).strip() or '目标'} 模式。",
        normalized_session_id,
        text_meta={
            "agent": current_agent,
            "synthetic": True,
            "mode_switch_action": "cancel",
        },
    )
    assistant_message = create_message(
        role="assistant",
        session_id=normalized_session_id,
        status="interrupted",
        finish_reason="cancelled",
    )
    append_text_part(assistant_message, _build_mode_switch_cancel_text(pending))
    _set_message_runtime_info(
        assistant_message,
        agent=current_agent,
        model=_latest_model(history_messages),
        provider=_latest_provider(history_messages),
        turn_started_at=turn_started_at,
        turn_completed_at=utc_now_iso(),
    )
    _attach_response_summary(
        assistant_message,
        process_items=[],
        display_parts=_build_display_parts_from_message(assistant_message),
        turn_started_at=turn_started_at,
        turn_completed_at=str(assistant_message["info"].get("turn_completed_at", "")),
    )
    _clear_pending_mode_switch(normalized_session_id)
    SESSION_MEMORY_STORE.save(normalized_session_id, [*history_messages, user_message, assistant_message])
    return assistant_message


def run_mode_switch_stream_events(
    session_id: str,
    action: ModeSwitchAction,
) -> Generator[dict[str, Any], None, None]:
    """由程序控制模式切换确认，并以流式事件形式继续后续会话。"""
    normalized_session_id = (session_id or "").strip()
    pending = get_pending_mode_switch(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待确认的模式切换。")

    action_name = (action or "").strip().lower()
    if action_name not in {"confirm", "cancel"}:
        raise ValueError("不支持的模式切换动作。")

    if action_name == "cancel":
        message = apply_mode_switch_action(normalized_session_id, "cancel")
        yield {
            "type": "done",
            "event_id": _new_stream_event_id(),
            "timestamp": utc_now_iso(),
            "session_id": normalized_session_id,
            "agent": str(message["info"].get("agent", "build")),
            "agent_kind": _resolve_agent_kind(str(message["info"].get("agent", "build"))),
            "depth": 0,
            "message_id": str(message["info"].get("message_id", "")),
            "status": str(message["info"].get("status", "interrupted")),
            "finish_reason": str(message["info"].get("finish_reason", "cancelled")),
            "turn_started_at": str(message["info"].get("turn_started_at", "")),
            "turn_completed_at": str(message["info"].get("turn_completed_at", "")),
            "response_meta": message["info"].get("response_meta", {}),
            "process_items": message["info"].get("process_items", []),
            "display_parts": message["info"].get("display_parts", []),
            "confirmation": message["info"].get("confirmation"),
            "provider": str(message["info"].get("provider", "")),
            "model": str(message["info"].get("model", "")),
        }
        return

    target_agent = str(pending.get("target_agent", "build")).strip().lower()
    synthetic_input = _build_mode_switch_confirm_input(pending)
    _clear_pending_mode_switch(normalized_session_id)
    yield from run_session_stream_events(
        synthetic_input,
        session_id=normalized_session_id,
        mode=target_agent,  # type: ignore[arg-type]
    )


def subagent_loop(
    prompt: str,
    agent: str = "explore",
    session_id: str | None = None,
    *,
    llm_config: ResolvedLLMConfig | None = None,
) -> str:
    agent_name = (agent or "explore").strip().lower()
    agent_definition = get_agent(agent_name)
    if agent_definition is None:
        return f"Error: Unknown subagent '{agent_name}'. 当前仅支持 explore。"
    if agent_definition.model != "subagent":
        return f"Error: Agent '{agent_name}' 不是 subagent，不能通过 task 调用。"

    result = run_session(
        user_input=prompt,
        session_id=session_id,
        tools=build_base_tools(registry.list_briefs()),
        system_prompt=build_system_prompt(
            agent=agent_name,
            model=(llm_config.model if llm_config else ""),
            provider=(llm_config.provider if llm_config else ""),
            vendor=(llm_config.vendor if llm_config else ""),
        ),
        runtime_agent=agent_name,
        todo_tool_names={"todo_write", "todo_read"},
        llm_config=llm_config,
    )
    return get_message_text(result)


def _run_session_stream(
    user_input: str,
    session_id: str | None = None,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
    depth: int = 0,
    delegation_id: str | None = None,
    parent_tool_call_id: str | None = None,
    process_items: list[ProcessItem] | None = None,
    display_parts: list[DisplayPart] | None = None,
) -> Generator[dict[str, Any], None, Message]:
    """内部流式会话入口：支持递归转发 subagent 事件，并返回最终助手消息。"""
    active_session_id = set_session_id(session_id)
    turn_started_at = utc_now_iso()
    mode_enabled = tools is None and system_prompt is None

    initial_mode: MainAgentMode = "build"
    if mode in {"build", "plan"}:
        initial_mode = mode

    history_messages: list[Message] = SESSION_MEMORY_STORE.load(active_session_id) if mode_enabled else []
    if mode is None and mode_enabled and history_messages:
        initial_mode = _resolve_mode_from_messages(history_messages, fallback=initial_mode)

    if mode_enabled:
        initial_runtime, initial_provider_explicit = _resolve_runtime_config(
            history_messages,
            mode=initial_mode,
            provider=provider,
            provider_specified=provider_specified,
        )
    else:
        initial_runtime = llm_config or resolve_llm_config("build")
        initial_provider_explicit = False
    initial_tools = (
        _get_tools_for_mode(initial_mode)
        if mode_enabled
        else (tools if tools is not None else build_agent_tools("build", registry.list_briefs()))
    )
    initial_system_prompt = (
        _get_system_prompt_for_mode(
            initial_mode,
            model=initial_runtime.model,
            provider=initial_runtime.provider,
            vendor=initial_runtime.vendor,
        )
        if mode_enabled
        else (
            system_prompt
            or build_system_prompt(
                agent=runtime_agent or "build",
                model=(llm_config.model if llm_config else ""),
                provider=(llm_config.provider if llm_config else ""),
                vendor=(llm_config.vendor if llm_config else ""),
            )
        )
    )

    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])
    active_process_items = process_items if process_items is not None else []
    active_display_parts = display_parts if display_parts is not None else []
    display_text_merge_open = False

    user_meta: dict[str, Any] | None = None
    if mode_enabled:
        user_meta = {
            "agent": initial_mode,
            "provider": initial_runtime.provider,
            "provider_explicit": initial_provider_explicit,
            "provider_reset_to_default": provider_specified and not (provider or "").strip(),
            "model": initial_runtime.model,
        }

    messages: list[Message] = [
        _build_text_message("system", initial_system_prompt, active_session_id),
        *history_messages,
        _build_text_message(
            "user",
            user_input,
            active_session_id,
            model=initial_runtime.model if mode_enabled else (llm_config.model if llm_config else ""),
            provider=initial_runtime.provider if mode_enabled else (llm_config.provider if llm_config else ""),
            text_meta=user_meta,
        ),
    ]

    current_mode: MainAgentMode = initial_mode
    current_runtime = initial_runtime if mode_enabled else (llm_config or resolve_llm_config("build"))
    current_provider_explicit = initial_provider_explicit
    initial_agent = initial_mode if mode_enabled else (runtime_agent or "build")
    agent_kind = _resolve_agent_kind(initial_agent)

    def _emit_event(event_type: str, **payload: Any) -> dict[str, Any]:
        nonlocal display_text_merge_open
        event = _build_stream_event(
            event_type,
            session_id=active_session_id,
            agent=payload.pop("agent", initial_agent),
            agent_kind=payload.pop("agent_kind", agent_kind),
            depth=payload.pop("depth", depth),
            delegation_id=payload.pop("delegation_id", delegation_id),
            parent_tool_call_id=payload.pop("parent_tool_call_id", parent_tool_call_id),
            **payload,
        )
        process_item = _build_process_item(event)
        if process_item is not None:
            active_process_items.append(process_item)
        _append_display_event_part(active_display_parts, event=event)
        display_text_merge_open = False
        return event

    tool_executor = ToolExecutor(
        _build_tool_handlers(
            session_id=active_session_id,
            get_mode=lambda: current_mode,
            get_latest_model=lambda: _latest_model(messages),
            get_current_runtime=lambda: current_runtime,
            get_provider_explicit=lambda: current_provider_explicit,
        )
    )

    yield _emit_event(
        "start",
        agent=initial_agent,
        agent_kind=agent_kind,
        depth=depth,
        delegation_id=delegation_id,
        parent_tool_call_id=parent_tool_call_id,
        mode=initial_mode,
        provider=current_runtime.provider,
        model=current_runtime.model,
        started_at=turn_started_at,
    )

    round_no = 0
    while True:
        round_no += 1
        pre_compact_agent = current_mode if mode_enabled else (runtime_agent or "build")
        messages = compact(messages, llm_config=current_runtime, agent=pre_compact_agent)

        selected_tools = initial_tools
        if mode_enabled:
            current_mode = _resolve_mode_from_messages(messages, fallback=initial_mode)
            current_runtime, current_provider_explicit = _resolve_runtime_config(
                messages,
                mode=current_mode,
                provider=provider,
                provider_specified=False,
            )
            selected_tools = _get_tools_for_mode(current_mode)
            messages = _ensure_system_prompt(
                messages,
                _get_system_prompt_for_mode(
                    current_mode,
                    model=current_runtime.model,
                    provider=current_runtime.provider,
                    vendor=current_runtime.vendor,
                ),
                active_session_id,
            )
        active_agent = current_mode if mode_enabled else (runtime_agent or "build")
        agent_kind = _resolve_agent_kind(active_agent)

        yield _emit_event(
            "round_start",
            agent=active_agent,
            agent_kind=agent_kind,
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            round=round_no,
            provider=current_runtime.provider,
            model=current_runtime.model,
            started_at=utc_now_iso(),
        )

        stream_iter = _call_chat_completion_stream(
            messages=messages,
            tools=selected_tools,
            llm_config=current_runtime,
            agent=active_agent,
        )
        while True:
            try:
                stream_event = next(stream_iter)
            except StopIteration as stop:
                assistant_message = stop.value
                break

            if stream_event.get("type") == "text_delta":
                delta = str(stream_event.get("delta", ""))
                if delta:
                    delta_event = _build_stream_event(
                        "text_delta",
                        session_id=active_session_id,
                        agent=active_agent,
                        agent_kind=agent_kind,
                        depth=depth,
                        delegation_id=delegation_id,
                        parent_tool_call_id=parent_tool_call_id,
                        round=round_no,
                        delta=delta,
                    )
                    _append_display_text_part(
                        active_display_parts,
                        delta=delta,
                        created_at=str(delta_event.get("timestamp", "")) or utc_now_iso(),
                        agent=active_agent,
                        agent_kind=agent_kind,
                        depth=depth,
                        round_no=round_no,
                        delegation_id=delegation_id,
                        parent_tool_call_id=parent_tool_call_id,
                        merge_allowed=display_text_merge_open,
                    )
                    display_text_merge_open = True
                    yield delta_event

        _set_message_runtime_info(
            assistant_message,
            agent=active_agent,
            model=current_runtime.model,
            provider=current_runtime.provider,
            turn_started_at=turn_started_at,
        )
        messages.append(assistant_message)

        tool_calls = extract_tool_calls(assistant_message)
        has_tool_calls = bool(tool_calls)

        if has_tool_calls:
            has_todo_call = any(tc["name"] in todo_names for tc in tool_calls)

            for tool_call in tool_calls:
                yield _emit_event(
                    "tool_call",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    round=round_no,
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                    arguments=tool_call["arguments"],
                )

        if not has_tool_calls:
            completed_at = utc_now_iso()
            assistant_message["info"]["turn_completed_at"] = completed_at
            yield _emit_event(
                "round_end",
                agent=active_agent,
                agent_kind=agent_kind,
                depth=depth,
                delegation_id=delegation_id,
                parent_tool_call_id=parent_tool_call_id,
                round=round_no,
                status=assistant_message["info"].get("status", "completed"),
                finish_reason=assistant_message["info"].get("finish_reason", "stop"),
                provider=current_runtime.provider,
                model=current_runtime.model,
                completed_at=completed_at,
            )
            response_meta = _attach_response_summary(
                assistant_message,
                process_items=active_process_items,
                display_parts=active_display_parts,
                turn_started_at=turn_started_at,
                turn_completed_at=completed_at,
            )
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            yield _emit_event(
                "done",
                agent=active_agent,
                agent_kind=agent_kind,
                depth=depth,
                delegation_id=delegation_id,
                parent_tool_call_id=parent_tool_call_id,
                message_id=str(assistant_message["info"].get("message_id", "")),
                status=assistant_message["info"].get("status", "completed"),
                finish_reason=assistant_message["info"].get("finish_reason", "stop"),
                provider=current_runtime.provider,
                model=current_runtime.model,
                completed_at=completed_at,
                turn_started_at=turn_started_at,
                turn_completed_at=completed_at,
                response_meta=response_meta,
                process_items=[dict(item) for item in active_process_items],
                display_parts=assistant_message["info"].get("display_parts", []),
                confirmation=assistant_message["info"].get("confirmation"),
            )
            return assistant_message

        should_interrupt = False
        task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
        for tool_call in tool_calls:
            if tool_call["name"] == "task":
                delegation_instance_id = _new_delegation_id()
                task_result: ToolResult = {
                    "output": "",
                    "metadata": {
                        "status": "completed",
                        "delegation_id": delegation_instance_id,
                    },
                }
                try:
                    parsed_args = json.loads(tool_call["arguments"])
                    if not isinstance(parsed_args, dict):
                        raise ValueError("Task arguments must be a JSON object")
                    delegated_prompt = str(parsed_args.get("prompt", ""))
                    delegated_agent = str(parsed_args.get("agent", "explore"))
                    delegated_agent_definition = get_agent(delegated_agent.strip().lower())
                    if delegated_agent_definition is None:
                        task_result["output"] = f"Error: Unknown subagent '{delegated_agent.strip().lower()}'. 当前仅支持 explore。"
                        task_result["metadata"]["status"] = "failed"
                    elif delegated_agent_definition.model != "subagent":
                        task_result["output"] = (
                            f"Error: Agent '{delegated_agent_definition.name}' 不是 subagent，不能通过 task 调用。"
                        )
                        task_result["metadata"]["status"] = "failed"
                    else:
                        delegated_agent = delegated_agent_definition.name
                except Exception as exc:
                    task_result["output"] = f"Error: Invalid tool arguments: {type(exc).__name__}: {exc}"
                    task_result["metadata"]["status"] = "failed"

                task_status = str(task_result["metadata"].get("status", "completed")).strip().lower()
                if task_status == "completed":
                    delegated_message = yield from _run_session_stream(
                        delegated_prompt,
                        session_id=active_session_id,
                        tools=build_base_tools(registry.list_briefs()),
                        system_prompt=build_system_prompt(
                            agent=delegated_agent,
                            model=current_runtime.model,
                            provider=current_runtime.provider,
                            vendor=current_runtime.vendor,
                        ),
                        runtime_agent=delegated_agent,
                        todo_tool_names={"todo_write", "todo_read"},
                        llm_config=current_runtime,
                        depth=depth + 1,
                        delegation_id=delegation_instance_id,
                        parent_tool_call_id=tool_call["id"],
                        process_items=active_process_items,
                        display_parts=active_display_parts,
                    )
                    task_result["output"] = get_message_text(delegated_message)
                result = task_result
            else:
                result = tool_executor.execute(
                    tool_call["name"],
                    tool_call["arguments"],
                    session_id=active_session_id,
                    tool_call_id=tool_call["id"],
                    round_no=round_no,
                    hooks=effective_tool_hooks,
                    agent=active_agent,
                    model=current_runtime.model,
                    task_available=task_available,
                    workdir=str(_get_workdir()),
                )

            messages.append(
                _build_tool_message(
                    active_session_id,
                    tool_call_id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["arguments"],
                    result=result,
                    agent=active_agent,
                    turn_started_at=turn_started_at,
                )
            )
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            yield _emit_event(
                "tool_result",
                agent=active_agent,
                agent_kind=agent_kind,
                depth=depth,
                delegation_id=str(metadata.get("delegation_id", delegation_id or "")).strip() or delegation_id,
                parent_tool_call_id=parent_tool_call_id,
                round=round_no,
                tool_call_id=tool_call["id"],
                name=tool_call["name"],
                status=str(metadata.get("status", "completed")),
                output_preview=_tool_result_preview(result),
            )

            if tool_call["name"] not in {"plan_enter", "plan_exit"}:
                continue

            status = str(metadata.get("status", "")).strip().lower()

            if status == "confirmation_required":
                output_text = str(result.get("output", "请确认后继续。"))
                question = str(metadata.get("confirmation_question", "是否继续？"))
                metadata = {
                    **metadata,
                    "tool_name": tool_call["name"],
                    "current_agent": str(metadata.get("current_agent", active_agent)).strip() or active_agent,
                }
                _save_pending_mode_switch(active_session_id, metadata)
                interrupted_message = _build_confirmation_interrupted_message(
                    active_session_id,
                    tool_name=tool_call["name"],
                    output_text=output_text,
                    question=question,
                    metadata=metadata,
                )
                _set_message_runtime_info(
                    interrupted_message,
                    agent=active_agent,
                    model=current_runtime.model,
                    provider=current_runtime.provider,
                    turn_started_at=turn_started_at,
                    turn_completed_at=utc_now_iso(),
                )
                completed_at = str(interrupted_message["info"].get("turn_completed_at", ""))
                messages.append(interrupted_message)
                yield _emit_event(
                    "round_end",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    round=round_no,
                    status=interrupted_message["info"].get("status", "interrupted"),
                    finish_reason=interrupted_message["info"].get("finish_reason", "confirmation_required"),
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=completed_at,
                )
                response_meta = _attach_response_summary(
                    interrupted_message,
                    process_items=active_process_items,
                    display_parts=active_display_parts,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                )
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
                yield _emit_event(
                    "done",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    message_id=str(interrupted_message["info"].get("message_id", "")),
                    status=interrupted_message["info"].get("status", "interrupted"),
                    finish_reason=interrupted_message["info"].get("finish_reason", "confirmation_required"),
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=completed_at,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                    response_meta=response_meta,
                    process_items=[dict(item) for item in active_process_items],
                    display_parts=interrupted_message["info"].get("display_parts", []),
                    confirmation=interrupted_message["info"].get("confirmation"),
                )
                return interrupted_message

            if status == "switched":
                synthetic_agent = str(metadata.get("synthetic_agent", "")).strip().lower()
                synthetic_text = str(metadata.get("synthetic_user_message", "")).strip()
                plan_path = str(metadata.get("plan_path", "")).strip()

                if synthetic_agent in {"build", "plan"} and synthetic_text:
                    synthetic_runtime, synthetic_provider_explicit = _resolve_runtime_config(
                        messages,
                        mode=synthetic_agent,  # type: ignore[arg-type]
                        provider=current_runtime.provider if current_provider_explicit else None,
                        provider_specified=current_provider_explicit,
                    )
                    _append_synthetic_user_message(
                        messages,
                        session_id=active_session_id,
                        agent=synthetic_agent,  # type: ignore[arg-type]
                        text=synthetic_text,
                        plan_path=plan_path,
                        provider=synthetic_runtime.provider,
                        provider_explicit=synthetic_provider_explicit,
                        model=synthetic_runtime.model,
                    )

            if status == "cancelled":
                should_interrupt = True

        if should_interrupt:
            message = create_message(
                role="assistant",
                session_id=active_session_id,
                status="interrupted",
                finish_reason="cancelled",
            )
            append_text_part(message, "用户取消了模式切换，当前流程已中断。")
            _set_message_runtime_info(
                message,
                agent=active_agent,
                model=current_runtime.model,
                provider=current_runtime.provider,
                turn_started_at=turn_started_at,
                turn_completed_at=utc_now_iso(),
            )
            completed_at = str(message["info"].get("turn_completed_at", ""))
            messages.append(message)
            yield _emit_event(
                "round_end",
                agent=active_agent,
                agent_kind=agent_kind,
                depth=depth,
                delegation_id=delegation_id,
                parent_tool_call_id=parent_tool_call_id,
                round=round_no,
                status="interrupted",
                finish_reason="cancelled",
                provider=current_runtime.provider,
                model=current_runtime.model,
                completed_at=completed_at,
            )
            response_meta = _attach_response_summary(
                message,
                process_items=active_process_items,
                display_parts=active_display_parts,
                turn_started_at=turn_started_at,
                turn_completed_at=completed_at,
            )
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            yield _emit_event(
                "done",
                agent=active_agent,
                agent_kind=agent_kind,
                depth=depth,
                delegation_id=delegation_id,
                parent_tool_call_id=parent_tool_call_id,
                message_id=str(message["info"].get("message_id", "")),
                status="interrupted",
                finish_reason="cancelled",
                provider=current_runtime.provider,
                model=current_runtime.model,
                completed_at=completed_at,
                turn_started_at=turn_started_at,
                turn_completed_at=completed_at,
                response_meta=response_meta,
                process_items=[dict(item) for item in active_process_items],
                display_parts=message["info"].get("display_parts", []),
                confirmation=message["info"].get("confirmation"),
            )
            return message

        yield _emit_event(
            "round_end",
            agent=active_agent,
            agent_kind=agent_kind,
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            round=round_no,
            status="completed",
            finish_reason="tool_call",
            provider=current_runtime.provider,
            model=current_runtime.model,
            completed_at=utc_now_iso(),
        )


def _build_tool_handlers(
    *,
    session_id: str,
    get_mode: Callable[[], MainAgentMode],
    get_latest_model: Callable[[], str],
    get_current_runtime: Callable[[], ResolvedLLMConfig],
    get_provider_explicit: Callable[[], bool],
) -> dict[str, Callable[..., object]]:
    def _run_mode_aware_bash(command: str) -> str:
        if get_mode() == "plan":
            validation_error = validate_readonly_bash(command)
            if validation_error is not None:
                return validation_error
        return run_bash(command)

    def _run_mode_aware_write(path: str, content: str) -> str:
        if get_mode() == "plan" and not is_allowed_plan_write_path(path):
            return "Error: plan 模式下仅允许写入 src/plan 目录。"
        return run_write(path, content)

    def _run_mode_aware_edit(path: str, old_text: str, new_text: str) -> str:
        if get_mode() == "plan" and not is_allowed_plan_write_path(path):
            return "Error: plan 模式下仅允许编辑 src/plan 目录。"
        return run_edit(path, old_text, new_text)

    def _run_plan_enter_tool(**kw: Any) -> dict[str, Any]:
        plan_path = str(build_plan_placeholder_path(session_id))
        return run_plan_enter(
            current_mode=get_mode(),
            plan_path=plan_path,
            plan_exists=Path(plan_path).exists(),
            latest_model=get_latest_model(),
        )

    def _run_plan_exit_tool(**kw: Any) -> dict[str, Any]:
        plan_path = str(build_plan_placeholder_path(session_id))
        return run_plan_exit(
            current_mode=get_mode(),
            plan_path=plan_path,
            plan_exists=Path(plan_path).exists(),
            latest_model=get_latest_model(),
        )

    return {
        "bash": lambda **kw: _run_mode_aware_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit"), kw.get("offset", 0)),
        "write_file": lambda **kw: _run_mode_aware_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: _run_mode_aware_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "webfetch": lambda **kw: webfetch(kw),
        "websearch": lambda **kw: websearch(kw),
        "todo_write": lambda **kw: TODO.update(kw["todo_list"]),
        "todo_read": lambda **kw: TODO.read_current_session(),
        "task": lambda **kw: subagent_loop(
            kw["prompt"],
            agent=kw.get("agent", "explore"),
            session_id=session_id,
            llm_config=get_current_runtime(),
        ),
        "plan_enter": lambda **kw: _run_plan_enter_tool(**kw),
        "plan_exit": lambda **kw: _run_plan_exit_tool(**kw),
        "load_skill": lambda **kw: registry.build_skill_context(kw["skill_names"]),
    }


def run_session(
    user_input: str,
    session_id: str | None = None,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
) -> Message:
    """新会话入口：返回最终助手 Message（含结构化 parts）。"""
    active_session_id = set_session_id(session_id)
    turn_started_at = utc_now_iso()
    mode_enabled = tools is None and system_prompt is None

    initial_mode: MainAgentMode = "build"
    if mode in {"build", "plan"}:
        initial_mode = mode

    history_messages: list[Message] = SESSION_MEMORY_STORE.load(active_session_id) if mode_enabled else []
    if mode is None and mode_enabled and history_messages:
        initial_mode = _resolve_mode_from_messages(history_messages, fallback=initial_mode)

    if mode_enabled:
        initial_runtime, initial_provider_explicit = _resolve_runtime_config(
            history_messages,
            mode=initial_mode,
            provider=provider,
            provider_specified=provider_specified,
        )
    else:
        initial_runtime = llm_config or resolve_llm_config("build")
        initial_provider_explicit = False
    initial_tools = (
        _get_tools_for_mode(initial_mode)
        if mode_enabled
        else (tools if tools is not None else build_agent_tools("build", registry.list_briefs()))
    )
    initial_system_prompt = (
        _get_system_prompt_for_mode(
            initial_mode,
            model=initial_runtime.model,
            provider=initial_runtime.provider,
            vendor=initial_runtime.vendor,
        )
        if mode_enabled
        else (
            system_prompt
            or build_system_prompt(
                agent=runtime_agent or "build",
                model=(llm_config.model if llm_config else ""),
                provider=(llm_config.provider if llm_config else ""),
                vendor=(llm_config.vendor if llm_config else ""),
            )
        )
    )

    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])

    user_meta: dict[str, Any] | None = None
    if mode_enabled:
        user_meta = {
            "agent": initial_mode,
            "provider": initial_runtime.provider,
            "provider_explicit": initial_provider_explicit,
            "provider_reset_to_default": provider_specified and not (provider or "").strip(),
            "model": initial_runtime.model,
        }

    messages: list[Message] = [
        _build_text_message("system", initial_system_prompt, active_session_id),
        *history_messages,
        _build_text_message(
            "user",
            user_input,
            active_session_id,
            model=initial_runtime.model if mode_enabled else (llm_config.model if llm_config else ""),
            provider=initial_runtime.provider if mode_enabled else (llm_config.provider if llm_config else ""),
            text_meta=user_meta,
        ),
    ]

    current_mode: MainAgentMode = initial_mode
    current_runtime = initial_runtime if mode_enabled else (llm_config or resolve_llm_config("build"))
    current_provider_explicit = initial_provider_explicit

    tool_executor = ToolExecutor(
        _build_tool_handlers(
            session_id=active_session_id,
            get_mode=lambda: current_mode,
            get_latest_model=lambda: _latest_model(messages),
            get_current_runtime=lambda: current_runtime,
            get_provider_explicit=lambda: current_provider_explicit,
        )
    )

    round_no = 0
    while True:
        round_no += 1
        pre_compact_agent = current_mode if mode_enabled else (runtime_agent or "build")
        messages = compact(messages, llm_config=current_runtime, agent=pre_compact_agent)

        selected_tools = initial_tools
        if mode_enabled:
            current_mode = _resolve_mode_from_messages(messages, fallback=initial_mode)
            current_runtime, current_provider_explicit = _resolve_runtime_config(
                messages,
                mode=current_mode,
                provider=provider,
                provider_specified=False,
            )
            selected_tools = _get_tools_for_mode(current_mode)
            messages = _ensure_system_prompt(
                messages,
                _get_system_prompt_for_mode(
                    current_mode,
                    model=current_runtime.model,
                    provider=current_runtime.provider,
                    vendor=current_runtime.vendor,
                ),
                active_session_id,
            )
        active_agent = current_mode if mode_enabled else (runtime_agent or "build")

        assistant_message = _call_chat_completion(
            messages=messages,
            tools=selected_tools,
            llm_config=current_runtime,
            agent=active_agent,
        )
        _set_message_runtime_info(
            assistant_message,
            agent=active_agent,
            model=current_runtime.model,
            provider=current_runtime.provider,
            turn_started_at=turn_started_at,
        )
        messages.append(assistant_message)

        tool_calls = extract_tool_calls(assistant_message)
        has_tool_calls = bool(tool_calls)

        if has_tool_calls:
            has_todo_call = any(tc["name"] in todo_names for tc in tool_calls)

        if not has_tool_calls:
            assistant_message["info"]["turn_completed_at"] = utc_now_iso()
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return assistant_message

        should_interrupt = False
        task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
        for tool_call in tool_calls:
            if tool_call["name"] == "task":
                task_result: ToolResult = {
                    "output": "",
                    "metadata": {
                        "status": "completed",
                    },
                }
                delegated_agent = "explore"
                try:
                    parsed_args = json.loads(tool_call["arguments"])
                    if not isinstance(parsed_args, dict):
                        raise ValueError("Task arguments must be a JSON object")
                    delegated_prompt = str(parsed_args.get("prompt", ""))
                    delegated_agent = str(parsed_args.get("agent", "explore"))
                    delegated_agent_definition = get_agent(delegated_agent.strip().lower())
                    if delegated_agent_definition is None:
                        task_result["output"] = f"Error: Unknown subagent '{delegated_agent.strip().lower()}'. 当前仅支持 explore。"
                        task_result["metadata"]["status"] = "failed"
                    elif delegated_agent_definition.model != "subagent":
                        task_result["output"] = (
                            f"Error: Agent '{delegated_agent_definition.name}' 不是 subagent，不能通过 task 调用。"
                        )
                        task_result["metadata"]["status"] = "failed"
                    else:
                        delegated_agent = delegated_agent_definition.name
                except Exception as exc:
                    task_result["output"] = f"Error: Invalid tool arguments: {type(exc).__name__}: {exc}"
                    task_result["metadata"]["status"] = "failed"

                if str(task_result["metadata"].get("status", "completed")).strip().lower() == "completed":
                    task_result["output"] = subagent_loop(
                        delegated_prompt,
                        agent=delegated_agent,
                        session_id=active_session_id,
                        llm_config=current_runtime,
                    )
                result = task_result
            else:
                result = tool_executor.execute(
                    tool_call["name"],
                    tool_call["arguments"],
                    session_id=active_session_id,
                    tool_call_id=tool_call["id"],
                    round_no=round_no,
                    hooks=effective_tool_hooks,
                    agent=active_agent,
                    model=current_runtime.model,
                    task_available=task_available,
                    workdir=str(_get_workdir()),
                )
            messages.append(
                _build_tool_message(
                    active_session_id,
                    tool_call_id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["arguments"],
                    result=result,
                    agent=active_agent,
                    turn_started_at=turn_started_at,
                )
            )

            if tool_call["name"] not in {"plan_enter", "plan_exit"}:
                continue

            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            status = str(metadata.get("status", "")).strip().lower()

            if status == "confirmation_required":
                output_text = str(result.get("output", "请确认后继续。"))
                question = str(metadata.get("confirmation_question", "是否继续？"))
                metadata = {
                    **metadata,
                    "tool_name": tool_call["name"],
                    "current_agent": str(metadata.get("current_agent", active_agent)).strip() or active_agent,
                }
                _save_pending_mode_switch(active_session_id, metadata)
                interrupted_message = _build_confirmation_interrupted_message(
                    active_session_id,
                    tool_name=tool_call["name"],
                    output_text=output_text,
                    question=question,
                    metadata=metadata,
                )
                _set_message_runtime_info(
                    interrupted_message,
                    agent=active_agent,
                    model=current_runtime.model,
                    provider=current_runtime.provider,
                    turn_started_at=turn_started_at,
                    turn_completed_at=utc_now_iso(),
                )
                messages.append(interrupted_message)
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
                return interrupted_message

            if status == "switched":
                synthetic_agent = str(metadata.get("synthetic_agent", "")).strip().lower()
                synthetic_text = str(metadata.get("synthetic_user_message", "")).strip()
                plan_path = str(metadata.get("plan_path", "")).strip()

                if synthetic_agent in {"build", "plan"} and synthetic_text:
                    synthetic_runtime, synthetic_provider_explicit = _resolve_runtime_config(
                        messages,
                        mode=synthetic_agent,  # type: ignore[arg-type]
                        provider=current_runtime.provider if current_provider_explicit else None,
                        provider_specified=current_provider_explicit,
                    )
                    _append_synthetic_user_message(
                        messages,
                        session_id=active_session_id,
                        agent=synthetic_agent,  # type: ignore[arg-type]
                        text=synthetic_text,
                        plan_path=plan_path,
                        provider=synthetic_runtime.provider,
                        provider_explicit=synthetic_provider_explicit,
                        model=synthetic_runtime.model,
                    )

            if status == "cancelled":
                should_interrupt = True

        if should_interrupt:
            message = create_message(
                role="assistant",
                session_id=active_session_id,
                status="interrupted",
                finish_reason="cancelled",
            )
            append_text_part(message, "用户取消了模式切换，当前流程已中断。")
            _set_message_runtime_info(
                message,
                agent=active_agent,
                model=current_runtime.model,
                provider=current_runtime.provider,
                turn_started_at=turn_started_at,
                turn_completed_at=utc_now_iso(),
            )
            messages.append(message)
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return message


def run_session_stream_events(
    user_input: str,
    session_id: str | None = None,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """流式会话入口：逐步产出轮次/文本/工具事件。"""
    yield from _run_session_stream(
        user_input,
        session_id=session_id,
        mode=mode,
        provider=provider,
        provider_specified=provider_specified,
        tools=tools,
        system_prompt=system_prompt,
        todo_tool_names=todo_tool_names,
        tool_hooks=tool_hooks,
        llm_config=llm_config,
        runtime_agent=runtime_agent,
    )


def agent_loop(user_input: str, session_id: str | None = None) -> Message:
    """兼容入口：内部转发到新接口。"""
    return run_session(user_input=user_input, session_id=session_id)
