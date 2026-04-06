import logging
import re
import subprocess
import uuid
import json
import inspect
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Generator
from typing import Any, Callable, Literal, TypedDict

from ..adapters.llm.client import create_chat_completion, create_chat_completion_stream
from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..mcp.runtime import describe_mcp_runtime_alerts_for_mode, execute_mcp_tool, list_mcp_tools
from ..slash_commands import ResolvedSlashCommand, resolve_slash_command
from ..config.settings import (
    ResolvedLLMConfig,
    resolve_agent_loop_settings,
    resolve_llm_config,
    resolve_session_memory_settings,
    resolve_subagent_loop_settings,
)
from ..core.context import set_session_id
from ..core.message import (
    DisplayPart,
    Message,
    ProcessItem,
    ResponseMeta,
    append_tool_part,
    append_text_part,
    create_error_message,
    create_message,
    extract_tool_calls,
    get_role,
    get_message_text,
    utc_now_iso,
)
from ..runtime.agents import get_agent
from ..tools.bash_tool import _normalize_timeout, resolve_bash_workdir, run_bash, validate_readonly_bash
from ..tools.edit_file_tool import run_edit
from ..tools.grep_tool import run_grep
from ..tools.glob_tool import run_glob
from ..skills.runtime import SkillRegistry
from ..tools.handlers import (
    build_plan_placeholder_path,
    build_tool_failure,
    build_tool_success,
    is_allowed_plan_write_path,
    run_plan_enter,
    run_plan_exit,
)
from ..tools.lsp_tool import run_lsp
from ..tools.question_tool import run_question
from ..tools.read_file_tool import run_read
from ..tools.skill_tool import run_load_skill
from ..tools.specs import build_agent_tools, build_base_tools
from ..tools.todo_manager import TodoManager
from ..tools.webfetch import webfetch
from ..tools.websearch import websearch
from ..tools.write_file_tool import run_write
from .compaction import compact
from .session_memory import FileSessionMemoryStore, InMemorySessionMemoryStore, SessionMemoryStore, normalize_history_prefix
from .stream_display import (
    _append_display_event_part,
    _append_display_reasoning_part,
    _append_display_text_part,
    _attach_response_summary,
    _build_display_parts_from_message,
    _build_process_item,
    _build_stream_event,
    _merge_display_parts_with_message,
    _new_stream_event_id,
    _resolve_agent_kind,
)
from .tool_executor import ToolExecutor, ToolHook, ToolResult, get_global_tool_hooks
from .workspace import get_workspace

logger = logging.getLogger(__name__)

MainAgentMode = Literal["build", "plan"]

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

TODO = TodoManager()
def _build_default_session_memory_store() -> SessionMemoryStore:
    settings = resolve_session_memory_settings()
    return FileSessionMemoryStore(
        max_messages=settings.max_messages,
        trim_enabled=settings.trim_enabled,
    )


SESSION_MEMORY_STORE: SessionMemoryStore = _build_default_session_memory_store()
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


class PendingQuestionOption(TypedDict):
    label: str
    description: str


class PendingQuestionItem(TypedDict):
    question: str
    header: str
    options: list[PendingQuestionOption]
    multiple: bool
    custom: bool


class PendingQuestion(TypedDict):
    request_id: str
    tool_name: str
    title: str
    questions: list[PendingQuestionItem]
    agent_name: str
    agent_kind: str
    resume_mode: str
    resume_runtime_agent: str
    provider: str
    vendor: str
    model: str
    delegation_id: str
    parent_tool_call_id: str
    requested_at: str


class QuestionAnswerPayload(TypedDict):
    answers: list[str]
    notes: str


@dataclass(frozen=True)
class SessionBootstrap:
    session_id: str
    turn_started_at: str
    mode_enabled: bool
    initial_mode: MainAgentMode
    history_messages: list[Message]
    initial_runtime: ResolvedLLMConfig
    initial_provider_explicit: bool
    initial_model_explicit: bool
    initial_tools: list[dict[str, Any]]
    initial_system_prompt: str
    user_meta: dict[str, Any] | None
    messages: list[Message]
    current_mode: MainAgentMode
    current_runtime: ResolvedLLMConfig
    current_provider_explicit: bool
    current_model_explicit: bool
    initial_agent: str


@dataclass(frozen=True)
class TaskToolRequest:
    prompt: str
    agent: str
    result: ToolResult

    @property
    def should_execute(self) -> bool:
        return str(self.result.get("metadata", {}).get("status", "completed")).strip().lower() == "completed"


@dataclass(frozen=True)
class PreparedSessionInput:
    user_input: str
    mode: MainAgentMode | None
    display_input: str
    slash_command: ResolvedSlashCommand | None = None
    immediate_output: str | None = None


PENDING_MODE_SWITCHES: dict[str, PendingModeSwitch] = {}
PENDING_QUESTIONS: dict[str, PendingQuestion] = {}
STOP_REQUESTED_SESSION_IDS: set[str] = set()
_SKILL_REGISTRY: SkillRegistry | None = None
_SKILL_REGISTRY_ROOT: Path | None = None

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
]
_CONTINUE_FINISH_REASONS = {"tool-calls", "unknown"}
_TERMINAL_FINISH_REASONS = {"stop", "length", "content-filter", "error"}


def _normalize_prompt_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower()).strip("._-")
    return normalized or "default"


def _assistant_finish_reason(message: Message) -> str:
    raw_reason = str(message["info"].get("finish_reason", "")).strip().lower().replace("_", "-")
    if raw_reason:
        return raw_reason
    if extract_tool_calls(message):
        return "tool-calls"
    if get_message_text(message):
        return "stop"
    return "unknown"


def _prepare_session_input(
    *,
    user_input: str,
    mode: MainAgentMode | None,
    tools: list[dict[str, Any]] | None,
    system_prompt: str | None,
) -> PreparedSessionInput:
    if tools is not None or system_prompt is not None:
        return PreparedSessionInput(user_input=user_input, mode=mode, display_input=user_input)

    resolved_command = resolve_slash_command(user_input)
    if resolved_command is None:
        return PreparedSessionInput(user_input=user_input, mode=mode, display_input=user_input)

    override_mode = resolved_command.override_mode
    normalized_mode = mode
    if override_mode in {"build", "plan"}:
        normalized_mode = override_mode
    return PreparedSessionInput(
        user_input=resolved_command.user_input,
        mode=normalized_mode,
        display_input=resolved_command.display_text or user_input,
        slash_command=resolved_command,
        immediate_output=resolved_command.immediate_output,
    )


def _should_continue_after_assistant(message: Message) -> bool:
    return _assistant_finish_reason(message) in _CONTINUE_FINISH_REASONS


def _build_max_rounds_exceeded_message(
    *,
    session_id: str,
    active_agent: str,
    current_runtime: ResolvedLLMConfig,
    turn_started_at: str,
    max_rounds: int,
) -> Message:
    message = create_error_message(
        session_id=session_id,
        model=current_runtime.model,
        provider=current_runtime.provider,
        error={
            "code": "loop_round_limit_exceeded",
            "message": f"当前流程已达到最大轮次限制（{max_rounds}），已停止继续推理。",
            "details": "LoopRoundLimitExceeded",
        },
    )
    _set_message_runtime_info(
        message,
        agent=active_agent,
        model=current_runtime.model,
        provider=current_runtime.provider,
        turn_started_at=turn_started_at,
        turn_completed_at=utc_now_iso(),
    )
    return message


def _get_workdir() -> Path:
    return get_workspace().root


def normalize_required_session_id(session_id: str | None) -> str:
    normalized = (session_id or "").strip()
    if not normalized:
        raise ValueError("session_id 不能为空")
    return normalized


def generate_session_id(prefix: str = "session") -> str:
    normalized_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", (prefix or "").strip()).strip("_")
    safe_prefix = normalized_prefix or "session"
    return f"{safe_prefix}_{uuid.uuid4().hex[:12]}"


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


def _get_skill_registry() -> SkillRegistry:
    global _SKILL_REGISTRY, _SKILL_REGISTRY_ROOT
    skills_root = get_workspace().skills_dir.resolve()
    if _SKILL_REGISTRY is not None and _SKILL_REGISTRY_ROOT == skills_root:
        return _SKILL_REGISTRY

    registry = SkillRegistry(skills_root)
    try:
        registry.discover()
    except FileNotFoundError:
        registry.skills = []
    _SKILL_REGISTRY = registry
    _SKILL_REGISTRY_ROOT = skills_root
    return registry


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"未找到 prompt 文件: {path}")
    return path.read_text(encoding="utf-8").strip()


def _read_global_agent_appendix() -> str:
    agent_md_path = Path.home() / ".my-agent" / "AGENTS.md"
    if not agent_md_path.exists():
        return ""
    try:
        content = agent_md_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("prompt.global_agent_md.read_failed path=%s error=%s", agent_md_path, exc)
        return ""
    if not content:
        return ""
    return f"以下是全局 ~/.my-agent/AGENTS.md 内容，请一并遵守：\n\n{content}"


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
def _apply_prompt_context(base_prompt: str, *, agent: str, session_id: str) -> str:
    if agent.strip().lower() != "plan":
        return base_prompt
    plan_path = str(build_plan_placeholder_path(session_id))
    return base_prompt.replace("{plan_path}", plan_path)


def _call_build_system_prompt(
    *,
    agent: str,
    model: str,
    provider: str,
    vendor: str,
    session_id: str,
) -> str:
    prompt_kwargs = {
        "agent": agent,
        "model": model,
        "provider": provider,
        "vendor": vendor,
    }
    if "session_id" in inspect.signature(build_system_prompt).parameters:
        prompt_kwargs["session_id"] = session_id
    return build_system_prompt(**prompt_kwargs)


def build_system_prompt(
    *,
    agent: str,
    model: str,
    provider: str,
    vendor: str,
    session_id: str,
) -> str:
    base_prompt = _read_prompt_file(_resolve_prompt_path(agent, vendor))
    rendered_prompt = _apply_prompt_context(base_prompt, agent=agent, session_id=session_id)
    parts = [
        rendered_prompt,
        _read_global_agent_appendix(),
        _read_local_agent_appendix(),
        _build_environment_appendix(agent=agent, model=model, provider=provider, vendor=vendor),
    ]
    return "\n\n".join(part for part in parts if part)


