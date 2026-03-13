import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from collections.abc import Generator
from typing import Any, Callable, Literal

from ..adapters.llm.client import create_chat_completion, create_chat_completion_stream
from ..config.settings import ResolvedLLMConfig, resolve_llm_config
from ..core.context import set_session_id
from ..core.message import (
    EVENT_BUS,
    Message,
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

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
]


def _normalize_model_name(model: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", model.strip().lower()).strip("._-")
    return normalized or "default"


def _get_workdir() -> Path:
    return Path.cwd()


def _resolve_build_prompt_path(model: str) -> Path:
    normalized_model = _normalize_model_name(model)
    candidate = PROMPTS_DIR / f"build.{normalized_model}.txt"
    if candidate.exists():
        return candidate
    return PROMPTS_DIR / "build.default.txt"


def _resolve_prompt_path(agent: str, model: str) -> Path:
    agent_name = agent.strip().lower()
    if agent_name == "build":
        return _resolve_build_prompt_path(model)
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


def _build_environment_appendix(*, agent: str, model: str, provider: str) -> str:
    workdir = _get_workdir()
    is_git_repo, git_root = _detect_git_repository(workdir)
    now_text = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "以下是当前运行环境信息：",
        f"- agent: {agent}",
        f"- provider: {provider or 'unknown'}",
        f"- model: {model or 'unknown'}",
        f"- workdir: {workdir}",
        f"- is_git_repo: {'true' if is_git_repo else 'false'}",
        f"- git_root: {git_root or 'N/A'}",
        f"- current_datetime: {now_text}",
    ]
    return "\n".join(lines)


def build_system_prompt(*, agent: str, model: str, provider: str) -> str:
    base_prompt = _read_prompt_file(_resolve_prompt_path(agent, model))
    parts = [
        base_prompt,
        _read_local_agent_appendix(),
        _build_environment_appendix(agent=agent, model=model, provider=provider),
    ]
    return "\n\n".join(part for part in parts if part)


def _get_system_prompt_for_mode(mode: MainAgentMode, *, model: str, provider: str) -> str:
    return build_system_prompt(agent=mode, model=model, provider=provider)


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
    return message


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


def _on_message_completed(event: dict) -> None:
    payload = event.get("payload", {})
    logger.debug(
        "event.message_completed session_id=%s message_id=%s finish_reason=%s",
        event.get("session_id", ""),
        event.get("message_id", ""),
        payload.get("finish_reason", ""),
    )


EVENT_BUS.subscribe("message_completed", _on_message_completed)


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


def configure_session_memory_store(store: SessionMemoryStore) -> None:
    """配置会话记忆存储实现，便于替换为 Redis 等后端。"""
    global SESSION_MEMORY_STORE
    SESSION_MEMORY_STORE = store


def clear_session_memory(session_id: str | None = None) -> None:
    SESSION_MEMORY_STORE.clear(session_id)


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

    logger.info("subagent.start agent=%s prompt_preview=%s", agent_name, prompt[:120].replace("\n", "\\n"))
    result = run_session(
        user_input=prompt,
        session_id=session_id,
        tools=build_base_tools(registry.list_briefs()),
        system_prompt=build_system_prompt(
            agent=agent_name,
            model=(llm_config.model if llm_config else ""),
            provider=(llm_config.provider if llm_config else ""),
        ),
        runtime_agent=agent_name,
        todo_tool_names={"todo_write", "todo_read"},
        llm_config=llm_config,
    )
    return get_message_text(result)


