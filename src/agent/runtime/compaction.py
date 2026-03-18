import json
import logging
from pathlib import Path
from typing import Any, TypedDict

from ..adapters.llm.client import create_chat_completion
from ..config.logging_setup import build_log_extra
from ..config.settings import (
    CompactionSettings,
    DEFAULT_SUMMARY_TRIGGER_THRESHOLD,
    DEFAULT_TOOL_OUTPUT_MAX_BYTES,
    DEFAULT_TOOL_OUTPUT_MAX_LINES,
    ResolvedLLMConfig,
    resolve_compaction_settings,
)
from ..core.message import (
    Message,
    append_compaction_part,
    append_compact_summary_part,
    append_text_part,
    create_message,
    get_message_text,
    get_role,
    trim_messages_by_compaction_checkpoint,
)

THRESHOLD = DEFAULT_SUMMARY_TRIGGER_THRESHOLD
TOOL_OUTPUT_MAX_LINES = DEFAULT_TOOL_OUTPUT_MAX_LINES
TOOL_OUTPUT_MAX_BYTES = DEFAULT_TOOL_OUTPUT_MAX_BYTES

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ToolOutputTruncationResult(TypedDict):
    output: str
    metadata: dict[str, Any]


def _safe_name(value: str, fallback: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value).strip("._")
    return normalized or fallback


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _build_tool_output_path(workdir: Path, session_id: str, tool_name: str, tool_call_id: str) -> Path:
    del workdir
    session_segment = _safe_name(session_id, "default_session")
    tool_segment = _safe_name(tool_name, "tool")
    call_segment = _safe_name(tool_call_id, "call")
    # tool 输出统一固定落到仓库内，避免启动目录变化导致排查路径漂移。
    return (PROJECT_ROOT / "src" / "storage" / "tool-output" / session_segment / f"{tool_segment}-{call_segment}.log").resolve()


def _build_preview_text(text: str, *, max_lines: int, max_bytes: int) -> str:
    if not text:
        return text

    kept_lines: list[str] = []
    total_bytes = 0
    line_count = 0

    for line in text.splitlines(keepends=True):
        encoded = line.encode("utf-8")
        if line_count >= max_lines or total_bytes + len(encoded) > max_bytes:
            break
        kept_lines.append(line)
        total_bytes += len(encoded)
        line_count += 1

    preview = "".join(kept_lines)
    if preview:
        return preview

    clipped = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return clipped


def _build_truncation_notice(
    *,
    full_output_path: str,
    original_lines: int,
    original_bytes: int,
    task_available: bool,
    tool_succeeded: bool,
) -> str:
    status_text = "这次 tool 调用成功了，但输出已被截断。" if tool_succeeded else "这次 tool 调用返回了错误输出，但内容已被截断。"
    size_text = f"原始输出共 {original_lines} 行、{original_bytes} 字节。"
    path_text = f"完整输出已保存到: {full_output_path}"
    if task_available:
        suggestion = (
            "不要直接把整份长文件重新塞回上下文。"
            "建议优先使用 Task 工具委托 explore agent，通过 bash + rg 搜索关键片段，"
            "再用 read_file 配合 offset/limit 分段读取，以节省上下文。"
        )
    else:
        suggestion = "建议先用 bash + rg 搜索关键内容，或者用 read_file 配合 offset/limit 分段查看完整输出。"
    return "\n".join([status_text, size_text, path_text, suggestion])


