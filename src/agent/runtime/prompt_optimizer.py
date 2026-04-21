from __future__ import annotations

import uuid
from typing import Literal

from ..adapters.llm.client import create_chat_completion
from ..config.settings import resolve_llm_config
from ..core.message import append_text_part, create_message, get_message_text


PROMPT_OPTIMIZER_SYSTEM_PROMPT = """你是一个专业的 Prompt 优化助手。
你的任务是把用户提供的原始 prompt 优化成更清晰、可执行、边界明确的版本。

要求：
- 只输出优化后的 prompt 正文，不要输出解释、标题、代码块围栏或前后寒暄。
- 保留用户原始意图、语言风格和关键约束，不要擅自扩大任务范围。
- 补足目标、上下文、输出要求、边界条件和验收标准。
- 如果原始 prompt 已经足够清晰，只做轻量润色。
"""


def optimize_prompt(
    prompt: str,
    *,
    mode: Literal["build", "plan"] = "build",
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str]:
    """使用当前 LLM 运行时优化 prompt，并返回优化结果与实际运行时信息。"""
    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise ValueError("prompt 不能为空")

    llm_config = resolve_llm_config(mode, provider, model)
    session_id = f"prompt_optimize_{uuid.uuid4().hex[:12]}"

    system_message = create_message("system", session_id=session_id, status="completed")
    append_text_part(system_message, PROMPT_OPTIMIZER_SYSTEM_PROMPT)

    user_message = create_message("user", session_id=session_id, status="completed")
    append_text_part(user_message, f"请优化下面这段 prompt：\n\n{normalized_prompt}")

    response_message = create_chat_completion(
        messages=[system_message, user_message],
        tools=[],
        llm_config=llm_config,
        agent=mode,
    )
    if response_message["info"].get("status") != "completed":
        detail = get_message_text(response_message) or "LLM 优化 prompt 失败"
        raise RuntimeError(detail)

    optimized_prompt = get_message_text(response_message).strip()
    if not optimized_prompt:
        raise RuntimeError("LLM 返回了空的优化结果")

    return optimized_prompt, llm_config.provider, llm_config.model