def _iter_mcp_runtime_alert_events(
    *,
    session_id: str,
    active_agent: str,
    depth: int,
    mode: MainAgentMode | None,
) -> Generator[dict[str, Any], None, None]:
    if depth != 0:
        return

    normalized_mode = "plan" if (mode or "").strip().lower() == "plan" else "build"
    alerts = describe_mcp_runtime_alerts_for_mode(normalized_mode)
    if not alerts:
        return

    for alert in alerts:
        logger.warning(
            "mcp.runtime_alert_emitted mode=%s server=%s code=%s message=%s",
            normalized_mode,
            alert.server_alias,
            alert.code,
            alert.message,
        )
        yield _build_stream_event(
            "runtime_alert",
            session_id=session_id,
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            scope="mcp",
            severity="error",
            code=alert.code,
            message=f"MCP server `{alert.server_alias}` 当前不可用：{alert.message}",
            server_alias=alert.server_alias,
            mode=normalized_mode,
        )


def _get_system_prompt_for_mode(
    mode: MainAgentMode,
    *,
    model: str,
    provider: str,
    vendor: str,
    session_id: str,
) -> str:
    return _call_build_system_prompt(
        agent=mode,
        model=model,
        provider=provider,
        vendor=vendor,
        session_id=session_id,
    )


def _get_tools_for_mode(mode: MainAgentMode) -> list[dict]:
    base_tools = build_agent_tools(mode, _get_skill_registry().list_briefs())
    mcp_tools, _ = list_mcp_tools(mode)
    return [*base_tools, *mcp_tools]


def _get_base_tools_for_agent(mode: str = "build") -> list[dict[str, Any]]:
    base_tools = build_base_tools(_get_skill_registry().list_briefs())
    mcp_tools, _ = list_mcp_tools(mode)
    return [*base_tools, *mcp_tools]


def _ensure_system_prompt(messages: list[Message], prompt: str, session_id: str) -> list[Message]:
    system_msg = _build_text_message("system", prompt, session_id)
    if messages and get_role(messages[0]) == "system":
        messages[0] = system_msg
        return messages
    return [system_msg, *messages]


def _extract_tool_result_call_id(message: Message) -> str:
    for part in message.get("parts", []):
        if part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        tool_call_id = str(state.get("tool_call_id", "")).strip()
        if tool_call_id:
            return tool_call_id
    return ""


def _build_recovered_tool_message(
    *,
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    assistant_message: Message,
) -> Message:
    recovered_message = create_message(role="tool", session_id=session_id)
    recovery_text = (
        "系统恢复提示：该工具调用在上一轮请求异常结束前未完成，未执行任何实际工具逻辑。"
        "当前结果为系统在会话恢复阶段自动补齐的失败占位结果，请基于当前上下文重新判断是否需要再次调用该工具。"
    )
    append_tool_part(
        recovered_message,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="failed",
        output={
            "output": recovery_text,
            "metadata": {
                "status": "failed",
                "error_code": "interrupted_tool_call_recovered",
                "recovered": True,
                "synthetic": True,
                "recovery_reason": "request_interrupted_before_tool_result",
            },
        },
    )
    _set_message_runtime_info(
        recovered_message,
        agent=str(assistant_message["info"].get("agent", "")).strip(),
        model=str(assistant_message["info"].get("model", "")).strip() or None,
        provider=str(assistant_message["info"].get("provider", "")).strip() or None,
        turn_started_at=(
            str(assistant_message["info"].get("turn_started_at", "")).strip()
            or str(assistant_message["info"].get("created_at", "")).strip()
            or utc_now_iso()
        ),
        turn_completed_at=utc_now_iso(),
    )
    return recovered_message


def _build_missing_tool_result_message(
    *,
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    assistant_message: Message,
) -> Message:
    recovered_message = create_message(role="tool", session_id=session_id)
    recovery_text = (
        "系统恢复提示：该工具调用的原始 tool result 已缺失，当前无法确认工具是否真正执行、执行到哪一步、"
        "以及当时的具体输出内容。当前结果为恢复阶段自动补齐的未知结果占位，请基于当前上下文重新判断是否需要再次调用该工具。"
    )
    append_tool_part(
        recovered_message,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="failed",
        output={
            "output": recovery_text,
            "metadata": {
                "status": "failed",
                "error_code": "missing_tool_result_context",
                "recovered": True,
                "synthetic": True,
                "recovery_reason": "tool_result_or_context_missing",
            },
        },
    )
    _set_message_runtime_info(
        recovered_message,
        agent=str(assistant_message["info"].get("agent", "")).strip(),
        model=str(assistant_message["info"].get("model", "")).strip() or None,
        provider=str(assistant_message["info"].get("provider", "")).strip() or None,
        turn_started_at=(
            str(assistant_message["info"].get("turn_started_at", "")).strip()
            or str(assistant_message["info"].get("created_at", "")).strip()
            or utc_now_iso()
        ),
        turn_completed_at=utc_now_iso(),
    )
    return recovered_message


def _build_orphan_tool_assistant_message(*, tool_message: Message, tool_call_id: str, tool_name: str) -> Message:
    session_id = str(tool_message["info"].get("session_id", "")).strip()
    assistant_message = create_message("assistant", session_id, status="completed", finish_reason="tool_calls")
    append_text_part(
        assistant_message,
        "系统恢复提示：发现一条缺少请求上下文的工具结果，以下为系统为保持会话可继续而补齐的工具调用锚点，请将后续工具结果仅视为参考。",
    )
    arguments = "{}"
    for part in tool_message.get("parts", []):
        if part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        input_data = state.get("input")
        if isinstance(input_data, dict):
            arguments = str(input_data.get("arguments", "{}") or "{}")
            break
    append_tool_part(
        assistant_message,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="requested",
        arguments=arguments,
    )
    _set_message_runtime_info(
        assistant_message,
        agent=str(tool_message["info"].get("agent", "")).strip(),
        model=str(tool_message["info"].get("model", "")).strip() or None,
        provider=str(tool_message["info"].get("provider", "")).strip() or None,
        turn_started_at=(
            str(tool_message["info"].get("turn_started_at", "")).strip()
            or str(tool_message["info"].get("created_at", "")).strip()
            or utc_now_iso()
        ),
        turn_completed_at=utc_now_iso(),
    )
    return assistant_message


def _extract_tool_message_meta(message: Message) -> tuple[str, str]:
    tool_call_id = ""
    tool_name = ""
    for part in message.get("parts", []):
        if part.get("type") != "tool":
            continue
        tool_name = str(part.get("name", "")).strip()
        state = part.get("state")
        if not isinstance(state, dict):
            continue
        tool_call_id = str(state.get("tool_call_id", "")).strip()
        if tool_call_id or tool_name:
            break
    return tool_call_id, tool_name


def _insert_missing_tool_results_for_pending(
    *,
    repaired_messages: list[Message],
    insert_at: int,
    pending_tool_calls: list[dict[str, str]],
    current_assistant_index: int,
) -> tuple[int, list[str]]:
    if not pending_tool_calls or current_assistant_index < 0:
        return insert_at, []

    assistant_message = repaired_messages[current_assistant_index]
    missing_tool_messages = [
        _build_missing_tool_result_message(
            session_id=str(assistant_message["info"].get("session_id", "")).strip(),
            tool_call_id=tool_call["id"],
            tool_name=tool_call["name"],
            assistant_message=assistant_message,
        )
        for tool_call in pending_tool_calls
    ]
    repaired_messages[insert_at:insert_at] = missing_tool_messages
    return insert_at + len(missing_tool_messages), [tool_call["id"] for tool_call in pending_tool_calls]


def _recover_incomplete_tool_calls(history_messages: list[Message]) -> tuple[list[Message], bool]:
    if not history_messages:
        return history_messages, False

    repaired_messages = list(normalize_history_prefix(history_messages))
    pending_tool_calls: list[dict[str, str]] = []
    current_assistant_index = -1
    repaired = repaired_messages != history_messages
    inserted_call_ids: list[str] = []
    recovered_call_ids: list[str] = []
    index = 0

    while index < len(repaired_messages):
        message = repaired_messages[index]
        role = get_role(message)

        if role == "assistant":
            if pending_tool_calls:
                index, recovered_ids = _insert_missing_tool_results_for_pending(
                    repaired_messages=repaired_messages,
                    insert_at=index,
                    pending_tool_calls=pending_tool_calls,
                    current_assistant_index=current_assistant_index,
                )
                recovered_call_ids.extend(recovered_ids)
                repaired = True
            pending_tool_calls = extract_tool_calls(message)
            current_assistant_index = index if pending_tool_calls else -1
            index += 1
            continue

        if role == "tool":
            tool_call_id = _extract_tool_result_call_id(message)
            if pending_tool_calls:
                matched_index = next(
                    (offset for offset, tool_call in enumerate(pending_tool_calls) if tool_call["id"] == tool_call_id),
                    -1,
                )
                if matched_index >= 0:
                    pending_tool_calls.pop(matched_index)
                    index += 1
                    continue

            orphan_tool_call_id, orphan_tool_name = _extract_tool_message_meta(message)
            if orphan_tool_call_id and orphan_tool_name:
                if pending_tool_calls:
                    # 先补齐前一个 assistant 尚未完成的 tool result，再修当前孤儿 tool，
                    # 避免新的 synthetic assistant 覆盖掉已有 pending 状态。
                    index, recovered_ids = _insert_missing_tool_results_for_pending(
                        repaired_messages=repaired_messages,
                        insert_at=index,
                        pending_tool_calls=pending_tool_calls,
                        current_assistant_index=current_assistant_index,
                    )
                    recovered_call_ids.extend(recovered_ids)
                    pending_tool_calls = []
                    current_assistant_index = -1
                    repaired = True
                synthetic_assistant = _build_orphan_tool_assistant_message(
                    tool_message=message,
                    tool_call_id=orphan_tool_call_id,
                    tool_name=orphan_tool_name,
                )
                repaired_messages.insert(index, synthetic_assistant)
                inserted_call_ids.append(orphan_tool_call_id)
                pending_tool_calls = extract_tool_calls(synthetic_assistant)
                current_assistant_index = index
                repaired = True
                index += 1
                continue

            index += 1
            continue

        if pending_tool_calls:
            index, recovered_ids = _insert_missing_tool_results_for_pending(
                repaired_messages=repaired_messages,
                insert_at=index,
                pending_tool_calls=pending_tool_calls,
                current_assistant_index=current_assistant_index,
            )
            recovered_call_ids.extend(recovered_ids)
            repaired = True
            pending_tool_calls = []
            current_assistant_index = -1
            continue

        index += 1

    if pending_tool_calls and current_assistant_index >= 0:
        _, recovered_ids = _insert_missing_tool_results_for_pending(
            repaired_messages=repaired_messages,
            insert_at=len(repaired_messages),
            pending_tool_calls=pending_tool_calls,
            current_assistant_index=current_assistant_index,
        )
        recovered_call_ids.extend(recovered_ids)
        repaired = True

    if inserted_call_ids:
        logger.info(
            "session.repair_orphan_tool_messages session_id=%s repaired_call_ids=%s",
            str(repaired_messages[0]["info"].get("session_id", "")).strip(),
            ",".join(inserted_call_ids),
            extra=build_log_extra(
                agent=str(repaired_messages[0]["info"].get("agent", "")).strip(),
                model=str(repaired_messages[0]["info"].get("model", "")).strip(),
            ),
        )
    if recovered_call_ids:
        assistant_message = repaired_messages[current_assistant_index] if 0 <= current_assistant_index < len(repaired_messages) else repaired_messages[0]
        logger.info(
            "session.recover_incomplete_tool_calls session_id=%s assistant_message_id=%s recovered_call_ids=%s",
            str(assistant_message["info"].get("session_id", "")).strip(),
            str(assistant_message["info"].get("message_id", "")).strip(),
            ",".join(recovered_call_ids),
            extra=build_log_extra(
                agent=str(assistant_message["info"].get("agent", "")).strip(),
                model=str(assistant_message["info"].get("model", "")).strip(),
            ),
        )
    return repaired_messages, repaired


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


