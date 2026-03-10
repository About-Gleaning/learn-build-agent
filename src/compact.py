import json
from pathlib import Path
import time

from .client import create_chat_completion


THRESHOLD = 50000
KEEP_RECENT = 3

def prune(messages: list) -> list:
    """
    压缩较早的 tool 消息内容，只保留最近 KEEP_RECENT 条完整 tool 输出。
    """
    tool_messages = []

    # 记录所有 tool 消息的位置
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            tool_messages.append((i, msg))

    # 如果 tool 消息数量不多，就不压缩
    if len(tool_messages) <= KEEP_RECENT:
        return messages

    # 压缩较老的 tool 消息，保留最近 keep_recent 条
    for _, msg in tool_messages[:-KEEP_RECENT]:
        content = msg.get("content", "")
        if len(content) > 100:
            msg["content"] = "[Old tool result content cleared]"

    return messages


def _estimate_tokens(messages: list) -> int:
    """
    粗略估算消息 token 数量。
    说明：按经验使用 1 token ~= 4 字符进行近似，复杂结构序列化后再估算。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            raw = content
        else:
            try:
                raw = json.dumps(content, ensure_ascii=False)
            except Exception:
                raw = str(content)

        total += max(1, len(raw) // 4)

        tool_calls = msg.get("tool_calls")
        if tool_calls is not None:
            try:
                total += max(1, len(json.dumps(tool_calls, ensure_ascii=False)) // 4)
            except Exception:
                total += max(1, len(str(tool_calls)) // 4)

    return total


def compaction_summary(messages: list) -> list:
    """
    压缩上下文：
    1) 当消息 token 估算超过阈值时触发总结；
    2) 调用 LLM 总结历史消息，且 max_tokens 固定为 2000。
    """
    if not messages:
        return messages

    token_size = _estimate_tokens(messages)
    if token_size <= THRESHOLD:
        return messages

    # 保留所有 system 消息，其余内容进行总结
    system_messages = [m for m in messages if m.get("role") == "system"]
    summarize_messages = [m for m in messages if m.get("role") != "system"]

    lines = []
    for i, msg in enumerate(summarize_messages, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        lines.append(f"{i}. [{role}] {content}")

    summary_prompt = (f"""
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
    )

    try:
        response = create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个擅长上下文压缩的助手，请输出简洁、结构化的中文摘要。",
                },
                {"role": "user", "content": summary_prompt},
            ],
            tools=[],
            max_tokens=2000,
        )
        summary_text = (response.choices[0].message.content or "").strip()
    except Exception:
        return messages

    if not summary_text:
        return messages

    compact_message = {
        "role": "user",
        "content": "以下是历史对话摘要（自动压缩生成）：\n" + summary_text,
    }
    continue_message = {
        "role": "assistant",
        "content": "Understood. Continuing.",
    }

    return system_messages + [compact_message] + [continue_message]

def compact(messages: list) -> list:
    """
    压缩接口，先进行微压缩，再根据 token 估算结果决定是否进行总结压缩。
    """
    pruned = prune(messages)
    return compaction_summary(pruned)