def apply_tool_output_truncation(
    *,
    text: str,
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    workdir: Path,
    task_available: bool,
    vendor: str | None = None,
    metadata: dict[str, Any] | None = None,
    max_lines: int | None = None,
    max_bytes: int | None = None,
) -> ToolOutputTruncationResult:
    compaction_settings = resolve_compaction_settings(vendor)
    effective_max_lines = compaction_settings.tool_output_max_lines if max_lines is None else max_lines
    effective_max_bytes = compaction_settings.tool_output_max_bytes if max_bytes is None else max_bytes
    base_metadata = dict(metadata or {})
    original_lines = _line_count(text)
    original_bytes = _utf8_size(text)
    tool_succeeded = str(base_metadata.get("status", "completed")).strip().lower() == "completed"

    if original_lines <= effective_max_lines and original_bytes <= effective_max_bytes:
        base_metadata.setdefault("truncated", False)
        return {
            "output": text,
            "metadata": base_metadata,
        }

    preview = _build_preview_text(text, max_lines=effective_max_lines, max_bytes=effective_max_bytes)
    preview_lines = _line_count(preview)
    preview_bytes = _utf8_size(preview)
    output_path = _build_tool_output_path(workdir, session_id, tool_name, tool_call_id)

    base_metadata.update(
        {
            "truncated": True,
            "original_lines": original_lines,
            "original_bytes": original_bytes,
            "preview_lines": preview_lines,
            "preview_bytes": preview_bytes,
        }
    )

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        base_metadata["full_output_path"] = str(output_path)
        notice = _build_truncation_notice(
            full_output_path=str(output_path),
            original_lines=original_lines,
            original_bytes=original_bytes,
            task_available=task_available,
            tool_succeeded=tool_succeeded,
        )
    except OSError as exc:
        base_metadata["full_output_write_error"] = f"{type(exc).__name__}: {exc}"
        notice = "\n".join(
            [
                "这次 tool 调用成功了，但输出已被截断。" if tool_succeeded else "这次 tool 调用返回了错误输出，但内容已被截断。",
                f"原始输出共 {original_lines} 行、{original_bytes} 字节。",
                f"完整输出落盘失败: {type(exc).__name__}: {exc}",
                (
                    "不要直接把整份长文件重新塞回上下文。建议优先使用 Task 工具委托 explore agent，"
                    "通过 bash + rg 搜索关键片段，再用 read_file 配合 offset/limit 分段读取，以节省上下文。"
                    if task_available
                    else "建议先用 bash + rg 搜索关键内容，或者用 read_file 配合 offset/limit 分段查看。"
                ),
            ]
        )

    truncated_output = f"{preview.rstrip()}\n\n{notice}".strip()
    return {
        "output": truncated_output,
        "metadata": base_metadata,
    }