def _build_tool_handlers(
    *,
    session_id: str,
    get_mode: Callable[[], MainAgentMode],
    get_latest_model: Callable[[], str],
    get_current_runtime: Callable[[], ResolvedLLMConfig],
    get_provider_explicit: Callable[[], bool],
) -> dict[str, Callable[..., object]]:
    def _normalize_confirmed(raw_args: dict[str, Any]) -> bool | None:
        if "confirmed" not in raw_args:
            return None
        return bool(raw_args.get("confirmed"))

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
            confirmed=_normalize_confirmed(kw),
        )

    def _run_plan_exit_tool(**kw: Any) -> dict[str, Any]:
        plan_path = str(build_plan_placeholder_path(session_id))
        return run_plan_exit(
            current_mode=get_mode(),
            plan_path=plan_path,
            plan_exists=Path(plan_path).exists(),
            latest_model=get_latest_model(),
            confirmed=_normalize_confirmed(kw),
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
        _get_system_prompt_for_mode(initial_mode, model=initial_runtime.model, provider=initial_runtime.provider)
        if mode_enabled
        else (
            system_prompt
            or build_system_prompt(
                agent=runtime_agent or "build",
                model=(llm_config.model if llm_config else ""),
                provider=(llm_config.provider if llm_config else ""),
            )
        )
    )

    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])

    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

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
        messages = compact(messages)

        logger.debug(
            "session.round.start session_id=%s round=%d message_count=%d",
            active_session_id,
            round_no,
            len(messages),
        )

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
                _get_system_prompt_for_mode(current_mode, model=current_runtime.model, provider=current_runtime.provider),
                active_session_id,
            )
        active_agent = current_mode if mode_enabled else (runtime_agent or "build")

        assistant_message = create_chat_completion(messages=messages, tools=selected_tools, llm_config=current_runtime)
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
            if has_todo_call:
                non_todo_round_streak = 0
            else:
                non_todo_round_streak += 1

        if not has_tool_calls:
            assistant_message["info"]["turn_completed_at"] = utc_now_iso()
            logger.info(
                "session.round.finish session_id=%s round=%d status=%s",
                active_session_id,
                round_no,
                assistant_message["info"].get("status", "unknown"),
            )
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            return assistant_message

        should_interrupt = False
        task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
        for tool_call in tool_calls:
            result = tool_executor.execute(
                tool_call["name"],
                tool_call["arguments"],
                session_id=active_session_id,
                tool_call_id=tool_call["id"],
                round_no=round_no,
                hooks=effective_tool_hooks,
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

        if non_todo_round_streak >= 3:
            messages.append(_build_text_message("user", todo_reminder_text, active_session_id))


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
        _get_system_prompt_for_mode(initial_mode, model=initial_runtime.model, provider=initial_runtime.provider)
        if mode_enabled
        else (
            system_prompt
            or build_system_prompt(
                agent=runtime_agent or "build",
                model=(llm_config.model if llm_config else ""),
                provider=(llm_config.provider if llm_config else ""),
            )
        )
    )

    todo_names = todo_tool_names if todo_tool_names is not None else {"todo_write", "todo_read"}
    effective_tool_hooks = get_global_tool_hooks() + (tool_hooks or [])

    todo_reminder_text = "提醒：你已经连续多轮未更新 todo，请尽快使用 todo 同步当前计划与进度。"
    non_todo_round_streak = 0

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

    yield {
        "type": "start",
        "session_id": active_session_id,
        "mode": initial_mode,
        "provider": current_runtime.provider,
        "model": current_runtime.model,
        "started_at": turn_started_at,
    }

    round_no = 0
    while True:
        round_no += 1
        messages = compact(messages)

        logger.debug(
            "session.stream.round.start session_id=%s round=%d message_count=%d",
            active_session_id,
            round_no,
            len(messages),
        )

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
                _get_system_prompt_for_mode(current_mode, model=current_runtime.model, provider=current_runtime.provider),
                active_session_id,
            )
        active_agent = current_mode if mode_enabled else (runtime_agent or "build")

        yield {
            "type": "round_start",
            "round": round_no,
            "agent": active_agent,
            "provider": current_runtime.provider,
            "model": current_runtime.model,
            "started_at": utc_now_iso(),
        }

        stream_iter = create_chat_completion_stream(messages=messages, tools=selected_tools, llm_config=current_runtime)
        while True:
            try:
                stream_event = next(stream_iter)
            except StopIteration as stop:
                assistant_message = stop.value
                break

            if stream_event.get("type") == "text_delta":
                delta = str(stream_event.get("delta", ""))
                if delta:
                    yield {
                        "type": "text_delta",
                        "round": round_no,
                        "delta": delta,
                    }

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
            if has_todo_call:
                non_todo_round_streak = 0
            else:
                non_todo_round_streak += 1

            for tool_call in tool_calls:
                yield {
                    "type": "tool_call",
                    "round": round_no,
                    "tool_call_id": tool_call["id"],
                    "name": tool_call["name"],
                    "arguments": tool_call["arguments"],
                }

        if not has_tool_calls:
            assistant_message["info"]["turn_completed_at"] = utc_now_iso()
            if mode_enabled:
                SESSION_MEMORY_STORE.save(active_session_id, messages)
            yield {
                "type": "round_end",
                "round": round_no,
                "status": assistant_message["info"].get("status", "completed"),
                "finish_reason": assistant_message["info"].get("finish_reason", "stop"),
                "provider": current_runtime.provider,
                "model": current_runtime.model,
                "completed_at": utc_now_iso(),
            }
            yield {
                "type": "done",
                "session_id": active_session_id,
                "message_id": str(assistant_message["info"].get("message_id", "")),
                "status": assistant_message["info"].get("status", "completed"),
                "provider": current_runtime.provider,
                "model": current_runtime.model,
                "completed_at": utc_now_iso(),
            }
            return

        should_interrupt = False
        task_available = any(tool["function"]["name"] == "task" for tool in selected_tools)
        for tool_call in tool_calls:
            result = tool_executor.execute(
                tool_call["name"],
                tool_call["arguments"],
                session_id=active_session_id,
                tool_call_id=tool_call["id"],
                round_no=round_no,
                hooks=effective_tool_hooks,
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
            yield {
                "type": "tool_result",
                "round": round_no,
                "tool_call_id": tool_call["id"],
                "name": tool_call["name"],
                "status": str(metadata.get("status", "completed")),
                "output_preview": _tool_result_preview(result),
            }

            if tool_call["name"] not in {"plan_enter", "plan_exit"}:
                continue

            status = str(metadata.get("status", "")).strip().lower()

            if status == "confirmation_required":
                output_text = str(result.get("output", "请确认后继续。"))
                question = str(metadata.get("confirmation_question", "是否继续？"))
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
                yield {
                    "type": "round_end",
                    "round": round_no,
                    "status": interrupted_message["info"].get("status", "interrupted"),
                    "finish_reason": interrupted_message["info"].get("finish_reason", "confirmation_required"),
                    "provider": current_runtime.provider,
                    "model": current_runtime.model,
                    "completed_at": utc_now_iso(),
                }
                yield {
                    "type": "done",
                    "session_id": active_session_id,
                    "message_id": str(interrupted_message["info"].get("message_id", "")),
                    "status": interrupted_message["info"].get("status", "interrupted"),
                    "provider": current_runtime.provider,
                    "model": current_runtime.model,
                    "completed_at": utc_now_iso(),
                }
                return

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
            yield {
                "type": "round_end",
                "round": round_no,
                "status": "interrupted",
                "finish_reason": "cancelled",
                "provider": current_runtime.provider,
                "model": current_runtime.model,
                "completed_at": utc_now_iso(),
            }
            yield {
                "type": "done",
                "session_id": active_session_id,
                "message_id": str(message["info"].get("message_id", "")),
                "status": "interrupted",
                "provider": current_runtime.provider,
                "model": current_runtime.model,
                "completed_at": utc_now_iso(),
            }
            return

        yield {
            "type": "round_end",
            "round": round_no,
            "status": "completed",
            "finish_reason": "tool_call",
            "provider": current_runtime.provider,
            "model": current_runtime.model,
            "completed_at": utc_now_iso(),
        }

        if non_todo_round_streak >= 3:
            messages.append(_build_text_message("user", todo_reminder_text, active_session_id))


def agent_loop(user_input: str, session_id: str | None = None) -> Message:
    """兼容入口：内部转发到新接口。"""
    return run_session(user_input=user_input, session_id=session_id)
