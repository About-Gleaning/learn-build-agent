import json
from typing import Any

from .client import create_chat_completion
from .message import (
    Message,
    append_compact_summary_part,
    append_text_part,
    create_message,
    get_message_text,
    get_role,
)

THRESHOLD = 50000
KEEP_RECENT = 3


def _part_content(part: dict[str, Any]) -> str:
    content = part.get("content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def prune(messages: list[Message]) -> list[Message]:
    """压缩较早的工具输出，只保留最近 KEEP_RECENT 条完整 tool_result。"""
    tool_messages: list[Message] = [msg for msg in messages if get_role(msg) == "tool"]

    if len(tool_messages) <= KEEP_RECENT:
        return messages

    for msg in tool_messages[:-KEEP_RECENT]:
        for part in msg["parts"]:
            if part.get("type") != "tool_result":
                continue
            content = _part_content(part)
            if len(content) > 100:
                part["content"] = "[Old tool result content cleared]"

    return messages


def _estimate_tokens(messages: list[Message]) -> int:
    """粗略估算消息 token 数量，近似按 1 token ~= 4 字符。"""
    total = 0
    for msg in messages:
        for part in msg["parts"]:
            raw = _part_content(part)
            total += max(1, len(raw) // 4)
            if part.get("type") == "tool_call":
                arguments = str(part.get("arguments", ""))
                total += max(1, len(arguments) // 4)

    return total


def compaction_summary(messages: list[Message]) -> list[Message]:
    """当上下文超过阈值时，调用 LLM 生成摘要并替换历史非 system 消息。"""
    if not messages:
        return messages

    token_size = _estimate_tokens(messages)
    if token_size <= THRESHOLD:
        return messages

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
    system_message = create_message("system", session_id=messages[-1]["info"].get("session_id", "default_session"))
    append_text_part(system_message, "你是一个擅长上下文压缩的助手，请输出简洁、结构化的中文摘要。")
    summary_messages.append(system_message)

    user_message = create_message("user", session_id=messages[-1]["info"].get("session_id", "default_session"))
    append_text_part(user_message, summary_prompt)
    summary_messages.append(user_message)

    response_message = create_chat_completion(
        messages=summary_messages,
        tools=[],
        max_tokens=2000,
    )
    if response_message["info"].get("status") != "completed":
        return messages
    summary_text = get_message_text(response_message).strip()

    if not summary_text:
        return messages

    compact_message = create_message("user", session_id=messages[-1]["info"].get("session_id", "default_session"))
    append_compact_summary_part(compact_message, "以下是历史对话摘要（自动压缩生成）：\n" + summary_text)

    continue_message = create_message("assistant", session_id=messages[-1]["info"].get("session_id", "default_session"))
    append_text_part(continue_message, "Understood. Continuing.")

    return system_messages + [compact_message, continue_message]


def compact(messages: list[Message]) -> list[Message]:
    """压缩接口，先微压缩，再按阈值做摘要压缩。"""
    pruned = prune(messages)
    return compaction_summary(pruned)
