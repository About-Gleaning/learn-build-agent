from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# 用来匹配最外层 frontmatter：
# ---
# key: value
# ---
# body...
FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

# 用来匹配最简单的 key: value 行
KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(.*?)\s*$")


@dataclass
class Skill:
    """
    表示一个 skill 的运行时对象。

    字段说明：
    - name: skill 名称
    - description: skill 简短描述，供模型做初步路由
    - path: skill 所在目录
    - skill_md_path: SKILL.md 文件路径
    - content: 完整 SKILL.md 内容，默认懒加载
    - metadata: 从 frontmatter 中解析出的元数据
    """

    name: str
    description: str
    path: Path
    skill_md_path: Path
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def scripts_dir(self) -> Path:
        """
        返回当前 skill 的 scripts 目录路径。

        约定：
        - 如果 skill 带有可执行脚本，则统一放在 scripts/ 目录下
        - 这里只返回路径，不检查目录是否真实存在
        """
        return self.path / "scripts"

    def to_brief_dict(self) -> Dict[str, Any]:
        """
        返回 skill 的轻量信息。

        这个方法的用途是：
        - 启动时先把 skill 的 name / description / path 提供给模型
        - 避免一开始就把完整 SKILL.md 全部塞进上下文
        - 命中后再加载全文，实现按需披露（progressive disclosure）
        """
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
        }

    def load_full_content(self) -> str:
        """
        懒加载并返回完整的 SKILL.md 内容。

        行为：
        - 第一次调用时从磁盘读取内容
        - 后续调用直接复用 self.content，避免重复 I/O
        """
        if self.content is None:
            self.content = self.skill_md_path.read_text(encoding="utf-8")
        return self.content

    def list_local_files(self) -> List[str]:
        """
        列出 skill 目录下除 SKILL.md 外的其他文件。

        典型用途：
        - 告诉模型这个 skill 附带了哪些脚本或资源文件
        - 例如 scripts/run.sh、examples/demo.txt 等

        返回：
        - 相对于当前 skill 根目录的文件路径列表
        """
        files: List[str] = []
        for p in self.path.rglob("*"):
            if p.is_file() and p.name != "SKILL.md":
                files.append(str(p.relative_to(self.path)))
        return sorted(files)


class SkillParseError(Exception):
    """
    skill 解析异常。

    当前示例里还没有大规模使用这个异常，
    后续你可以在 frontmatter 非法、字段缺失等场景里抛出它。
    """
    pass


class SkillRegistry:
    """
    Skill 注册表。

    主要职责：
    1. 扫描 skills 根目录
    2. 解析每个 skill 的 SKILL.md
    3. 保存所有已发现的 skill
    4. 提供查找、列举、路由、上下文构建等能力
    """

    def __init__(self, skills_root: str | Path) -> None:
        """
        初始化 SkillRegistry。

        参数：
        - skills_root: skills 根目录路径，可以传字符串或 Path

        说明：
        - 会统一转成绝对路径
        - 初始化时不会立刻扫描，需要显式调用 discover()
        """
        self.skills_root = Path(skills_root).resolve()
        self.skills: List[Skill] = []

    def discover(self) -> List[Skill]:
        """
        扫描 skills 根目录，发现所有合法的 skill。

        规则：
        - 只扫描 skills_root 的一级子目录
        - 子目录中存在 SKILL.md 才视为一个 skill
        - 每个 skill 会被解析成 Skill 对象并保存到 self.skills

        返回：
        - 发现到的 Skill 对象列表

        异常：
        - 如果 skills 根目录不存在，抛出 FileNotFoundError
        """
        if not self.skills_root.exists():
            raise FileNotFoundError(f"skills root not found: {self.skills_root}")

        discovered: List[Skill] = []
        for entry in self.skills_root.iterdir():
            if not entry.is_dir():
                continue

            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue

            skill = self._parse_skill(entry, skill_md)
            discovered.append(skill)

        # 按名称排序，便于输出稳定
        self.skills = sorted(discovered, key=lambda s: s.name.lower())
        return self.skills

    def _parse_skill(self, skill_dir: Path, skill_md_path: Path) -> Skill:
        """
        解析单个 skill 目录，生成 Skill 对象。

        处理逻辑：
        1. 读取 SKILL.md 原文
        2. 解析 frontmatter 和正文
        3. 优先使用 frontmatter 中的 name / description
        4. 如果 description 缺失，则尝试从正文中提取第一条有意义的文本作为描述

        参数：
        - skill_dir: skill 根目录
        - skill_md_path: 该 skill 的 SKILL.md 路径

        返回：
        - Skill 对象
        """
        raw = skill_md_path.read_text(encoding="utf-8")
        metadata, body = parse_skill_markdown(raw)

        # 优先使用 frontmatter 中的 name，否则退化为目录名
        name = str(metadata.get("name") or skill_dir.name).strip()

        # 优先使用 frontmatter 中的 description
        description = str(metadata.get("description") or "").strip()

        if not description:
            # 如果没有 description，就从正文中找第一条“有意义的文本”
            description = extract_first_meaningful_line(body) or f"Skill at {skill_dir.name}"

        return Skill(
            name=name,
            description=description,
            path=skill_dir,
            skill_md_path=skill_md_path,
            metadata=metadata,
        )

    def get_skill(self, name: str) -> Optional[Skill]:
        """
        根据 skill 名称精确查找 skill。

        匹配规则：
        - 忽略大小写
        - 必须精确匹配名称

        参数：
        - name: 要查找的 skill 名称

        返回：
        - 找到时返回 Skill
        - 找不到时返回 None
        """
        target = name.strip().lower()
        for skill in self.skills:
            if skill.name.lower() == target:
                return skill
        return None

    def list_briefs(self) -> List[Dict[str, Any]]:
        """
        返回所有 skill 的轻量信息列表。

        常见用途：
        - 序列化后提供给模型做第一轮技能选择
        - 在调试时快速查看当前已加载的 skills
        """
        return [skill.to_brief_dict() for skill in self.skills]

    def route(self, user_input: str, top_k: int = 3) -> List[Skill]:
        """
        根据用户输入，做一个极简的技能路由。

        当前策略非常简单：
        - 把用户输入切词
        - 看 token 是否出现在 skill.name / skill.description 中
        - 按命中分数排序后返回 top_k

        评分规则：
        - token 出现在名称或描述中：+2
        - token 恰好等于 skill 名称：+4

        适合：
        - demo
        - 本地最小可用版本

        不适合：
        - 复杂语义理解
        - 同义词召回
        - 长尾表达匹配

        后续建议：
        - 替换成 embedding 检索
        - 再加一层 LLM rerank / judge

        参数：
        - user_input: 用户原始输入
        - top_k: 返回前几个候选 skill

        返回：
        - 命中的 Skill 列表，按相关性从高到低排序
        """
        query_tokens = tokenize(user_input)
        if not query_tokens:
            return []

        scored: List[tuple[int, Skill]] = []
        for skill in self.skills:
            hay = f"{skill.name} {skill.description}".lower()
            score = 0

            for token in query_tokens:
                if token in hay:
                    score += 2
                if token == skill.name.lower():
                    score += 4

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in scored[:top_k]]

    def build_brief_catalog_for_model(self) -> str:
        """
        构建给模型使用的“轻量 skill 目录”。

        特点：
        - 只包含 name / description / path
        - 不包含完整 SKILL.md 正文
        - 用于第一阶段技能筛选

        返回：
        - JSON 字符串，方便直接拼进 prompt
        """
        items = [skill.to_brief_dict() for skill in self.skills]
        return json.dumps(items, ensure_ascii=False, indent=2)

    def build_skill_context(self, skill_names: List[str]) -> str:
        """
        根据 skill 名称列表，构建完整的 skill 上下文文本。

        用途：
        - 当模型已经选中某些 skill 后
        - 再把这些 skill 的完整 SKILL.md 内容注入到下一轮上下文中
        - 同时附带该 skill 目录中的其他文件清单，便于后续进一步读取或执行

        参数：
        - skill_names: 要加载的 skill 名称列表

        返回：
        - 拼接后的大文本，可直接注入模型上下文
        """
        sections: List[str] = []

        for name in skill_names:
            skill = self.get_skill(name)
            if not skill:
                continue

            full_text = skill.load_full_content()
            files = skill.list_local_files()

            section = [
                f"# Skill: {skill.name}",
                f"Path: {skill.path}",
                "",
                "## Full SKILL.md",
                full_text,
                "",
                "## Additional Local Files",
                json.dumps(files, ensure_ascii=False, indent=2),
            ]
            sections.append("\n".join(section))

        return "\n\n".join(sections)