def _resolve_model_preference_from_messages(messages: list[Message]) -> str | None:
    for meta in _iter_user_text_meta(messages):
        if bool(meta.get("provider_reset_to_default")) or bool(meta.get("model_reset_to_default")):
            return ""
        model = str(meta.get("model", "")).strip()
        if model and bool(meta.get("model_explicit")):
            return model
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
    model: str | None = None,
    model_specified: bool = False,
) -> tuple[ResolvedLLMConfig, bool, bool]:
    normalized_provider = (provider or "").strip()
    normalized_model = (model or "").strip()

    # 当本轮既没有显式 provider，也没有从历史继承 provider 偏好时，应优先回到 agent 默认配置。
    if not provider_specified and _resolve_provider_preference_from_messages(messages) is None:
        if normalized_model and model_specified:
            return resolve_llm_config(mode, model_name=normalized_model), False, True

        inherited_model = _resolve_model_preference_from_messages(messages)
        if inherited_model:
            return resolve_llm_config(mode, model_name=inherited_model), False, True

        return resolve_llm_config(mode), False, False

    provider_name, is_explicit = _resolve_provider_selection(
        messages,
        mode=mode,
        provider=provider,
        provider_specified=provider_specified,
    )

    if provider_specified and not normalized_provider:
        return resolve_llm_config(mode), False, False

    if normalized_model and model_specified:
        return resolve_llm_config(mode, provider_name, normalized_model), is_explicit, True

    if provider_specified:
        return resolve_llm_config(mode, provider_name), is_explicit, False

    inherited_model = _resolve_model_preference_from_messages(messages)
    if inherited_model:
        return resolve_llm_config(mode, provider_name, inherited_model), is_explicit, True

    return resolve_llm_config(mode, provider_name), is_explicit, False


def _build_user_message_meta(
    *,
    mode_enabled: bool,
    initial_mode: MainAgentMode,
    initial_runtime: ResolvedLLMConfig,
    initial_provider_explicit: bool,
    initial_model_explicit: bool,
    provider: str | None,
    provider_specified: bool,
    model: str | None,
    model_specified: bool,
) -> dict[str, Any] | None:
    if not mode_enabled:
        return None
    return {
        "agent": initial_mode,
        "provider": initial_runtime.provider,
        "provider_explicit": initial_provider_explicit,
        "provider_reset_to_default": provider_specified and not (provider or "").strip(),
        "model": initial_runtime.model,
        "model_explicit": initial_model_explicit,
        "model_reset_to_default": (
            (provider_specified and not (provider or "").strip())
            or (model_specified and not (model or "").strip())
        ),
    }


def _build_session_messages(
    *,
    session_id: str,
    history_messages: list[Message],
    initial_system_prompt: str,
    user_input: str,
    display_input: str | None,
    initial_runtime: ResolvedLLMConfig,
    llm_config: ResolvedLLMConfig | None,
    mode_enabled: bool,
    user_meta: dict[str, Any] | None,
) -> list[Message]:
    effective_user_meta = dict(user_meta or {})
    if display_input and display_input != user_input:
        effective_user_meta["display_text"] = display_input
    return [
        _build_text_message("system", initial_system_prompt, session_id),
        *history_messages,
        _build_text_message(
            "user",
            user_input,
            session_id,
            model=initial_runtime.model if mode_enabled else (llm_config.model if llm_config else ""),
            provider=initial_runtime.provider if mode_enabled else (llm_config.provider if llm_config else ""),
            text_meta=effective_user_meta or None,
        ),
    ]


def _bootstrap_session(
    user_input: str,
    session_id: str,
    *,
    display_input: str | None = None,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    model: str | None = None,
    model_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
) -> SessionBootstrap:
    active_session_id = set_session_id(normalize_required_session_id(session_id))
    turn_started_at = utc_now_iso()
    mode_enabled = tools is None and system_prompt is None
    registry = _get_skill_registry()

    initial_mode: MainAgentMode = "build"
    if mode in {"build", "plan"}:
        initial_mode = mode

    history_messages: list[Message] = SESSION_MEMORY_STORE.load(active_session_id) if mode_enabled else []
    if mode_enabled and history_messages:
        history_messages, history_repaired = _recover_incomplete_tool_calls(history_messages)
        if history_repaired:
            SESSION_MEMORY_STORE.save(active_session_id, history_messages)
    if mode is None and mode_enabled and history_messages:
        initial_mode = _resolve_mode_from_messages(history_messages, fallback=initial_mode)

    if mode_enabled:
        initial_runtime, initial_provider_explicit, initial_model_explicit = _resolve_runtime_config(
            history_messages,
            mode=initial_mode,
            provider=provider,
            provider_specified=provider_specified,
            model=model,
            model_specified=model_specified,
        )
    else:
        initial_runtime = llm_config or resolve_llm_config("build")
        initial_provider_explicit = False
        initial_model_explicit = False

    initial_tools = (
        _get_tools_for_mode(initial_mode)
        if mode_enabled
        else (tools if tools is not None else _get_tools_for_mode("build"))
    )
    initial_system_prompt = (
        _get_system_prompt_for_mode(
            initial_mode,
            model=initial_runtime.model,
            provider=initial_runtime.provider,
            vendor=initial_runtime.vendor,
            session_id=active_session_id,
        )
        if mode_enabled
        else (
            system_prompt
            or _call_build_system_prompt(
                agent=runtime_agent or "build",
                model=(llm_config.model if llm_config else ""),
                provider=(llm_config.provider if llm_config else ""),
                vendor=(llm_config.vendor if llm_config else ""),
                session_id=active_session_id,
            )
        )
    )

    user_meta = _build_user_message_meta(
        mode_enabled=mode_enabled,
        initial_mode=initial_mode,
        initial_runtime=initial_runtime,
        initial_provider_explicit=initial_provider_explicit,
        initial_model_explicit=initial_model_explicit,
        provider=provider,
        provider_specified=provider_specified,
        model=model,
        model_specified=model_specified,
    )
    messages = _build_session_messages(
        session_id=active_session_id,
        history_messages=history_messages,
        initial_system_prompt=initial_system_prompt,
        user_input=user_input,
        display_input=display_input,
        initial_runtime=initial_runtime,
        llm_config=llm_config,
        mode_enabled=mode_enabled,
        user_meta=user_meta,
    )
    initial_agent = initial_mode if mode_enabled else (runtime_agent or "build")

    return SessionBootstrap(
        session_id=active_session_id,
        turn_started_at=turn_started_at,
        mode_enabled=mode_enabled,
        initial_mode=initial_mode,
        history_messages=history_messages,
        initial_runtime=initial_runtime,
        initial_provider_explicit=initial_provider_explicit,
        initial_model_explicit=initial_model_explicit,
        initial_tools=initial_tools,
        initial_system_prompt=initial_system_prompt,
        user_meta=user_meta,
        messages=messages,
        current_mode=initial_mode,
        current_runtime=initial_runtime if mode_enabled else (llm_config or resolve_llm_config("build")),
        current_provider_explicit=initial_provider_explicit,
        current_model_explicit=initial_model_explicit,
        initial_agent=initial_agent,
    )


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


