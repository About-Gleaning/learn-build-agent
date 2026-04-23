from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..adapters.llm.client import create_chat_completion
from ..config.logging_setup import build_log_extra, sanitize_log_text
from ..config.settings import ResolvedLLMConfig, resolve_llm_config
from ..core.message import Message, append_text_part, create_message, get_message_text
from ..slash_commands.parser import parse_slash_command
from ..slash_commands.registry import get_slash_command
from .session_hooks import SessionHook
from .workspace import build_session_storage_name, get_workspace

logger = logging.getLogger(__name__)

ARTIFACT_INGEST_AGENT = "artifact_ingest"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
INGEST_PROMPT_PATH = PROMPTS_DIR / "artifact_ingest.txt"

SQL_KEYWORDS = ["sql", "entity", "mapper", "dto", "query", "repository", "dao", "schema", "migration", "table", "insert", "update", "delete"]
JSON_KEYWORDS = ["api", "client", "request", "response", "fetch", "http", "service", "interface", "rpc"]
OTHER_KEYWORDS = ["config", "setting", "constant", "enum"]
DEFAULT_CONSTRAINTS = ["不得猜测表结构", "调用 write_file/edit_file 前必须声明 related_artifacts"]


class TaskArtifactError(RuntimeError):
    """任务工件处理失败；该错误会终止当前 session，避免缺失权威资料时继续编码。"""


def _is_registered_slash_command(user_input: str) -> bool:
    parsed = parse_slash_command(user_input)
    return parsed is not None and get_slash_command(parsed.name) is not None


def get_artifact_session_dir(session_id: str) -> Path:
    session_name = build_session_storage_name(session_id)
    return (get_workspace().workspace_home / "artifacts" / session_name).resolve()


def get_artifacts_dir(session_id: str) -> Path:
    return (get_artifact_session_dir(session_id) / "artifacts").resolve()


def get_task_brief_path(session_id: str) -> Path:
    return (get_artifact_session_dir(session_id) / "task_brief.md").resolve()


def get_task_facts_path(session_id: str) -> Path:
    return (get_artifact_session_dir(session_id) / "task_facts.json").resolve()


