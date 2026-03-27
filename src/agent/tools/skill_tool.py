from typing import Any

from ..skills.runtime import SkillRegistry
from .handlers import build_tool_failure


def run_load_skill(*, name: Any, registry: SkillRegistry) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return build_tool_failure(
            "Error: `name` 必须是非空字符串。",
            error_code="skill_name_invalid",
        )

    skill = registry.get_skill(normalized_name)
    if skill is None:
        return build_tool_failure(
            f"Error: 未找到 skill: {normalized_name}",
            error_code="skill_not_found",
            name=normalized_name,
        )

    skill_content = skill.load_full_content()
    output = "\n".join(
        [
            f"## Skill: {skill.name}",
            f"Base directory: {skill.path}",
            "",
            skill_content,
        ]
    )
    # 仅注入当前 skill 的根目录与原始 SKILL.md，避免附带额外扫描信息，降低上下文噪音。
    return {
        "title": f"Loaded skill: {skill.name}",
        "output": output,
        "metadata": {
            "status": "completed",
            "name": skill.name,
            "dir": str(skill.path),
        },
    }
