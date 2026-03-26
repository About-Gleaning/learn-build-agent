from typing import Any

from .handlers import build_tool_failure

CUSTOM_OPTION_LABEL = "不是以上任何选项"
CUSTOM_OPTION_DESCRIPTION = "以上选项都不符合，请填写自定义说明"


def _normalize_question_option(raw_option: Any, *, index: int, question_index: int) -> dict[str, str]:
    if not isinstance(raw_option, dict):
        raise ValueError(f"第 {question_index + 1} 个问题的第 {index + 1} 个选项必须是对象")
    label = str(raw_option.get("label", "")).strip()
    description = str(raw_option.get("description", "")).strip()
    if not label:
        raise ValueError(f"第 {question_index + 1} 个问题的第 {index + 1} 个选项缺少 label")
    if not description:
        raise ValueError(f"第 {question_index + 1} 个问题的第 {index + 1} 个选项缺少 description")
    return {
        "label": label,
        "description": description,
    }


def _has_custom_option(options: list[dict[str, str]]) -> bool:
    return any(str(option.get("label", "")).strip() == CUSTOM_OPTION_LABEL for option in options)


def _normalize_question_item(raw_question: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw_question, dict):
        raise ValueError(f"第 {index + 1} 个问题必须是对象")
    question = str(raw_question.get("question", "")).strip()
    header = str(raw_question.get("header", "")).strip()
    multiple = bool(raw_question.get("multiple", False))
    custom = bool(raw_question.get("custom", True))
    raw_options = raw_question.get("options")
    if not question:
        raise ValueError(f"第 {index + 1} 个问题缺少 question")
    if not header:
        raise ValueError(f"第 {index + 1} 个问题缺少 header")
    if not isinstance(raw_options, list) or not raw_options:
        raise ValueError(f"第 {index + 1} 个问题至少需要一个选项")

    options = [_normalize_question_option(item, index=option_index, question_index=index) for option_index, item in enumerate(raw_options)]
    # 统一由后端补齐兜底选项，避免模型生成多种“其他”文案导致重复和展示不一致。
    if custom and not _has_custom_option(options):
        options.append(
            {
                "label": CUSTOM_OPTION_LABEL,
                "description": CUSTOM_OPTION_DESCRIPTION,
            }
        )

    return {
        "question": question,
        "header": header,
        "options": options,
        "multiple": multiple,
        "custom": custom,
    }


def run_question(*, questions: Any) -> dict[str, Any]:
    if not isinstance(questions, list) or not questions:
        return build_tool_failure("Error: `questions` 必须是非空数组。", error_code="question_invalid")

    try:
        normalized_questions = [_normalize_question_item(item, index=index) for index, item in enumerate(questions)]
    except ValueError as exc:
        return build_tool_failure(f"Error: {exc}", error_code="question_invalid")

    question_count = len(normalized_questions)
    title = f"等待用户回答 {question_count} 个问题"
    return {
        "title": title,
        "output": "等待用户回答问题后再继续。",
        "metadata": {
            "status": "question_required",
            "questions": normalized_questions,
            "question_count": question_count,
        },
    }