def _safe_filename(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("._-")
    return normalized or fallback


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise TaskArtifactError(f"artifact_ingest 输出不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise TaskArtifactError("artifact_ingest 输出必须是 JSON object")
    return data


def _infer_keywords(filename: str, raw_keywords: Any = None) -> list[str]:
    if isinstance(raw_keywords, list):
        keywords = [str(item).strip().lower() for item in raw_keywords if str(item).strip()]
        if keywords:
            return list(dict.fromkeys(keywords))
    suffix = Path(filename).suffix.lower()
    if suffix == ".sql":
        return SQL_KEYWORDS
    if suffix == ".json":
        return JSON_KEYWORDS
    return OTHER_KEYWORDS


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def _read_facts(session_id: str) -> dict[str, Any]:
    path = get_task_facts_path(session_id)
    if not path.exists():
        return {"artifacts": [], "constraints": list(DEFAULT_CONSTRAINTS)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("task_artifacts.facts_read_failed path=%s", path)
        return {"artifacts": [], "constraints": list(DEFAULT_CONSTRAINTS)}
    if not isinstance(data, dict):
        return {"artifacts": [], "constraints": list(DEFAULT_CONSTRAINTS)}
    artifacts = data.get("artifacts")
    constraints = data.get("constraints")
    return {
        "artifacts": artifacts if isinstance(artifacts, list) else [],
        "constraints": constraints if isinstance(constraints, list) else list(DEFAULT_CONSTRAINTS),
    }


def load_task_facts(session_id: str) -> dict[str, Any]:
    return _read_facts(session_id)


def _write_facts(session_id: str, facts: dict[str, Any]) -> None:
    path = get_task_facts_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")


def _artifact_note(filename: str, note: str) -> str:
    if note.strip():
        return note.strip()
    suffix = Path(filename).suffix.lower()
    if suffix == ".sql":
        return f"{filename} 权威 SQL/表结构资料，涉及相关数据读写时必须参照"
    if suffix == ".json":
        return f"{filename} 权威 JSON/API 资料，涉及接口、请求或响应时必须参照"
    return f"{filename} 权威配置/参考资料，涉及相关逻辑时必须参照"


def _normalize_artifact_item(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content", "")).strip()
    if not content:
        return None
    filename = _safe_filename(str(raw.get("file", "") or raw.get("filename", "")), f"artifact_{_timestamp()}.txt")
    suffix = Path(filename).suffix
    artifact_type = str(raw.get("type", "")).strip().lower()
    if not suffix:
        if artifact_type == "sql" or "create table" in content.lower():
            filename = f"{filename}.sql"
        elif artifact_type == "json":
            filename = f"{filename}.json"
        elif artifact_type == "csv":
            filename = f"{filename}.csv"
        else:
            filename = f"{filename}.txt"
    return {
        "file": filename,
        "content": content,
        "note": str(raw.get("note", "")).strip(),
        "keywords": json.dumps(raw.get("keywords", []), ensure_ascii=False),
    }


def _run_ingest_agent(session_id: str, user_input: str, llm_config: ResolvedLLMConfig | None = None) -> str:
    prompt = INGEST_PROMPT_PATH.read_text(encoding="utf-8").strip()
    runtime = llm_config or resolve_llm_config("build")
    system_message = create_message("system", session_id=session_id)
    append_text_part(system_message, prompt)
    user_message = create_message("user", session_id=session_id)
    append_text_part(user_message, user_input)
    response = create_chat_completion(
        messages=[system_message, user_message],
        tools=[],
        llm_config=runtime,
        agent=ARTIFACT_INGEST_AGENT,
    )
    text = get_message_text(response)
    if response.get("info", {}).get("status") != "completed":
        raise TaskArtifactError("artifact_ingest agent 未成功完成")
    return text


def should_ingest_user_input(user_input: str, user_message: Message | None = None) -> bool:
    """判断本轮用户输入是否需要交给 artifact_ingest。

    这里只过滤运行时控制输入；普通自然语言仍交给专用 agent 判断，避免漏掉口述资料。
    """
    normalized_input = (user_input or "").strip()
    if not normalized_input:
        return False
    # slash command 是运行时控制命令，不属于用户提供的权威资料来源。
    if _is_registered_slash_command(normalized_input):
        return False
    message = user_message if isinstance(user_message, dict) else {}
    for part in message.get("parts", []) if isinstance(message, dict) else []:
        meta = part.get("meta") if isinstance(part, dict) and isinstance(part.get("meta"), dict) else {}
        if bool(meta.get("slash_command")) or bool(meta.get("synthetic")):
            return False
        display_text = str(meta.get("display_text", "")).strip()
        if display_text.startswith("/"):
            return False
    return True


def persist_ingest_result(session_id: str, response_text: str) -> bool:
    """解析 artifact_ingest 输出并落盘。返回是否产生或更新了工件。"""
    try:
        result = _extract_json_object(response_text)
        raw_artifacts = result.get("artifacts", [])
        if not isinstance(raw_artifacts, list):
            raise TaskArtifactError("artifact_ingest 输出 artifacts 必须是数组")
        artifacts = [_normalize_artifact_item(item) for item in raw_artifacts]
        artifacts = [item for item in artifacts if item is not None]
        if not artifacts:
            return False

        session_dir = get_artifact_session_dir(session_id)
        artifacts_dir = get_artifacts_dir(session_id)
        history_dir = artifacts_dir / ".history"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        history_dir.mkdir(parents=True, exist_ok=True)

        facts = _read_facts(session_id)
        fact_by_file = {
            str(item.get("file", "")).strip(): dict(item)
            for item in facts.get("artifacts", [])
            if isinstance(item, dict) and str(item.get("file", "")).strip()
        }
        changed = False
        for item in artifacts:
            filename = item["file"]
            content = item["content"]
            target = artifacts_dir / filename
            if target.exists():
                old_content = target.read_text(encoding="utf-8")
                if old_content == content:
                    continue
                history_target = history_dir / f"{filename}.{_timestamp()}"
                history_target.parent.mkdir(parents=True, exist_ok=True)
                target.replace(history_target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            note = _artifact_note(filename, item["note"])
            try:
                raw_keywords = json.loads(item.get("keywords", "[]"))
            except json.JSONDecodeError:
                raw_keywords = []
            fact_by_file[filename] = {
                "file": filename,
                "note": note,
                "triggers": {"keywords": _infer_keywords(filename, raw_keywords)},
                "hash": _hash_text(content),
                "version": _timestamp(),
            }
            changed = True

        if not changed:
            return False

        constraints = facts.get("constraints") if isinstance(facts.get("constraints"), list) else []
        merged_constraints = list(dict.fromkeys([*DEFAULT_CONSTRAINTS, *(str(item) for item in constraints if str(item).strip())]))
        next_facts = {
            "artifacts": sorted(fact_by_file.values(), key=lambda item: str(item.get("file", ""))),
            "constraints": merged_constraints,
        }
        _write_facts(session_id, next_facts)
        _write_task_brief(session_id, result, next_facts)
        logger.info(
            "task_artifacts.ingested session_id=%s artifact_count=%s path=%s",
            session_id,
            len(artifacts),
            sanitize_log_text(str(session_dir)),
            extra=build_log_extra(agent=ARTIFACT_INGEST_AGENT, model=""),
        )
        return True
    except TaskArtifactError:
        raise
    except Exception as exc:
        raise TaskArtifactError(f"任务工件落盘失败：{type(exc).__name__}: {exc}") from exc


def ingest_user_input(session_id: str, user_input: str, llm_config: ResolvedLLMConfig | None = None) -> bool:
    """识别并落盘本轮用户输入中的权威资料。返回是否产生或更新了工件。"""
    response_text = _run_ingest_agent(session_id, user_input, llm_config=llm_config)
    return persist_ingest_result(session_id, response_text)


def _write_task_brief(session_id: str, ingest_result: dict[str, Any], facts: dict[str, Any]) -> None:
    objective = str(ingest_result.get("task_goal", "") or ingest_result.get("goal", "")).strip() or "未明确"
    lines = [
        "## 任务目标",
        objective,
        "",
        "## 关键约束",
    ]
    for constraint in facts.get("constraints", []):
        text = str(constraint).strip()
        if text:
            lines.append(f"- {text}")
    lines.extend(["", "## 权威资料索引"])
    for artifact in facts.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        file = str(artifact.get("file", "")).strip()
        note = str(artifact.get("note", "")).strip()
        if file:
            lines.append(f"- {file}: {note}")
    path = get_task_brief_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def render_persistent_context(session_id: str) -> str:
    brief_path = get_task_brief_path(session_id)
    facts = _read_facts(session_id)
    if not brief_path.exists() and not facts.get("artifacts"):
        return ""
    parts: list[str] = ["[PERSISTENT CONTEXT - DO NOT SUMMARIZE OR COMPRESS]"]
    if brief_path.exists():
        try:
            brief = brief_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("task_artifacts.brief_read_failed path=%s error=%s", brief_path, exc)
            brief = ""
        if brief:
            parts.extend(["## 任务概览", brief])
    artifacts = [item for item in facts.get("artifacts", []) if isinstance(item, dict)]
    if artifacts:
        parts.append("## 可用权威资料")
        for item in artifacts:
            file = str(item.get("file", "")).strip()
            note = str(item.get("note", "")).strip()
            if file:
                parts.append(f"- {file}：{note}")
    constraints = [str(item).strip() for item in facts.get("constraints", []) if str(item).strip()]
    if constraints:
        parts.append("## 强制约束")
        parts.append("- 调用 write_file/edit_file 前必须在 related_artifacts 中声明本次依赖的权威资料文件名")
        parts.append("- 可通过 list_artifacts 查看所有可用文件")
        for item in constraints:
            parts.append(f"- {item}")
    parts.append("[/PERSISTENT CONTEXT]")
    return "\n".join(parts).strip()


class TaskArtifactSessionHook(SessionHook):
    def __init__(self) -> None:
        super().__init__(
            name="task_artifact_ingestion",
            fail_fast=True,
            order=900,
            agent_kinds={"primary"},
        )

    def should_run(self, ctx: dict[str, Any]) -> bool:
        return super().should_run(ctx) and str(ctx.get("agent_kind", "")).strip().lower() == "primary"

    def before_session(self, ctx: dict[str, Any]) -> None:
        if bool(ctx.get("stream")):
            return
        session_id = str(ctx.get("session_id", "")).strip()
        user_input = str(ctx.get("user_input", "")).strip()
        if not session_id or not user_input:
            return
        user_message = ctx.get("user_message") if isinstance(ctx.get("user_message"), dict) else {}
        if not should_ingest_user_input(user_input, user_message if isinstance(user_message, dict) else None):
            return
        llm_config = ctx.get("llm_config")
        ingest_user_input(
            session_id,
            user_input,
            llm_config=llm_config if isinstance(llm_config, ResolvedLLMConfig) else None,
        )

    def after_session(self, ctx: dict[str, Any], message: dict[str, Any]) -> None:
        return

    def on_error(self, ctx: dict[str, Any], error: Exception, normalized_error: dict[str, str]) -> None:
        return