def parse_skill_markdown(raw: str) -> tuple[Dict[str, Any], str]:
    """
    解析 SKILL.md 的 frontmatter 和正文。

    当前支持的格式是最简单版本：

    ---
    name: git_helper
    description: help with git operations
    ---
    正文...

    返回：
    - metadata: dict，frontmatter 里的键值对
    - body: str，去掉 frontmatter 后的正文

    注意：
    - 这里不是完整 YAML 解析器
    - 只适合简单的 key: value 场景
    - 如果后续要支持数组、嵌套对象、多行文本，建议改成 PyYAML
    """
    m = FRONTMATTER_RE.match(raw)
    if not m:
        # 没有 frontmatter 时，metadata 为空，正文就是原始文本
        return {}, raw

    frontmatter_text = m.group(1)
    body = m.group(2)

    metadata: Dict[str, Any] = {}
    for line in frontmatter_text.splitlines():
        line = line.strip()

        # 跳过空行和注释行
        if not line or line.startswith("#"):
            continue

        kv = KEY_VALUE_RE.match(line)
        if not kv:
            continue

        key, value = kv.group(1), kv.group(2)
        metadata[key] = strip_quotes(value)

    return metadata, body


def strip_quotes(value: str) -> str:
    """
    去掉字符串首尾的单引号或双引号。

    例如：
    - '"hello"' -> 'hello'
    - "'hello'" -> 'hello'
    - "hello" -> "hello"

    说明：
    - 这里只处理最外层一对引号
    - 不处理复杂转义
    """
    value = value.strip()
    if len(value) >= 2 and (
        (value[0] == '"' and value[-1] == '"')
        or (value[0] == "'" and value[-1] == "'")
    ):
        return value[1:-1]
    return value


def extract_first_meaningful_line(text: str) -> Optional[str]:
    """
    从正文中提取第一条“有意义的文本”。

    过滤规则：
    - 跳过空行
    - 跳过 Markdown 标题行（以 # 开头）

    用途：
    - 当前缺少 description 时，用它来兜底生成描述

    返回：
    - 找到则返回该行文本
    - 否则返回 None
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        return line
    return None


def tokenize(text: str) -> List[str]:
    """
    对输入文本做一个非常轻量的切词。

    规则：
    - 按非字母、非数字、非下划线、非连字符、非中文字符进行切分
    - 统一转小写
    - 过滤掉长度小于 2 的 token

    适用场景：
    - demo 级别的关键词路由

    局限性：
    - 不是真正的中文分词
    - 对复杂自然语言理解能力有限
    - 英文短词、缩写可能会被过滤掉

    返回：
    - token 列表
    """
    parts = re.split(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", text.lower())
    return [p for p in parts if len(p) >= 2]