def _build_question_interrupted_message(
    session_id: str,
    *,
    tool_name: str,
    request_id: str,
    title: str,
    questions: list[PendingQuestionItem],
    output_text: str,
) -> Message:
    message = create_message(
        role="assistant",
        session_id=session_id,
        status="interrupted",
        finish_reason="question_required",
    )
    append_text_part(
        message,
        f"{output_text}\n请先回答这些问题后再继续。".strip(),
        meta={
            "tool": tool_name,
            "question_required": True,
            "request_id": request_id,
        },
    )
    message["info"]["question"] = {
        "tool": tool_name,
        "request_id": request_id,
        "title": title,
        "questions": questions,
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


def _new_question_request_id() -> str:
    return f"question_{uuid.uuid4().hex[:12]}"


def _save_pending_question(session_id: str, metadata: dict[str, Any], *, active_agent: str) -> PendingQuestion:
    raw_questions = metadata.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("无效的问题列表")
    questions: list[PendingQuestionItem] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            raise ValueError("无效的问题项")
        raw_options = item.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            raise ValueError("问题项缺少选项")
        options: list[PendingQuestionOption] = []
        for option in raw_options:
            if not isinstance(option, dict):
                raise ValueError("无效的问题选项")
            options.append(
                PendingQuestionOption(
                    label=str(option.get("label", "")).strip(),
                    description=str(option.get("description", "")).strip(),
                )
            )
        questions.append(
            PendingQuestionItem(
                question=str(item.get("question", "")).strip(),
                header=str(item.get("header", "")).strip(),
                options=options,
                multiple=bool(item.get("multiple", False)),
                custom=bool(item.get("custom", True)),
            )
        )

    normalized_agent = str(metadata.get("agent_name", "")).strip().lower() or active_agent.strip().lower()
    agent_kind = _resolve_agent_kind(normalized_agent)
    pending = PendingQuestion(
        request_id=_new_question_request_id(),
        tool_name=str(metadata.get("tool_name", "")).strip() or "question",
        title=str(metadata.get("title", "")).strip() or f"等待用户回答 {len(questions)} 个问题",
        questions=questions,
        agent_name=normalized_agent,
        agent_kind=agent_kind,
        resume_mode=normalized_agent if normalized_agent in {"build", "plan"} else "",
        resume_runtime_agent=normalized_agent,
        provider=str(metadata.get("provider", "")).strip(),
        vendor=str(metadata.get("vendor", "")).strip(),
        model=str(metadata.get("model", "")).strip(),
        delegation_id=str(metadata.get("delegation_id", "")).strip(),
        parent_tool_call_id=str(metadata.get("parent_tool_call_id", "")).strip(),
        requested_at=utc_now_iso(),
    )
    PENDING_QUESTIONS[session_id] = pending
    return pending


def get_pending_question(session_id: str) -> PendingQuestion | None:
    return PENDING_QUESTIONS.get(session_id)


def _clear_pending_question(session_id: str | None = None) -> None:
    normalized = (session_id or "").strip()
    if not normalized:
        PENDING_QUESTIONS.clear()
        return
    PENDING_QUESTIONS.pop(normalized, None)


def _clear_pending_mode_switch(session_id: str | None = None) -> None:
    normalized = (session_id or "").strip()
    if not normalized:
        PENDING_MODE_SWITCHES.clear()
        return
    PENDING_MODE_SWITCHES.pop(normalized, None)


def request_session_stop(session_id: str) -> None:
    normalized = (session_id or "").strip()
    if normalized:
        STOP_REQUESTED_SESSION_IDS.add(normalized)


def is_session_stop_requested(session_id: str) -> bool:
    normalized = (session_id or "").strip()
    return bool(normalized) and normalized in STOP_REQUESTED_SESSION_IDS


def clear_session_stop(session_id: str | None = None) -> None:
    normalized = (session_id or "").strip()
    if not normalized:
        STOP_REQUESTED_SESSION_IDS.clear()
        return
    STOP_REQUESTED_SESSION_IDS.discard(normalized)


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


def _normalize_question_answers(pending: PendingQuestion, answers: list[QuestionAnswerPayload]) -> list[QuestionAnswerPayload]:
    questions = pending["questions"]
    if len(answers) != len(questions):
        raise ValueError("answers 数量与问题数量不一致。")

    normalized_answers: list[QuestionAnswerPayload] = []
    for index, question in enumerate(questions):
        raw_answer = answers[index]
        if not isinstance(raw_answer, dict):
            raise ValueError(f"第 {index + 1} 个问题的答案必须是对象。")
        raw_values = raw_answer.get("answers", [])
        if not isinstance(raw_values, list):
            raise ValueError(f"第 {index + 1} 个问题的 answers 必须是数组。")
        normalized = [str(item).strip() for item in raw_values if str(item).strip()]
        if not normalized:
            raise ValueError(f"第 {index + 1} 个问题至少需要一个答案；若用户拒绝回答，请调用 reject 接口。")
        if not question.get("multiple", False) and len(normalized) > 1:
            raise ValueError(f"第 {index + 1} 个问题是单选，不能提交多个答案。")
        normalized_answers.append(
            QuestionAnswerPayload(
                answers=normalized,
                notes=str(raw_answer.get("notes", "")).strip(),
            )
        )
    return normalized_answers


def _build_question_answer_summary(pending: PendingQuestion, answers: list[QuestionAnswerPayload]) -> str:
    lines = ["question 工具已收到用户回答："]
    for index, question in enumerate(pending["questions"]):
        answer_item = answers[index]
        answer_values = answer_item["answers"]
        answer_text = "、".join(answer_values)
        lines.append(f"- {question['header']}：{answer_text}")
        notes_text = answer_item.get("notes", "").strip()
        if notes_text:
            lines.append(f"  备注：{notes_text}")
    return "\n".join(lines)


def _build_question_rejected_input(pending: PendingQuestion) -> str:
    return (
        "question 工具未拿到答案：用户拒绝回答这些问题。"
        "这不是工具执行异常，而是用户明确拒绝提供补充信息。"
        "请基于现有上下文决定如何继续，必要时可以向用户解释影响。"
    )


def _resume_question_session(
    *,
    pending: PendingQuestion,
    session_id: str,
    user_input: str,
    stream: bool,
) -> Message | Generator[dict[str, Any], None, None]:
    agent_kind = str(pending.get("agent_kind", "")).strip().lower()
    if agent_kind == "subagent":
        registry = _get_skill_registry()
        provider_name = str(pending.get("provider", "")).strip()
        model_name = str(pending.get("model", "")).strip()
        runtime_agent = str(pending.get("resume_runtime_agent", "")).strip().lower() or "explore"
        if provider_name and model_name:
            llm_config = resolve_llm_config("build", provider_name, model_name)
        elif provider_name:
            llm_config = resolve_llm_config("build", provider_name)
        else:
            llm_config = resolve_llm_config("build")
        kwargs: dict[str, Any] = {
            "user_input": user_input,
            "session_id": session_id,
            "tools": _get_base_tools_for_agent("build"),
            "system_prompt": _call_build_system_prompt(
                agent=runtime_agent,
                model=llm_config.model,
                provider=llm_config.provider,
                vendor=llm_config.vendor,
                session_id=session_id,
            ),
            "runtime_agent": runtime_agent,
            "todo_tool_names": {"todo_write", "todo_read"},
            "llm_config": llm_config,
        }
        if stream:
            return run_session_stream_events(**kwargs)
        return run_session(**kwargs)

    resume_mode = str(pending.get("resume_mode", "")).strip().lower() or "build"
    if stream:
        return run_session_stream_events(
            user_input,
            session_id=session_id,
            mode=resume_mode,  # type: ignore[arg-type]
        )
    return run_session(
        user_input,
        session_id=session_id,
        mode=resume_mode,  # type: ignore[arg-type]
    )


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
    model_explicit: bool,
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
            "model_explicit": model_explicit,
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


def _build_slash_command_error_message(
    *,
    session_id: str,
    user_input: str,
    error_text: str,
    mode: MainAgentMode | None,
    turn_started_at: str,
) -> tuple[Message, Message]:
    active_mode: MainAgentMode = mode if mode in {"build", "plan"} else "build"
    runtime = resolve_llm_config(active_mode)
    user_message = _build_text_message(
        "user",
        user_input,
        session_id,
        model=runtime.model,
        provider=runtime.provider,
        text_meta={
            "agent": active_mode,
            "slash_command": True,
        },
    )
    assistant_message = create_message(
        role="assistant",
        session_id=session_id,
        status="completed",
        finish_reason="error",
    )
    append_text_part(
        assistant_message,
        error_text,
        meta={
            "slash_command_error": True,
        },
    )
    completed_at = utc_now_iso()
    _set_message_runtime_info(
        assistant_message,
        agent=active_mode,
        model=runtime.model,
        provider=runtime.provider,
        turn_started_at=turn_started_at,
        turn_completed_at=completed_at,
    )
    _attach_response_summary(
        assistant_message,
        process_items=[],
        display_parts=[],
        turn_started_at=turn_started_at,
        turn_completed_at=completed_at,
    )
    return user_message, assistant_message


def _build_slash_command_completed_message(
    *,
    session_id: str,
    user_input: str,
    display_input: str,
    output_text: str,
    mode: MainAgentMode | None,
    turn_started_at: str,
) -> tuple[Message, Message]:
    active_mode: MainAgentMode = mode if mode in {"build", "plan"} else "build"
    runtime = resolve_llm_config(active_mode)
    user_message = _build_text_message(
        "user",
        user_input,
        session_id,
        model=runtime.model,
        provider=runtime.provider,
        text_meta={
            "agent": active_mode,
            "slash_command": True,
            "display_text": display_input,
        },
    )
    assistant_message = create_message(
        role="assistant",
        session_id=session_id,
        status="completed",
        finish_reason="stop",
    )
    append_text_part(
        assistant_message,
        output_text,
        meta={
            "slash_command_completed": True,
        },
    )
    completed_at = utc_now_iso()
    _set_message_runtime_info(
        assistant_message,
        agent=active_mode,
        model=runtime.model,
        provider=runtime.provider,
        turn_started_at=turn_started_at,
        turn_completed_at=completed_at,
    )
    _attach_response_summary(
        assistant_message,
        process_items=[],
        display_parts=[],
        turn_started_at=turn_started_at,
        turn_completed_at=completed_at,
    )
    return user_message, assistant_message


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
    message_id = str(message["info"].get("message_id", ""))
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    attachments = result.get("attachments")
    if isinstance(attachments, list):
        normalized_attachments: list[dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            normalized_attachment = dict(attachment)
            normalized_attachment.setdefault("id", f"att_{uuid.uuid4().hex[:12]}")
            normalized_attachment.setdefault("sessionID", session_id)
            normalized_attachment.setdefault("messageID", message_id)
            filename = str(metadata.get("filename", "")).strip()
            if filename:
                normalized_attachment.setdefault("filename", filename)
            normalized_attachments.append(normalized_attachment)
        if normalized_attachments:
            result = dict(result)
            result["attachments"] = normalized_attachments
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


def _pending_question_result_from_message(message: Message) -> ToolResult:
    info = message.get("info", {})
    question_info = info.get("question") if isinstance(info.get("question"), dict) else {}
    metadata: dict[str, Any] = {
        "status": "question_required",
        "tool_name": "question",
        "title": str(question_info.get("title", "")).strip(),
        "questions": question_info.get("questions", []),
        "agent_name": str(info.get("agent", "")).strip(),
        "model": str(info.get("model", "")).strip(),
        "provider": str(info.get("provider", "")).strip(),
    }
    return {
        "output": get_message_text(message),
        "metadata": metadata,
    }


def _prepare_task_tool_request(arguments: str, *, delegation_id: str | None = None) -> TaskToolRequest:
    metadata: dict[str, Any] = {"status": "completed"}
    if delegation_id:
        metadata["delegation_id"] = delegation_id
    result: ToolResult = {
        "output": "",
        "metadata": metadata,
    }
    delegated_prompt = ""
    delegated_agent = "explore"

    try:
        parsed_args = json.loads(arguments)
        if not isinstance(parsed_args, dict):
            raise ValueError("Task arguments must be a JSON object")
        delegated_prompt = str(parsed_args.get("prompt", ""))
        delegated_agent = str(parsed_args.get("agent", "explore")).strip().lower()
        delegated_agent_definition = get_agent(delegated_agent)
        if delegated_agent_definition is None:
            result["output"] = f"Error: Unknown subagent '{delegated_agent}'. 当前仅支持 explore。"
            result["metadata"]["status"] = "failed"
        elif delegated_agent_definition.model != "subagent":
            result["output"] = f"Error: Agent '{delegated_agent_definition.name}' 不是 subagent，不能通过 task 调用。"
            result["metadata"]["status"] = "failed"
        else:
            delegated_agent = delegated_agent_definition.name
    except Exception as exc:
        result["output"] = f"Error: Invalid tool arguments: {type(exc).__name__}: {exc}"
        result["metadata"]["status"] = "failed"

    return TaskToolRequest(
        prompt=delegated_prompt,
        agent=delegated_agent,
        result=result,
    )


def _handle_mode_switch_tool_result(
    *,
    session_id: str,
    tool_name: str,
    result: ToolResult,
    messages: list[Message],
    active_agent: str,
    current_runtime: ResolvedLLMConfig,
    current_provider_explicit: bool,
    current_model_explicit: bool,
    turn_started_at: str,
) -> tuple[Message | None, bool]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    status = str(metadata.get("status", "")).strip().lower()

    if status == "confirmation_required":
        output_text = str(result.get("output", "请确认后继续。"))
        question = str(metadata.get("confirmation_question", "是否继续？"))
        normalized_metadata = {
            **metadata,
            "tool_name": tool_name,
            "current_agent": str(metadata.get("current_agent", active_agent)).strip() or active_agent,
        }
        _save_pending_mode_switch(session_id, normalized_metadata)
        interrupted_message = _build_confirmation_interrupted_message(
            session_id,
            tool_name=tool_name,
            output_text=output_text,
            question=question,
            metadata=normalized_metadata,
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
        return interrupted_message, False

    if status == "switched":
        synthetic_agent = str(metadata.get("synthetic_agent", "")).strip().lower()
        synthetic_text = str(metadata.get("synthetic_user_message", "")).strip()
        plan_path = str(metadata.get("plan_path", "")).strip()

        if synthetic_agent in {"build", "plan"} and synthetic_text:
            synthetic_runtime, synthetic_provider_explicit, synthetic_model_explicit = _resolve_runtime_config(
                messages,
                mode=synthetic_agent,  # type: ignore[arg-type]
                provider=current_runtime.provider if current_provider_explicit else None,
                provider_specified=current_provider_explicit,
                model=current_runtime.model if current_model_explicit else None,
                model_specified=current_model_explicit,
            )
            _append_synthetic_user_message(
                messages,
                session_id=session_id,
                agent=synthetic_agent,  # type: ignore[arg-type]
                text=synthetic_text,
                plan_path=plan_path,
                provider=synthetic_runtime.provider,
                provider_explicit=synthetic_provider_explicit,
                model=synthetic_runtime.model,
                model_explicit=synthetic_model_explicit,
            )

    return None, status == "cancelled"


def _handle_question_tool_result(
    *,
    session_id: str,
    tool_name: str,
    result: ToolResult,
    messages: list[Message],
    active_agent: str,
    current_runtime: ResolvedLLMConfig,
    turn_started_at: str,
    delegation_id: str | None = None,
    parent_tool_call_id: str | None = None,
) -> Message | None:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    status = str(metadata.get("status", "")).strip().lower()
    if status != "question_required":
        return None

    normalized_metadata = {
        **metadata,
        "tool_name": tool_name,
        "model": current_runtime.model,
        "provider": current_runtime.provider,
        "vendor": current_runtime.vendor,
        "delegation_id": delegation_id or "",
        "parent_tool_call_id": parent_tool_call_id or "",
    }
    pending = _save_pending_question(session_id, normalized_metadata, active_agent=active_agent)
    interrupted_message = _build_question_interrupted_message(
        session_id,
        tool_name=tool_name,
        request_id=pending["request_id"],
        title=pending["title"],
        questions=pending["questions"],
        output_text=str(result.get("output", "等待用户回答问题后再继续。")).strip() or "等待用户回答问题后再继续。",
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
    return interrupted_message


def _build_cancelled_mode_switch_message(
    *,
    session_id: str,
    active_agent: str,
    current_runtime: ResolvedLLMConfig,
    turn_started_at: str,
) -> Message:
    message = create_message(
        role="assistant",
        session_id=session_id,
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
    return message


def _build_stopped_session_message(
    *,
    session_id: str,
    active_agent: str,
    current_runtime: ResolvedLLMConfig,
    turn_started_at: str,
) -> Message:
    message = create_message(
        role="assistant",
        session_id=session_id,
        status="interrupted",
        finish_reason="cancelled",
    )
    # 用户主动停止后，统一落一条可追踪的助手消息，保证前端历史与事件流一致。
    append_text_part(message, "当前执行已手动停止。")
    _set_message_runtime_info(
        message,
        agent=active_agent,
        model=current_runtime.model,
        provider=current_runtime.provider,
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


def _new_delegation_id() -> str:
    return f"delegation_{uuid.uuid4().hex[:12]}"


def configure_session_memory_store(store: SessionMemoryStore) -> None:
    """配置会话记忆存储实现，便于替换为 Redis 等后端。"""
    global SESSION_MEMORY_STORE
    SESSION_MEMORY_STORE = store


def clear_session_memory(session_id: str | None = None) -> None:
    SESSION_MEMORY_STORE.clear(session_id)
    _clear_pending_mode_switch(session_id)
    _clear_pending_question(session_id)
    clear_session_stop(session_id)


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


def apply_question_answer(session_id: str, request_id: str, answers: list[QuestionAnswerPayload]) -> Message:
    normalized_session_id = (session_id or "").strip()
    normalized_request_id = (request_id or "").strip()
    pending = get_pending_question(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待回答的问题。")
    if pending["request_id"] != normalized_request_id:
        raise ValueError("问题请求已失效或不匹配。")

    normalized_answers = _normalize_question_answers(pending, answers)
    _clear_pending_question(normalized_session_id)
    return _resume_question_session(
        pending=pending,
        session_id=normalized_session_id,
        user_input=_build_question_answer_summary(pending, normalized_answers),
        stream=False,
    )


def run_question_answer_stream_events(
    session_id: str,
    request_id: str,
    answers: list[QuestionAnswerPayload],
) -> Generator[dict[str, Any], None, None]:
    normalized_session_id = (session_id or "").strip()
    normalized_request_id = (request_id or "").strip()
    pending = get_pending_question(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待回答的问题。")
    if pending["request_id"] != normalized_request_id:
        raise ValueError("问题请求已失效或不匹配。")

    normalized_answers = _normalize_question_answers(pending, answers)
    _clear_pending_question(normalized_session_id)
    resumed = _resume_question_session(
        pending=pending,
        session_id=normalized_session_id,
        user_input=_build_question_answer_summary(pending, normalized_answers),
        stream=True,
    )
    yield from resumed


def apply_question_reject(session_id: str, request_id: str) -> Message:
    normalized_session_id = (session_id or "").strip()
    normalized_request_id = (request_id or "").strip()
    pending = get_pending_question(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待回答的问题。")
    if pending["request_id"] != normalized_request_id:
        raise ValueError("问题请求已失效或不匹配。")

    _clear_pending_question(normalized_session_id)
    return _resume_question_session(
        pending=pending,
        session_id=normalized_session_id,
        user_input=_build_question_rejected_input(pending),
        stream=False,
    )


def run_question_reject_stream_events(
    session_id: str,
    request_id: str,
) -> Generator[dict[str, Any], None, None]:
    normalized_session_id = (session_id or "").strip()
    normalized_request_id = (request_id or "").strip()
    pending = get_pending_question(normalized_session_id)
    if pending is None:
        raise ValueError("当前没有待回答的问题。")
    if pending["request_id"] != normalized_request_id:
        raise ValueError("问题请求已失效或不匹配。")

    _clear_pending_question(normalized_session_id)
    resumed = _resume_question_session(
        pending=pending,
        session_id=normalized_session_id,
        user_input=_build_question_rejected_input(pending),
        stream=True,
    )
    yield from resumed


def subagent_loop(
    prompt: str,
    session_id: str,
    agent: str = "explore",
    *,
    llm_config: ResolvedLLMConfig | None = None,
) -> str:
    agent_name = (agent or "explore").strip().lower()
    agent_definition = get_agent(agent_name)
    if agent_definition is None:
        return f"Error: Unknown subagent '{agent_name}'. 当前仅支持 explore。"
    if agent_definition.model != "subagent":
        return f"Error: Agent '{agent_name}' 不是 subagent，不能通过 task 调用。"

    # 使用独立的 subagent loop 配置
    subagent_max_rounds = resolve_subagent_loop_settings().max_rounds

    result = run_session(
        user_input=prompt,
        session_id=session_id,
        tools=_get_base_tools_for_agent("build"),
        system_prompt=_call_build_system_prompt(
            agent=agent_name,
            model=(llm_config.model if llm_config else ""),
            provider=(llm_config.provider if llm_config else ""),
            vendor=(llm_config.vendor if llm_config else ""),
            session_id=session_id,
        ),
        runtime_agent=agent_name,
        todo_tool_names={"todo_write", "todo_read"},
        llm_config=llm_config,
        max_rounds=subagent_max_rounds,
    )
    return get_message_text(result)


def _run_session_stream(
    user_input: str,
    session_id: str,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    model: str | None = None,
    model_specified: bool = False,
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
    max_rounds: int | None = None,
) -> Generator[dict[str, Any], None, Message]:
    """内部流式会话入口：支持递归转发 subagent 事件，并返回最终助手消息。
    
    Args:
        max_rounds: 最大循环轮次，为 None 时使用默认配置（主 agent 使用 agent_loop.max_rounds）。
    """
    turn_started_at = utc_now_iso()
    try:
        prepared_input = _prepare_session_input(
            user_input=user_input,
            mode=mode,
            tools=tools,
            system_prompt=system_prompt,
        )
    except ValueError as exc:
        active_session_id = set_session_id(normalize_required_session_id(session_id))
        mode_enabled = tools is None and system_prompt is None
        user_message, assistant_message = _build_slash_command_error_message(
            session_id=active_session_id,
            user_input=user_input,
            error_text=str(exc),
            mode=mode,
            turn_started_at=turn_started_at,
        )
        if mode_enabled:
            history_messages = SESSION_MEMORY_STORE.load(active_session_id)
            SESSION_MEMORY_STORE.save(active_session_id, [*history_messages, user_message, assistant_message])
        active_agent = str(assistant_message["info"].get("agent", "build")).strip() or "build"
        runtime_provider = str(assistant_message["info"].get("provider", "")).strip()
        runtime_model = str(assistant_message["info"].get("model", "")).strip()
        yield _build_stream_event(
            "start",
            session_id=active_session_id,
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            mode=active_agent,
            provider=runtime_provider,
            model=runtime_model,
            started_at=turn_started_at,
        )
        yield _build_stream_event(
            "done",
            session_id=active_session_id,
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            message_id=str(assistant_message["info"].get("message_id", "")),
            status=str(assistant_message["info"].get("status", "completed")),
            finish_reason=str(assistant_message["info"].get("finish_reason", "error")),
            provider=runtime_provider,
            model=runtime_model,
            turn_started_at=turn_started_at,
            turn_completed_at=str(assistant_message["info"].get("turn_completed_at", "")),
            response_meta=dict(assistant_message["info"].get("response_meta", {})),
            process_items=list(assistant_message["info"].get("process_items", [])),
            display_parts=list(assistant_message["info"].get("display_parts", [])),
        )
        return assistant_message

    if prepared_input.immediate_output is not None:
        active_session_id = set_session_id(normalize_required_session_id(session_id))
        mode_enabled = tools is None and system_prompt is None
        user_message, assistant_message = _build_slash_command_completed_message(
            session_id=active_session_id,
            user_input=user_input,
            display_input=prepared_input.display_input,
            output_text=prepared_input.immediate_output,
            mode=prepared_input.mode,
            turn_started_at=turn_started_at,
        )
        if mode_enabled:
            history_messages = SESSION_MEMORY_STORE.load(active_session_id)
            SESSION_MEMORY_STORE.save(active_session_id, [*history_messages, user_message, assistant_message])
        active_agent = str(assistant_message["info"].get("agent", "build")).strip() or "build"
        runtime_provider = str(assistant_message["info"].get("provider", "")).strip()
        runtime_model = str(assistant_message["info"].get("model", "")).strip()
        yield _build_stream_event(
            "start",
            session_id=active_session_id,
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            mode=active_agent,
            provider=runtime_provider,
            model=runtime_model,
            started_at=turn_started_at,
        )
        yield _build_stream_event(
            "done",
            session_id=active_session_id,
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            message_id=str(assistant_message["info"].get("message_id", "")),
            status=str(assistant_message["info"].get("status", "completed")),
            finish_reason=str(assistant_message["info"].get("finish_reason", "stop")),
            provider=runtime_provider,
            model=runtime_model,
            turn_started_at=turn_started_at,
            turn_completed_at=str(assistant_message["info"].get("turn_completed_at", "")),
            response_meta=dict(assistant_message["info"].get("response_meta", {})),
            process_items=list(assistant_message["info"].get("process_items", [])),
            display_parts=list(assistant_message["info"].get("display_parts", [])),
        )
        return assistant_message

    bootstrap = _bootstrap_session(
        prepared_input.user_input,
        session_id=session_id,
        display_input=prepared_input.display_input,
        mode=prepared_input.mode,
        provider=provider,
        provider_specified=provider_specified,
        model=model,
        model_specified=model_specified,
        tools=tools,
        system_prompt=system_prompt,
        llm_config=llm_config,
        runtime_agent=runtime_agent,
    )
    active_session_id = bootstrap.session_id
    turn_started_at = bootstrap.turn_started_at
    mode_enabled = bootstrap.mode_enabled

    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])
    active_process_items = process_items if process_items is not None else []
    active_display_parts = display_parts if display_parts is not None else []
    display_text_merge_open = False
    display_reasoning_merge_open = False
    messages = list(bootstrap.messages)
    current_mode: MainAgentMode = bootstrap.current_mode
    current_runtime = bootstrap.current_runtime
    current_provider_explicit = bootstrap.current_provider_explicit
    current_model_explicit = bootstrap.current_model_explicit
    initial_agent = bootstrap.initial_agent
    agent_kind = _resolve_agent_kind(initial_agent)
    stop_message_saved = False

    def _emit_event(event_type: str, **payload: Any) -> dict[str, Any]:
        nonlocal display_reasoning_merge_open, display_text_merge_open
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
        display_reasoning_merge_open = False
        return event

    def _build_stop_result_message(active_agent: str, runtime_config: ResolvedLLMConfig) -> Message:
        nonlocal stop_message_saved
        message = _build_stopped_session_message(
            session_id=active_session_id,
            active_agent=active_agent,
            current_runtime=runtime_config,
            turn_started_at=turn_started_at,
        )
        completed_at = str(message["info"].get("turn_completed_at", ""))
        response_meta = _attach_response_summary(
            message,
            process_items=active_process_items,
            display_parts=active_display_parts,
            turn_started_at=turn_started_at,
            turn_completed_at=completed_at,
        )
        messages.append(message)
        if mode_enabled:
            SESSION_MEMORY_STORE.save(active_session_id, messages)
        stop_message_saved = True
        if depth == 0:
            clear_session_stop(active_session_id)
        yield _emit_event(
            "round_end",
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            round=round_no,
            status="interrupted",
            finish_reason="cancelled",
            provider=runtime_config.provider,
            model=runtime_config.model,
            completed_at=completed_at,
        )
        yield _emit_event(
            "done",
            agent=active_agent,
            agent_kind=_resolve_agent_kind(active_agent),
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            message_id=str(message["info"].get("message_id", "")),
            status="interrupted",
            finish_reason="cancelled",
            provider=runtime_config.provider,
            model=runtime_config.model,
            completed_at=completed_at,
            turn_started_at=turn_started_at,
            turn_completed_at=completed_at,
            response_meta=response_meta,
            process_items=[dict(item) for item in active_process_items],
            display_parts=message["info"].get("display_parts", []),
            confirmation=message["info"].get("confirmation"),
            question=message["info"].get("question"),
        )
        return message

    def _consume_stop_request_if_needed(active_agent: str, runtime_config: ResolvedLLMConfig) -> Generator[dict[str, Any], None, Message | None]:
        if not is_session_stop_requested(active_session_id):
            return None
        return (yield from _build_stop_result_message(active_agent, runtime_config))

    tool_executor = ToolExecutor(
        _build_tool_handlers(
            session_id=active_session_id,
            get_mode=lambda: current_mode,
            get_latest_model=lambda: _latest_model(messages),
            get_current_runtime=lambda: current_runtime,
        )
    )

    try:
        yield _emit_event(
            "start",
            agent=initial_agent,
            agent_kind=agent_kind,
            depth=depth,
            delegation_id=delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            mode=bootstrap.initial_mode,
            provider=current_runtime.provider,
            model=current_runtime.model,
            started_at=turn_started_at,
        )
        yield from _iter_mcp_runtime_alert_events(
            session_id=active_session_id,
            active_agent=initial_agent,
            depth=depth,
            mode=bootstrap.initial_mode,
        )

        effective_max_rounds = max_rounds if max_rounds is not None else resolve_agent_loop_settings().max_rounds
        round_no = 0
        while True:
            round_no += 1
            if round_no > effective_max_rounds:
                active_agent = current_mode if mode_enabled else (runtime_agent or "build")
                agent_kind = _resolve_agent_kind(active_agent)
                limit_message = _build_max_rounds_exceeded_message(
                    session_id=active_session_id,
                    active_agent=active_agent,
                    current_runtime=current_runtime,
                    turn_started_at=turn_started_at,
                    max_rounds=effective_max_rounds,
                )
                completed_at = str(limit_message["info"].get("turn_completed_at", ""))
                messages.append(limit_message)
                yield _emit_event(
                    "round_end",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    round=round_no,
                    status=limit_message["info"].get("status", "failed"),
                    finish_reason=limit_message["info"].get("finish_reason", "error"),
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=completed_at,
                )
                response_meta = _attach_response_summary(
                    limit_message,
                    process_items=active_process_items,
                    display_parts=active_display_parts,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                )
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
                if depth == 0:
                    clear_session_stop(active_session_id)
                yield _emit_event(
                    "done",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    message_id=str(limit_message["info"].get("message_id", "")),
                    status=limit_message["info"].get("status", "failed"),
                    finish_reason=limit_message["info"].get("finish_reason", "error"),
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=completed_at,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                    response_meta=response_meta,
                    process_items=[dict(item) for item in active_process_items],
                    display_parts=limit_message["info"].get("display_parts", []),
                    confirmation=limit_message["info"].get("confirmation"),
                    question=limit_message["info"].get("question"),
                )
                return limit_message
            pre_compact_agent = current_mode if mode_enabled else (runtime_agent or "build")
            messages = compact(messages, llm_config=current_runtime, agent=pre_compact_agent)

            selected_tools = bootstrap.initial_tools
            if mode_enabled:
                current_mode = _resolve_mode_from_messages(messages, fallback=bootstrap.initial_mode)
                current_runtime, current_provider_explicit, current_model_explicit = _resolve_runtime_config(
                    messages,
                    mode=current_mode,
                    provider=provider,
                    provider_specified=False,
                    model=model,
                    model_specified=False,
                )
                selected_tools = _get_tools_for_mode(current_mode)
                messages = _ensure_system_prompt(
                    messages,
                    _get_system_prompt_for_mode(
                        current_mode,
                        model=current_runtime.model,
                        provider=current_runtime.provider,
                        vendor=current_runtime.vendor,
                        session_id=active_session_id,
                    ),
                    active_session_id,
                )
            active_agent = current_mode if mode_enabled else (runtime_agent or "build")
            agent_kind = _resolve_agent_kind(active_agent)

            stopped_message = yield from _consume_stop_request_if_needed(active_agent, current_runtime)
            if stopped_message is not None:
                return stopped_message

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
                            kind="assistant_text",
                            title=f"{active_agent} 回复",
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
                        display_reasoning_merge_open = False
                        yield delta_event
                    continue

                if stream_event.get("type") == "reasoning_delta":
                    delta = str(stream_event.get("delta", ""))
                    if delta:
                        delta_event = _build_stream_event(
                            "reasoning_delta",
                            session_id=active_session_id,
                            agent=active_agent,
                            agent_kind=agent_kind,
                            depth=depth,
                            delegation_id=delegation_id,
                            parent_tool_call_id=parent_tool_call_id,
                            round=round_no,
                            delta=delta,
                        )
                        _append_display_reasoning_part(
                            active_display_parts,
                            delta=delta,
                            created_at=str(delta_event.get("timestamp", "")) or utc_now_iso(),
                            agent=active_agent,
                            agent_kind=agent_kind,
                            depth=depth,
                            round_no=round_no,
                            delegation_id=delegation_id,
                            parent_tool_call_id=parent_tool_call_id,
                            merge_allowed=display_reasoning_merge_open,
                        )
                        display_reasoning_merge_open = True
                        display_text_merge_open = False
                        yield delta_event
                    continue

            stopped_message = yield from _consume_stop_request_if_needed(active_agent, current_runtime)
            if stopped_message is not None:
                return stopped_message

            _set_message_runtime_info(
                assistant_message,
                agent=active_agent,
                model=current_runtime.model,
                provider=current_runtime.provider,
                turn_started_at=turn_started_at,
            )
            messages.append(assistant_message)

            tool_calls = extract_tool_calls(assistant_message)
            finish_reason = _assistant_finish_reason(assistant_message)
            should_continue = _should_continue_after_assistant(assistant_message)
            has_tool_calls = bool(tool_calls)

            if has_tool_calls:
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

            if not should_continue:
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
                if depth == 0:
                    clear_session_stop(active_session_id)
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
                    question=assistant_message["info"].get("question"),
                )
                return assistant_message

            if finish_reason == "unknown" and not has_tool_calls:
                yield _emit_event(
                    "round_end",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=delegation_id,
                    parent_tool_call_id=parent_tool_call_id,
                    round=round_no,
                    status=assistant_message["info"].get("status", "completed"),
                    finish_reason=finish_reason,
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=utc_now_iso(),
                )
                continue

            should_interrupt = False
            task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
            for tool_call in tool_calls:
                stopped_message = yield from _consume_stop_request_if_needed(active_agent, current_runtime)
                if stopped_message is not None:
                    return stopped_message
                if tool_call["name"] == "task":
                    delegation_instance_id = _new_delegation_id()
                    task_request = _prepare_task_tool_request(
                        tool_call["arguments"],
                        delegation_id=delegation_instance_id,
                    )
                    result = task_request.result
                    if task_request.should_execute:
                        registry = _get_skill_registry()
                        delegated_message = yield from _run_session_stream(
                            task_request.prompt,
                            session_id=active_session_id,
                            tools=_get_base_tools_for_agent("build"),
                            system_prompt=_call_build_system_prompt(
                                agent=task_request.agent,
                                model=current_runtime.model,
                                provider=current_runtime.provider,
                                vendor=current_runtime.vendor,
                                session_id=active_session_id,
                            ),
                            runtime_agent=task_request.agent,
                            todo_tool_names={"todo_write", "todo_read"},
                            llm_config=current_runtime,
                            depth=depth + 1,
                            delegation_id=delegation_instance_id,
                            parent_tool_call_id=tool_call["id"],
                            process_items=active_process_items,
                            display_parts=active_display_parts,
                        )
                        delegated_finish_reason = str(delegated_message["info"].get("finish_reason", "")).strip().lower()
                        delegated_status = str(delegated_message["info"].get("status", "")).strip().lower()
                        if delegated_finish_reason == "question_required" or (
                            delegated_status == "interrupted" and isinstance(delegated_message["info"].get("question"), dict)
                        ):
                            result = _pending_question_result_from_message(delegated_message)
                            result["metadata"]["delegation_id"] = delegation_instance_id
                            result["metadata"]["parent_tool_call_id"] = tool_call["id"]
                        else:
                            result["output"] = get_message_text(delegated_message)
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
                        vendor=current_runtime.vendor,
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

                stopped_message = yield from _consume_stop_request_if_needed(active_agent, current_runtime)
                if stopped_message is not None:
                    return stopped_message

                if tool_call["name"] in {"plan_enter", "plan_exit"}:
                    status = str(metadata.get("status", "")).strip().lower()

                    interrupted_message, should_cancel = _handle_mode_switch_tool_result(
                        session_id=active_session_id,
                        tool_name=tool_call["name"],
                        result=result,
                        messages=messages,
                        active_agent=active_agent,
                        current_runtime=current_runtime,
                        current_provider_explicit=current_provider_explicit,
                        current_model_explicit=current_model_explicit,
                        turn_started_at=turn_started_at,
                    )

                    if interrupted_message is not None:
                        completed_at = str(interrupted_message["info"].get("turn_completed_at", ""))
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
                        if depth == 0:
                            clear_session_stop(active_session_id)
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
                            question=interrupted_message["info"].get("question"),
                        )
                        return interrupted_message

                    if should_cancel:
                        should_interrupt = True
                    continue

                if tool_call["name"] != "question":
                    continue

                interrupted_message = _handle_question_tool_result(
                    session_id=active_session_id,
                    tool_name=tool_call["name"],
                    result=result,
                    messages=messages,
                    active_agent=active_agent,
                    current_runtime=current_runtime,
                    turn_started_at=turn_started_at,
                    delegation_id=str(metadata.get("delegation_id", delegation_id or "")).strip() or delegation_id,
                    parent_tool_call_id=str(metadata.get("parent_tool_call_id", parent_tool_call_id or "")).strip()
                    or parent_tool_call_id,
                )
                if interrupted_message is None:
                    continue

                completed_at = str(interrupted_message["info"].get("turn_completed_at", ""))
                question_delegation_id = str(metadata.get("delegation_id", delegation_id or "")).strip() or delegation_id
                question_parent_tool_call_id = (
                    str(metadata.get("parent_tool_call_id", parent_tool_call_id or "")).strip() or parent_tool_call_id
                )
                yield _emit_event(
                    "round_end",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=question_delegation_id,
                    parent_tool_call_id=question_parent_tool_call_id,
                    round=round_no,
                    status=interrupted_message["info"].get("status", "interrupted"),
                    finish_reason=interrupted_message["info"].get("finish_reason", "question_required"),
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
                if depth == 0:
                    clear_session_stop(active_session_id)
                yield _emit_event(
                    "done",
                    agent=active_agent,
                    agent_kind=agent_kind,
                    depth=depth,
                    delegation_id=question_delegation_id,
                    parent_tool_call_id=question_parent_tool_call_id,
                    message_id=str(interrupted_message["info"].get("message_id", "")),
                    status=interrupted_message["info"].get("status", "interrupted"),
                    finish_reason=interrupted_message["info"].get("finish_reason", "question_required"),
                    provider=current_runtime.provider,
                    model=current_runtime.model,
                    completed_at=completed_at,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                    response_meta=response_meta,
                    process_items=[dict(item) for item in active_process_items],
                    display_parts=interrupted_message["info"].get("display_parts", []),
                    confirmation=interrupted_message["info"].get("confirmation"),
                    question=interrupted_message["info"].get("question"),
                )
                return interrupted_message

            if should_interrupt:
                message = _build_cancelled_mode_switch_message(
                    session_id=active_session_id,
                    active_agent=active_agent,
                    current_runtime=current_runtime,
                    turn_started_at=turn_started_at,
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
                if depth == 0:
                    clear_session_stop(active_session_id)
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
                    question=message["info"].get("question"),
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
                finish_reason="tool-calls",
                provider=current_runtime.provider,
                model=current_runtime.model,
                completed_at=utc_now_iso(),
            )
    finally:
        if depth == 0:
            if is_session_stop_requested(active_session_id) and not stop_message_saved:
                fallback_message = _build_stopped_session_message(
                    session_id=active_session_id,
                    active_agent=current_mode if mode_enabled else (runtime_agent or "build"),
                    current_runtime=current_runtime,
                    turn_started_at=turn_started_at,
                )
                completed_at = str(fallback_message["info"].get("turn_completed_at", ""))
                _attach_response_summary(
                    fallback_message,
                    process_items=active_process_items,
                    display_parts=active_display_parts,
                    turn_started_at=turn_started_at,
                    turn_completed_at=completed_at,
                )
                messages.append(fallback_message)
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
            clear_session_stop(active_session_id)


def _build_tool_handlers(
    *,
    session_id: str,
    get_mode: Callable[[], MainAgentMode],
    get_latest_model: Callable[[], str],
    get_current_runtime: Callable[[], ResolvedLLMConfig],
) -> dict[str, Callable[..., object]]:
    mcp_tools, _ = list_mcp_tools()

    def _run_mode_aware_bash(
        command: str,
        timeout: int | float | None = None,
        workdir: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if get_mode() == "plan":
            validation_error = validate_readonly_bash(command)
            if validation_error is not None:
                return build_tool_failure(validation_error, error_code="readonly_violation")
        try:
            _normalize_timeout(timeout)
            resolve_bash_workdir(workdir)
        except FileNotFoundError as exc:
            return build_tool_failure(f"Error: {exc}", error_code="bash_workdir_not_found")
        except NotADirectoryError as exc:
            return build_tool_failure(f"Error: {exc}", error_code="bash_workdir_not_directory")
        except ValueError as exc:
            if "timeout" in str(exc):
                return build_tool_failure(f"Error: {exc}", error_code="bash_timeout_invalid")
            return build_tool_failure(f"Error: {exc}", error_code="bash_workdir_forbidden")
        return build_tool_success(run_bash(command, timeout, workdir))

    def _run_mode_aware_write(file_path: str, content: str) -> dict[str, Any]:
        if get_mode() == "plan" and not is_allowed_plan_write_path(file_path):
            plan_path = str(build_plan_placeholder_path(session_id))
            return build_tool_failure(
                f"Error: plan 模式下仅允许写入 {plan_path} 文件。",
                error_code="plan_write_forbidden",
            )
        return run_write(file_path, content)

    def _run_mode_aware_edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict[str, Any]:
        if get_mode() == "plan" and not is_allowed_plan_write_path(file_path):
            plan_path = str(build_plan_placeholder_path(session_id))
            return build_tool_failure(
                f"Error: plan 模式下仅允许编辑 {plan_path} 文件。",
                error_code="plan_edit_forbidden",
            )
        return run_edit(file_path, old_string, new_string, replace_all)

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

    handlers: dict[str, Callable[..., object]] = {
        "bash": lambda **kw: _run_mode_aware_bash(**kw),
        "glob": lambda **kw: run_glob(kw["pattern"], kw.get("path")),
        "grep": lambda **kw: run_grep(kw["pattern"], kw.get("path"), kw.get("include")),
        "read_file": lambda **kw: run_read(
            kw.get("file_path") or kw.get("path") or kw["filePath"],
            kw.get("limit"),
            kw.get("offset", 0),
        ),
        "write_file": lambda **kw: _run_mode_aware_write(kw["filePath"], kw["content"]),
        "edit_file": lambda **kw: _run_mode_aware_edit(
            kw.get("filePath") or kw.get("path") or kw["file_path"],
            kw.get("oldString") or kw.get("old_text") or kw["old_string"],
            kw.get("newString") or kw.get("new_text") or kw["new_string"],
            bool(kw.get("replaceAll", kw.get("replace_all", False))),
        ),
        "lsp": lambda **kw: run_lsp(
            kw["operation"],
            kw.get("filePath") or kw.get("path") or kw["file_path"],
            kw["line"],
            kw["character"],
        ),
        "webfetch": lambda **kw: webfetch(kw),
        "websearch": lambda **kw: websearch(kw),
        "question": lambda **kw: run_question(questions=kw["questions"]),
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
        "load_skill": lambda **kw: run_load_skill(name=kw["name"], registry=_get_skill_registry()),
    }
    for tool in mcp_tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = str(function.get("name", "")).strip() if isinstance(function, dict) else ""
        if not tool_name:
            continue
        handlers[tool_name] = lambda _tool_name=tool_name, **kw: execute_mcp_tool(
            _tool_name,
            kw,
            mode=get_mode(),
        )
    return handlers


def run_session(
    user_input: str,
    session_id: str,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    model: str | None = None,
    model_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
    max_rounds: int | None = None,
) -> Message:
    """新会话入口：返回最终助手 Message（含结构化 parts）。
    
    Args:
        max_rounds: 最大循环轮次，为 None 时使用默认配置（主 agent 使用 agent_loop.max_rounds）。
    """
    turn_started_at = utc_now_iso()
    try:
        prepared_input = _prepare_session_input(
            user_input=user_input,
            mode=mode,
            tools=tools,
            system_prompt=system_prompt,
        )
    except ValueError as exc:
        active_session_id = set_session_id(normalize_required_session_id(session_id))
        mode_enabled = tools is None and system_prompt is None
        user_message, assistant_message = _build_slash_command_error_message(
            session_id=active_session_id,
            user_input=user_input,
            error_text=str(exc),
            mode=mode,
            turn_started_at=turn_started_at,
        )
        if mode_enabled:
            history_messages = SESSION_MEMORY_STORE.load(active_session_id)
            SESSION_MEMORY_STORE.save(active_session_id, [*history_messages, user_message, assistant_message])
        return assistant_message

    if prepared_input.immediate_output is not None:
        active_session_id = set_session_id(normalize_required_session_id(session_id))
        mode_enabled = tools is None and system_prompt is None
        user_message, assistant_message = _build_slash_command_completed_message(
            session_id=active_session_id,
            user_input=user_input,
            display_input=prepared_input.display_input,
            output_text=prepared_input.immediate_output,
            mode=prepared_input.mode,
            turn_started_at=turn_started_at,
        )
        if mode_enabled:
            history_messages = SESSION_MEMORY_STORE.load(active_session_id)
            SESSION_MEMORY_STORE.save(active_session_id, [*history_messages, user_message, assistant_message])
        return assistant_message

    bootstrap = _bootstrap_session(
        prepared_input.user_input,
        session_id=session_id,
        display_input=prepared_input.display_input,
        mode=prepared_input.mode,
        provider=provider,
        provider_specified=provider_specified,
        model=model,
        model_specified=model_specified,
        tools=tools,
        system_prompt=system_prompt,
        llm_config=llm_config,
        runtime_agent=runtime_agent,
    )
    active_session_id = bootstrap.session_id
    turn_started_at = bootstrap.turn_started_at
    mode_enabled = bootstrap.mode_enabled
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])
    messages = list(bootstrap.messages)
    current_mode: MainAgentMode = bootstrap.current_mode
    current_runtime = bootstrap.current_runtime
    current_provider_explicit = bootstrap.current_provider_explicit
    current_model_explicit = bootstrap.current_model_explicit

    tool_executor = ToolExecutor(
        _build_tool_handlers(
            session_id=active_session_id,
            get_mode=lambda: current_mode,
            get_latest_model=lambda: _latest_model(messages),
            get_current_runtime=lambda: current_runtime,
        )
    )

    effective_max_rounds = max_rounds if max_rounds is not None else resolve_agent_loop_settings().max_rounds
    round_no = 0
    while True:
        round_no += 1
        if round_no > effective_max_rounds:
            active_agent = current_mode if mode_enabled else (runtime_agent or "build")
            limit_message = _build_max_rounds_exceeded_message(
                session_id=active_session_id,
                active_agent=active_agent,
                current_runtime=current_runtime,
                turn_started_at=turn_started_at,
                max_rounds=effective_max_rounds,
            )
            messages.append(limit_message)
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return limit_message
        pre_compact_agent = current_mode if mode_enabled else (runtime_agent or "build")
        messages = compact(messages, llm_config=current_runtime, agent=pre_compact_agent)

        selected_tools = bootstrap.initial_tools
        if mode_enabled:
            current_mode = _resolve_mode_from_messages(messages, fallback=bootstrap.initial_mode)
            current_runtime, current_provider_explicit, current_model_explicit = _resolve_runtime_config(
                messages,
                mode=current_mode,
                provider=provider,
                provider_specified=False,
                model=model,
                model_specified=False,
            )
            selected_tools = _get_tools_for_mode(current_mode)
            messages = _ensure_system_prompt(
                messages,
                _get_system_prompt_for_mode(
                    current_mode,
                    model=current_runtime.model,
                    provider=current_runtime.provider,
                    vendor=current_runtime.vendor,
                    session_id=active_session_id,
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
        should_continue = _should_continue_after_assistant(assistant_message)
        has_tool_calls = bool(tool_calls)

        if not should_continue:
            assistant_message["info"]["turn_completed_at"] = utc_now_iso()
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return assistant_message

        if _assistant_finish_reason(assistant_message) == "unknown" and not has_tool_calls:
            continue

        should_interrupt = False
        task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
        for tool_call in tool_calls:
            if tool_call["name"] == "task":
                task_request = _prepare_task_tool_request(tool_call["arguments"])
                result = task_request.result
                if task_request.should_execute:
                    result["output"] = subagent_loop(
                        task_request.prompt,
                        agent=task_request.agent,
                        session_id=active_session_id,
                        llm_config=current_runtime,
                    )
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
                    vendor=current_runtime.vendor,
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

            if tool_call["name"] in {"plan_enter", "plan_exit"}:
                interrupted_message, should_cancel = _handle_mode_switch_tool_result(
                    session_id=active_session_id,
                    tool_name=tool_call["name"],
                    result=result,
                    messages=messages,
                    active_agent=active_agent,
                    current_runtime=current_runtime,
                    current_provider_explicit=current_provider_explicit,
                    current_model_explicit=current_model_explicit,
                    turn_started_at=turn_started_at,
                )
                if interrupted_message is not None:
                    if mode_enabled:
                        SESSION_MEMORY_STORE.save(active_session_id, messages)
                    return interrupted_message
                if should_cancel:
                    should_interrupt = True
                continue

            if tool_call["name"] != "question":
                continue

            interrupted_message = _handle_question_tool_result(
                session_id=active_session_id,
                tool_name=tool_call["name"],
                result=result,
                messages=messages,
                active_agent=active_agent,
                current_runtime=current_runtime,
                turn_started_at=turn_started_at,
            )
            if interrupted_message is not None:
                if mode_enabled:
                    SESSION_MEMORY_STORE.save(active_session_id, messages)
                return interrupted_message

        if should_interrupt:
            message = _build_cancelled_mode_switch_message(
                session_id=active_session_id,
                active_agent=active_agent,
                current_runtime=current_runtime,
                turn_started_at=turn_started_at,
            )
            messages.append(message)
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return message


def run_session_stream_events(
    user_input: str,
    session_id: str,
    *,
    mode: MainAgentMode | None = None,
    provider: str | None = None,
    provider_specified: bool = False,
    model: str | None = None,
    model_specified: bool = False,
    tools: list[dict] | None = None,
    system_prompt: str | None = None,
    todo_tool_names: set[str] | None = None,
    tool_hooks: list[ToolHook] | None = None,
    llm_config: ResolvedLLMConfig | None = None,
    runtime_agent: str | None = None,
    max_rounds: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """流式会话入口：逐步产出轮次/文本/工具事件。
    
    Args:
        max_rounds: 最大循环轮次，为 None 时使用默认配置（主 agent 使用 agent_loop.max_rounds）。
    """
    yield from _run_session_stream(
        user_input,
        session_id=session_id,
        mode=mode,
        provider=provider,
        provider_specified=provider_specified,
        model=model,
        model_specified=model_specified,
        tools=tools,
        system_prompt=system_prompt,
        todo_tool_names=todo_tool_names,
        tool_hooks=tool_hooks,
        llm_config=llm_config,
        runtime_agent=runtime_agent,
        max_rounds=max_rounds,
    )


def agent_loop(user_input: str, session_id: str) -> Message:
    """兼容入口：内部转发到新接口。"""
    return run_session(user_input=user_input, session_id=session_id)