def _part_content(part: dict[str, Any]) -> str:
    if part.get("type") == "tool":
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state.get("output"), dict) else {}
        text = output.get("output", "")
        if isinstance(text, str):
            return text
        try:
            return json.dumps(text, ensure_ascii=False)
        except Exception:
            return str(text)

    content = part.get("content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def prune(messages: list[Message], *, settings: CompactionSettings | None = None) -> list[Message]:
    """按配置压缩较早的工具输出，只保留最近若干条完整 tool result。"""
    compaction_settings = settings or resolve_compaction_settings()
    if not compaction_settings.tool_result_prune_enabled:
        return messages

    keep_recent = compaction_settings.tool_result_keep_recent
    tool_messages: list[Message] = [msg for msg in messages if get_role(msg) == "tool"]

    if len(tool_messages) <= keep_recent:
        return messages

    prune_upto = len(tool_messages) - keep_recent
    for msg in tool_messages[:prune_upto]:
        for part in msg["parts"]:
            if part.get("type") != "tool":
                continue
            content = _part_content(part)
            if len(content) > compaction_settings.tool_result_prune_min_chars:
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                output = state.get("output") if isinstance(state.get("output"), dict) else {}
                if output:
                    output["output"] = "[Old tool result content cleared]"
                    state["output"] = output
                    part["state"] = state

    return messages


def _estimate_tokens(messages: list[Message]) -> int:
    """粗略估算消息 token 数量，近似按 1 token ~= 4 字符。"""
    total = 0
    for msg in messages:
        for part in msg["parts"]:
            raw = _part_content(part)
            total += max(1, len(raw) // 4)
            if part.get("type") == "tool":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                input_data = state.get("input") if isinstance(state.get("input"), dict) else {}
                arguments = str(input_data.get("arguments", ""))
                total += max(1, len(arguments) // 4)

    return total


def compaction_summary(
    messages: list[Message],
    *,
    llm_config: ResolvedLLMConfig | None = None,
    agent: str = "build",
    settings: CompactionSettings | None = None,
) -> list[Message]:
    """当上下文超过阈值时，生成 compaction checkpoint 并折叠更早历史。"""
    if not messages:
        return messages

    compaction_settings = settings or resolve_compaction_settings(llm_config.vendor if llm_config else None)
    token_size = _estimate_tokens(messages)
    model_name = llm_config.model if llm_config else ""
    logger.info(
        "compaction.check token_size=%s threshold=%s message_count=%s",
        token_size,
        compaction_settings.summary_trigger_threshold,
        len(messages),
        extra=build_log_extra(agent=agent, model=model_name),
    )
    if token_size <= compaction_settings.summary_trigger_threshold:
        logger.info(
            "compaction.skip reason=below_threshold token_size=%s",
            token_size,
            extra=build_log_extra(agent=agent, model=model_name),
        )
        return trim_messages_by_compaction_checkpoint(messages)

    system_messages = [m for m in messages if get_role(m) == "system"]
    summarize_messages = [m for m in messages if get_role(m) != "system"]

    lines = []
    for i, msg in enumerate(summarize_messages, 1):
        role = get_role(msg)
        content = get_message_text(msg)
        lines.append(f"{i}. [{role}] {content}")

    summary_prompt = f"""
你是一个乐于助人的 AI 助手，负责总结对话内容。
当被要求进行总结时，请提供一份详尽但简洁的对话摘要。
重点包含以下有助于继续对话的信息：
已完成的工作
当前正在处理的任务
正在修改的文件
下一步需要完成的事项
用户的关键需求、约束条件或偏好（需持续关注）
重要的技术决策及其原因
你的摘要应足够全面以提供上下文，同时足够简洁以便快速理解。

以下是会话内容：
{chr(10).join(lines)}
"""

    summary_messages: list[Message] = []
    session_id = messages[-1]["info"].get("session_id", "default_session")

    system_message = create_message("system", session_id=session_id)
    append_text_part(system_message, "你是一个擅长上下文压缩的助手，请输出简洁、结构化的中文摘要。")
    summary_messages.append(system_message)

    user_message = create_message("user", session_id=session_id, status="completed")
    append_text_part(user_message, summary_prompt)
    summary_messages.append(user_message)

    logger.info(
        "compaction.summary_request token_size=%s summary_message_count=%s",
        token_size,
        len(summary_messages),
        extra=build_log_extra(agent=agent, model=model_name),
    )
    response_message = create_chat_completion(
        messages=summary_messages,
        tools=[],
        max_tokens=compaction_settings.summary_max_tokens,
        llm_config=llm_config,
        agent=agent,
    )
    if response_message["info"].get("status") != "completed":
        logger.warning(
            "compaction.summary_failed status=%s finish_reason=%s",
            response_message["info"].get("status", ""),
            response_message["info"].get("finish_reason", ""),
            extra=build_log_extra(agent=agent, model=model_name),
        )
        return messages

    summary_text = get_message_text(response_message).strip()
    if not summary_text:
        logger.warning(
            "compaction.summary_empty",
            extra=build_log_extra(agent=agent, model=model_name),
        )
        return messages

    logger.info(
        "compaction.summary_done summary_chars=%s",
        len(summary_text),
        extra=build_log_extra(agent=agent, model=model_name),
    )

    compaction_message = create_message("user", session_id=session_id, status="completed")
    append_compaction_part(compaction_message, "以下历史消息已完成压缩总结，请结合下一条摘要继续当前任务。")
    append_compact_summary_part(compaction_message, "以下是历史对话摘要请求，请参考下一条 summary assistant。")

    response_message["info"]["parent_id"] = str(compaction_message["info"].get("message_id", ""))
    response_message["info"]["summary"] = True
    if not str(response_message["info"].get("finish_reason", "")).strip():
        response_message["info"]["finish_reason"] = "stop"

    return system_messages + trim_messages_by_compaction_checkpoint([*summarize_messages, compaction_message, response_message])


def compact(
    messages: list[Message],
    *,
    llm_config: ResolvedLLMConfig | None = None,
    agent: str = "build",
) -> list[Message]:
    """压缩接口，先微压缩，再按阈值做摘要压缩。"""
    compaction_settings = resolve_compaction_settings(llm_config.vendor if llm_config else None)
    pruned = prune(messages, settings=compaction_settings)
    return compaction_summary(pruned, llm_config=llm_config, agent=agent, settings=compaction_settings)
