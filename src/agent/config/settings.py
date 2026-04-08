import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from ..runtime.workspace import get_workspace
from ..slash_commands import list_visible_slash_commands

load_dotenv()

MainAgentMode = Literal["build", "plan"]
LLMApiMode = Literal["responses", "chat_completions"]

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LLM_PROMPT = os.getenv("LOG_LLM_PROMPT", "false").strip().lower() in {"1", "true", "yes", "on"}
LOG_LLM_PROMPT_LIMIT = max(200, int(os.getenv("LOG_LLM_PROMPT_LIMIT", "2000")))
LLM_CONFIG_PATH = Path(
    os.getenv(
        "LLM_CONFIG_PATH",
        str(Path(__file__).resolve().with_name("llm_runtime.json")),
    )
).resolve()
PROJECT_RUNTIME_CONFIG_PATH = Path(
    os.getenv(
        "PROJECT_RUNTIME_CONFIG_PATH",
        str(Path(__file__).resolve().with_name("project_runtime.json")),
    )
).resolve()

DEFAULT_TOOL_RESULT_PRUNE_ENABLED = True
DEFAULT_TOOL_RESULT_KEEP_RECENT = 3
DEFAULT_TOOL_RESULT_PRUNE_MIN_CHARS = 100
DEFAULT_SUMMARY_TRIGGER_THRESHOLD = 50000
DEFAULT_SUMMARY_MAX_TOKENS = 2000
DEFAULT_TOOL_OUTPUT_MAX_LINES = 2000
DEFAULT_TOOL_OUTPUT_MAX_BYTES = 50 * 1024
DEFAULT_FILE_EXTRACTION_ALLOWED_EXTENSIONS = (".pdf",)
DEFAULT_FILE_EXTRACTION_CLEANUP_MODE = "async_delete"
DEFAULT_AGENT_LOOP_MAX_ROUNDS = 8
DEFAULT_SUBAGENT_LOOP_MAX_ROUNDS = 15
DEFAULT_LOG_TRUNCATE_ENABLED = False
DEFAULT_LOG_TRUNCATE_LIMIT = 500
DEFAULT_SESSION_MEMORY_TRIM_ENABLED = True
DEFAULT_SESSION_MEMORY_MAX_MESSAGES = 24
DEFAULT_LSP_ENABLED = True
DEFAULT_LSP_IDE_ENABLED = False
DEFAULT_LSP_STARTUP_MODE = "on_demand"
DEFAULT_LSP_SERVER_IDLE_TTL_SECONDS = 900
DEFAULT_LSP_REQUEST_TIMEOUT_MS = 5000
DEFAULT_LSP_MAX_DIAGNOSTICS = 20
DEFAULT_LSP_MAX_CHARS = 4000
DEFAULT_LSP_INCLUDE_SEVERITY = ("error", "warning")
DEFAULT_LSP_STRICT_UNAVAILABLE = False
DEFAULT_LSP_DIAGNOSTICS_STABLE_WINDOW_MS = 800
DEFAULT_LSP_DIAGNOSTICS_MAX_WAIT_ROUNDS = 4
DEFAULT_LSP_JAVA_DEBUG_OBSERVATION_ENABLED = False
DEFAULT_JAVA_LSP_COMMAND = (
    "/usr/bin/env",
    "JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home",
    "jdtls",
)
DEFAULT_JAVA_FILE_EXTENSIONS = (".java",)
DEFAULT_JAVA_WORKSPACE_MARKERS = ("pom.xml", "build.gradle", "settings.gradle")
DEFAULT_PYTHON_LSP_COMMAND = ("pylsp",)
DEFAULT_PYTHON_FILE_EXTENSIONS = (".py",)
DEFAULT_PYTHON_WORKSPACE_MARKERS = ("pyproject.toml", "setup.py", "requirements.txt", "setup.cfg")


@dataclass(frozen=True)
class LLMDefaultsSettings:
    max_tokens: int


@dataclass(frozen=True)
class ModelSettings:
    max_tokens: int | None = None


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    vendor: str
    base_url: str
    default_model: str
    models: dict[str, ModelSettings]
    api_key_env: str
    timeout_seconds: float
    api_mode: LLMApiMode

    @property
    def model_names(self) -> tuple[str, ...]:
        return tuple(self.models.keys())


@dataclass(frozen=True)
class AgentDefaultSettings:
    provider: str
    model: str


@dataclass(frozen=True)
class RuntimeSettings:
    defaults: LLMDefaultsSettings
    providers: dict[str, ProviderSettings]
    agent_defaults: dict[MainAgentMode, AgentDefaultSettings]


@dataclass(frozen=True)
class CompactionSettings:
    tool_result_prune_enabled: bool = DEFAULT_TOOL_RESULT_PRUNE_ENABLED
    tool_result_keep_recent: int = DEFAULT_TOOL_RESULT_KEEP_RECENT
    tool_result_prune_min_chars: int = DEFAULT_TOOL_RESULT_PRUNE_MIN_CHARS
    summary_trigger_threshold: int = DEFAULT_SUMMARY_TRIGGER_THRESHOLD
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS
    tool_output_max_lines: int = DEFAULT_TOOL_OUTPUT_MAX_LINES
    tool_output_max_bytes: int = DEFAULT_TOOL_OUTPUT_MAX_BYTES


@dataclass(frozen=True)
class ProjectRuntimeSettings:
    compaction_default: CompactionSettings
    compaction_vendors: dict[str, CompactionSettings]
    file_extraction_default: "FileExtractionSettings"
    file_extraction_vendors: dict[str, "FileExtractionSettings"]
    agent_loop: "AgentLoopSettings"
    subagent_loop: "SubagentLoopSettings"
    logging: "LoggingSettings"
    session_memory: "SessionMemorySettings"
    lsp: "LspSettings"
    mcp: "McpSettings"


@dataclass(frozen=True)
class FileExtractionSettings:
    allowed_extensions: tuple[str, ...] = DEFAULT_FILE_EXTRACTION_ALLOWED_EXTENSIONS
    cleanup_mode: str = DEFAULT_FILE_EXTRACTION_CLEANUP_MODE


@dataclass(frozen=True)
class AgentLoopSettings:
    max_rounds: int = DEFAULT_AGENT_LOOP_MAX_ROUNDS


@dataclass(frozen=True)
class SubagentLoopSettings:
    max_rounds: int = DEFAULT_SUBAGENT_LOOP_MAX_ROUNDS


@dataclass(frozen=True)
class LoggingSettings:
    truncate_enabled: bool = DEFAULT_LOG_TRUNCATE_ENABLED
    truncate_limit: int = DEFAULT_LOG_TRUNCATE_LIMIT


@dataclass(frozen=True)
class SessionMemorySettings:
    trim_enabled: bool = DEFAULT_SESSION_MEMORY_TRIM_ENABLED
    max_messages: int = DEFAULT_SESSION_MEMORY_MAX_MESSAGES


@dataclass(frozen=True)
class LspIdeSettings:
    transport: str = ""
    endpoint: str = ""


@dataclass(frozen=True)
class LspLanguageSettings:
    enabled: bool
    command: tuple[str, ...]
    file_extensions: tuple[str, ...]
    workspace_markers: tuple[str, ...]
    init_options: dict[str, Any]
    maven_local_repository: str = ""


@dataclass(frozen=True)
class LspSettings:
    enabled: bool = DEFAULT_LSP_ENABLED
    ide_enabled: bool = DEFAULT_LSP_IDE_ENABLED
    startup_mode: str = DEFAULT_LSP_STARTUP_MODE
    server_idle_ttl_seconds: int = DEFAULT_LSP_SERVER_IDLE_TTL_SECONDS
    request_timeout_ms: int = DEFAULT_LSP_REQUEST_TIMEOUT_MS
    max_diagnostics: int = DEFAULT_LSP_MAX_DIAGNOSTICS
    max_chars: int = DEFAULT_LSP_MAX_CHARS
    include_severity: tuple[str, ...] = DEFAULT_LSP_INCLUDE_SEVERITY
    strict_unavailable: bool = DEFAULT_LSP_STRICT_UNAVAILABLE
    diagnostics_stable_window_ms: int = DEFAULT_LSP_DIAGNOSTICS_STABLE_WINDOW_MS
    diagnostics_max_wait_rounds: int = DEFAULT_LSP_DIAGNOSTICS_MAX_WAIT_ROUNDS
    java_debug_observation_enabled: bool = DEFAULT_LSP_JAVA_DEBUG_OBSERVATION_ENABLED
    languages: dict[str, LspLanguageSettings] | None = None
    ide: LspIdeSettings = LspIdeSettings()


@dataclass(frozen=True)
class McpServerSettings:
    enabled: bool = True
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] | None = None
    expose_to_plan: bool = True
    discovery_timeout_ms: int = 10000
    call_timeout_ms: int = 120000


@dataclass(frozen=True)
class McpSettings:
    enabled: bool = False
    discovery_timeout_ms: int = 10000
    call_timeout_ms: int = 120000
    servers: dict[str, McpServerSettings] | None = None


@dataclass(frozen=True)
class ResolvedLLMConfig:
    agent: MainAgentMode
    provider: str
    vendor: str
    model: str
    max_tokens: int
    api_mode: LLMApiMode
    base_url: str
    api_key: str
    timeout_seconds: float


def _load_runtime_payload() -> dict[str, Any]:
    if not LLM_CONFIG_PATH.exists():
        raise ValueError(f"未找到 LLM 配置文件: {LLM_CONFIG_PATH}")

    try:
        payload = json.loads(LLM_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 配置文件 JSON 格式非法: {LLM_CONFIG_PATH}") from exc

    if not isinstance(payload, dict):
        raise ValueError("LLM 配置文件顶层必须是 JSON 对象。")
    return payload


def _parse_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} 必须是布尔值。")


def _parse_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 必须是大于等于 0 的整数。")
    if value < 0:
        raise ValueError(f"{field_name} 必须是大于等于 0 的整数。")
    return value


def _parse_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 必须是大于 0 的整数。")
    if value <= 0:
        raise ValueError(f"{field_name} 必须是大于 0 的整数。")
    return value


def _load_project_runtime_payload() -> dict[str, Any]:
    if not PROJECT_RUNTIME_CONFIG_PATH.exists():
        return {}

    try:
        payload = json.loads(_strip_json_comments(PROJECT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"项目运行时配置文件 JSON 格式非法: {PROJECT_RUNTIME_CONFIG_PATH}") from exc

    if not isinstance(payload, dict):
        raise ValueError("项目运行时配置文件顶层必须是 JSON 对象。")
    return payload


def _strip_json_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    in_line_comment = False
    in_block_comment = False
    index = 0

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append(char)
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "\"":
                in_string = False
            index += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue

        result.append(char)
        if char == "\"":
            in_string = True
        index += 1

    return "".join(result)


def _load_compaction_settings(raw_compaction: Any) -> CompactionSettings:
    if raw_compaction is None:
        return CompactionSettings()
    if not isinstance(raw_compaction, dict):
        raise ValueError("project_runtime.compaction 必须是对象。")
    return _parse_compaction_settings(raw_compaction)


def _parse_optional_bool(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    return _parse_bool(value, field_name=field_name)


def _parse_optional_non_negative_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _parse_non_negative_int(value, field_name=field_name)


def _parse_compaction_patch(raw_value: Any, *, field_prefix: str) -> dict[str, bool | int]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError(f"{field_prefix} 必须是对象。")
    patch: dict[str, bool | int] = {}

    tool_result_prune_enabled = _parse_optional_bool(
        raw_value.get("tool_result_prune_enabled"),
        field_name=f"{field_prefix}.tool_result_prune_enabled",
    )
    if tool_result_prune_enabled is not None:
        patch["tool_result_prune_enabled"] = tool_result_prune_enabled

    for field_name in (
        "tool_result_keep_recent",
        "tool_result_prune_min_chars",
        "summary_trigger_threshold",
        "summary_max_tokens",
        "tool_output_max_lines",
        "tool_output_max_bytes",
    ):
        parsed = _parse_optional_non_negative_int(raw_value.get(field_name), field_name=f"{field_prefix}.{field_name}")
        if parsed is not None:
            patch[field_name] = parsed
    return patch


def _merge_compaction_settings(base: CompactionSettings, patch: dict[str, bool | int]) -> CompactionSettings:
    return CompactionSettings(
        tool_result_prune_enabled=bool(patch.get("tool_result_prune_enabled", base.tool_result_prune_enabled)),
        tool_result_keep_recent=int(patch.get("tool_result_keep_recent", base.tool_result_keep_recent)),
        tool_result_prune_min_chars=int(patch.get("tool_result_prune_min_chars", base.tool_result_prune_min_chars)),
        summary_trigger_threshold=int(patch.get("summary_trigger_threshold", base.summary_trigger_threshold)),
        summary_max_tokens=int(patch.get("summary_max_tokens", base.summary_max_tokens)),
        tool_output_max_lines=int(patch.get("tool_output_max_lines", base.tool_output_max_lines)),
        tool_output_max_bytes=int(patch.get("tool_output_max_bytes", base.tool_output_max_bytes)),
    )


def _parse_compaction_settings(raw_value: dict[str, Any]) -> CompactionSettings:
    return _merge_compaction_settings(CompactionSettings(), _parse_compaction_patch(raw_value, field_prefix="compaction"))


def _load_project_compaction_settings(raw_compaction: Any) -> tuple[CompactionSettings, dict[str, CompactionSettings]]:
    if raw_compaction is None:
        return CompactionSettings(), {}
    if not isinstance(raw_compaction, dict):
        raise ValueError("project_runtime.compaction 必须是对象。")

    # 兼容旧结构：直接把 compaction 视作 default 配置。
    has_nested_structure = "default" in raw_compaction or "vendors" in raw_compaction
    if not has_nested_structure:
        default_settings = _parse_compaction_settings(raw_compaction)
        return default_settings, {}

    default_raw = raw_compaction.get("default")
    vendors_raw = raw_compaction.get("vendors", {})
    default_patch = _parse_compaction_patch(default_raw, field_prefix="compaction.default")
    default_settings = _merge_compaction_settings(CompactionSettings(), default_patch)

    if not isinstance(vendors_raw, dict):
        raise ValueError("compaction.vendors 必须是对象。")

    vendor_settings: dict[str, CompactionSettings] = {}
    for raw_vendor, raw_value in vendors_raw.items():
        vendor = str(raw_vendor).strip().lower()
        if not vendor:
            raise ValueError("compaction.vendors 中的厂商名称不能为空。")
        vendor_patch = _parse_compaction_patch(raw_value, field_prefix=f"compaction.vendors.{vendor}")
        vendor_settings[vendor] = _merge_compaction_settings(default_settings, vendor_patch)

    return default_settings, vendor_settings


def _normalize_file_extension(value: Any, *, field_name: str) -> str:
    text = str(value).strip().lower()
    if not text:
        raise ValueError(f"{field_name} 不能为空。")
    if not text.startswith("."):
        raise ValueError(f"{field_name} 必须以 '.' 开头。")
    return text


def _parse_file_extraction_patch(raw_value: Any, *, field_prefix: str) -> dict[str, Any]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError(f"{field_prefix} 必须是对象。")

    patch: dict[str, Any] = {}
    raw_extensions = raw_value.get("allowed_extensions")
    if raw_extensions is not None:
        if not isinstance(raw_extensions, list) or not raw_extensions:
            raise ValueError(f"{field_prefix}.allowed_extensions 必须是非空数组。")
        patch["allowed_extensions"] = tuple(
            _normalize_file_extension(item, field_name=f"{field_prefix}.allowed_extensions[{index}]")
            for index, item in enumerate(raw_extensions)
        )

    cleanup_mode = raw_value.get("cleanup_mode")
    if cleanup_mode is not None:
        normalized_cleanup_mode = str(cleanup_mode).strip().lower()
        if normalized_cleanup_mode != "async_delete":
            raise ValueError(f"{field_prefix}.cleanup_mode 目前仅支持 async_delete。")
        patch["cleanup_mode"] = normalized_cleanup_mode
    return patch


def _merge_file_extraction_settings(base: FileExtractionSettings, patch: dict[str, Any]) -> FileExtractionSettings:
    return FileExtractionSettings(
        allowed_extensions=tuple(patch.get("allowed_extensions", base.allowed_extensions)),
        cleanup_mode=str(patch.get("cleanup_mode", base.cleanup_mode)),
    )


def _load_project_file_extraction_settings(raw_file_extraction: Any) -> tuple[FileExtractionSettings, dict[str, FileExtractionSettings]]:
    if raw_file_extraction is None:
        return FileExtractionSettings(), {}
    if not isinstance(raw_file_extraction, dict):
        raise ValueError("project_runtime.file_extraction 必须是对象。")

    has_nested_structure = "default" in raw_file_extraction or "vendors" in raw_file_extraction
    if not has_nested_structure:
        default_settings = _merge_file_extraction_settings(
            FileExtractionSettings(),
            _parse_file_extraction_patch(raw_file_extraction, field_prefix="file_extraction"),
        )
        return default_settings, {}

    default_raw = raw_file_extraction.get("default")
    vendors_raw = raw_file_extraction.get("vendors", {})
    default_settings = _merge_file_extraction_settings(
        FileExtractionSettings(),
        _parse_file_extraction_patch(default_raw, field_prefix="file_extraction.default"),
    )

    if not isinstance(vendors_raw, dict):
        raise ValueError("file_extraction.vendors 必须是对象。")

    vendor_settings: dict[str, FileExtractionSettings] = {}
    for raw_vendor, raw_value in vendors_raw.items():
        vendor = str(raw_vendor).strip().lower()
        if not vendor:
            raise ValueError("file_extraction.vendors 中的厂商名称不能为空。")
        vendor_settings[vendor] = _merge_file_extraction_settings(
            default_settings,
            _parse_file_extraction_patch(raw_value, field_prefix=f"file_extraction.vendors.{vendor}"),
        )
    return default_settings, vendor_settings


def _load_project_agent_loop_settings(raw_agent_loop: Any) -> AgentLoopSettings:
    if raw_agent_loop is None:
        return AgentLoopSettings()
    if not isinstance(raw_agent_loop, dict):
        raise ValueError("project_runtime.agent_loop 必须是对象。")
    raw_max_rounds = raw_agent_loop.get("max_rounds", DEFAULT_AGENT_LOOP_MAX_ROUNDS)
    max_rounds = _parse_positive_int(raw_max_rounds, field_name="agent_loop.max_rounds")
    return AgentLoopSettings(max_rounds=max_rounds)


def _load_project_subagent_loop_settings(raw_subagent_loop: Any) -> SubagentLoopSettings:
    if raw_subagent_loop is None:
        return SubagentLoopSettings()
    if not isinstance(raw_subagent_loop, dict):
        raise ValueError("project_runtime.subagent_loop 必须是对象。")
    raw_max_rounds = raw_subagent_loop.get("max_rounds", DEFAULT_SUBAGENT_LOOP_MAX_ROUNDS)
    max_rounds = _parse_positive_int(raw_max_rounds, field_name="subagent_loop.max_rounds")
    return SubagentLoopSettings(max_rounds=max_rounds)


def _load_project_logging_settings(raw_logging: Any) -> LoggingSettings:
    if raw_logging is None:
        return LoggingSettings()
    if not isinstance(raw_logging, dict):
        raise ValueError("project_runtime.logging 必须是对象。")

    raw_truncate_enabled = raw_logging.get("truncate_enabled", DEFAULT_LOG_TRUNCATE_ENABLED)
    truncate_enabled = _parse_bool(raw_truncate_enabled, field_name="logging.truncate_enabled")

    raw_truncate_limit = raw_logging.get("truncate_limit", DEFAULT_LOG_TRUNCATE_LIMIT)
    truncate_limit = _parse_positive_int(raw_truncate_limit, field_name="logging.truncate_limit")
    return LoggingSettings(truncate_enabled=truncate_enabled, truncate_limit=truncate_limit)


def _load_project_session_memory_settings(raw_session_memory: Any) -> SessionMemorySettings:
    if raw_session_memory is None:
        return SessionMemorySettings()
    if not isinstance(raw_session_memory, dict):
        raise ValueError("project_runtime.session_memory 必须是对象。")

    raw_trim_enabled = raw_session_memory.get("trim_enabled", DEFAULT_SESSION_MEMORY_TRIM_ENABLED)
    trim_enabled = _parse_bool(raw_trim_enabled, field_name="session_memory.trim_enabled")

    raw_max_messages = raw_session_memory.get("max_messages", DEFAULT_SESSION_MEMORY_MAX_MESSAGES)
    max_messages = _parse_positive_int(raw_max_messages, field_name="session_memory.max_messages")
    return SessionMemorySettings(trim_enabled=trim_enabled, max_messages=max_messages)


def _parse_string_list(raw_value: Any, *, field_name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        raise ValueError(f"{field_name} 必须是数组。")
    items = tuple(str(item).strip() for item in raw_value if str(item).strip())
    if not allow_empty and not items:
        raise ValueError(f"{field_name} 不能为空。")
    return items


def _parse_string_mapping(raw_value: Any, *, field_name: str) -> dict[str, str]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError(f"{field_name} 必须是对象。")
    result: dict[str, str] = {}
    for raw_key, raw_item in raw_value.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError(f"{field_name} 的键不能为空。")
        if isinstance(raw_item, (dict, list, tuple, set)):
            raise ValueError(f"{field_name}.{key} 必须是字符串。")
        result[key] = str(raw_item)
    return result


def _load_lsp_language_settings(raw_value: Any, *, language: str) -> LspLanguageSettings:
    defaults = {
        "java": LspLanguageSettings(
            enabled=True,
            command=DEFAULT_JAVA_LSP_COMMAND,
            file_extensions=DEFAULT_JAVA_FILE_EXTENSIONS,
            workspace_markers=DEFAULT_JAVA_WORKSPACE_MARKERS,
            init_options={},
            maven_local_repository="",
        ),
        "python": LspLanguageSettings(
            enabled=True,
            command=DEFAULT_PYTHON_LSP_COMMAND,
            file_extensions=DEFAULT_PYTHON_FILE_EXTENSIONS,
            workspace_markers=DEFAULT_PYTHON_WORKSPACE_MARKERS,
            init_options={},
            maven_local_repository="",
        ),
        "typescript": LspLanguageSettings(
            enabled=True,
            command=("typescript-language-server", "--stdio"),
            file_extensions=(".ts", ".tsx", ".js", ".jsx"),
            workspace_markers=("tsconfig.json", "package.json"),
            init_options={},
            maven_local_repository="",
        ),
    }
    if language not in defaults:
        raise ValueError(f"未支持的 LSP 语言配置: {language}")
    base = defaults[language]
    if raw_value is None:
        return base
    if not isinstance(raw_value, dict):
        raise ValueError(f"lsp.languages.{language} 必须是对象。")
    enabled = _parse_bool(raw_value.get("enabled", base.enabled), field_name=f"lsp.languages.{language}.enabled")
    command = _parse_string_list(
        raw_value.get("command", list(base.command)),
        field_name=f"lsp.languages.{language}.command",
        allow_empty=not enabled,
    )
    file_extensions = tuple(
        _normalize_file_extension(item, field_name=f"lsp.languages.{language}.file_extensions[{index}]")
        for index, item in enumerate(raw_value.get("file_extensions", list(base.file_extensions)))
    )
    if not file_extensions:
        raise ValueError(f"lsp.languages.{language}.file_extensions 不能为空。")
    workspace_markers = _parse_string_list(
        raw_value.get("workspace_markers", list(base.workspace_markers)),
        field_name=f"lsp.languages.{language}.workspace_markers",
        allow_empty=True,
    )
    init_options = raw_value.get("init_options", base.init_options)
    if not isinstance(init_options, dict):
        raise ValueError(f"lsp.languages.{language}.init_options 必须是对象。")
    maven_local_repository = base.maven_local_repository
    if language == "java":
        if "maven_profiles" in raw_value:
            raise ValueError(
                "lsp.languages.java.maven_profiles 已废弃，当前仅支持自动探测 Maven profile，请删除该配置。"
            )
        raw_local_repository = raw_value.get("maven_local_repository", base.maven_local_repository)
        if raw_local_repository is None:
            raw_local_repository = ""
        if not isinstance(raw_local_repository, str):
            raise ValueError("lsp.languages.java.maven_local_repository 必须是字符串。")
        maven_local_repository = raw_local_repository.strip()
    return LspLanguageSettings(
        enabled=enabled,
        command=command,
        file_extensions=file_extensions,
        workspace_markers=workspace_markers,
        init_options=dict(init_options),
        maven_local_repository=maven_local_repository,
    )


def _load_project_lsp_settings(raw_lsp: Any) -> LspSettings:
    if raw_lsp is None:
        raw_lsp = {}
    if not isinstance(raw_lsp, dict):
        raise ValueError("project_runtime.lsp 必须是对象。")

    enabled = _parse_bool(raw_lsp.get("enabled", DEFAULT_LSP_ENABLED), field_name="lsp.enabled")
    ide_enabled = _parse_bool(raw_lsp.get("ide_enabled", DEFAULT_LSP_IDE_ENABLED), field_name="lsp.ide_enabled")
    startup_mode = str(raw_lsp.get("startup_mode", DEFAULT_LSP_STARTUP_MODE)).strip().lower()
    if startup_mode != "on_demand":
        raise ValueError("lsp.startup_mode 目前仅支持 on_demand。")
    server_idle_ttl_seconds = _parse_positive_int(
        raw_lsp.get("server_idle_ttl_seconds", DEFAULT_LSP_SERVER_IDLE_TTL_SECONDS),
        field_name="lsp.server_idle_ttl_seconds",
    )
    request_timeout_ms = _parse_positive_int(
        raw_lsp.get("request_timeout_ms", DEFAULT_LSP_REQUEST_TIMEOUT_MS),
        field_name="lsp.request_timeout_ms",
    )
    max_diagnostics = _parse_positive_int(
        raw_lsp.get("max_diagnostics", DEFAULT_LSP_MAX_DIAGNOSTICS),
        field_name="lsp.max_diagnostics",
    )
    max_chars = _parse_positive_int(raw_lsp.get("max_chars", DEFAULT_LSP_MAX_CHARS), field_name="lsp.max_chars")
    include_severity = tuple(
        str(item).strip().lower()
        for item in raw_lsp.get("include_severity", list(DEFAULT_LSP_INCLUDE_SEVERITY))
    )
    if not include_severity:
        raise ValueError("lsp.include_severity 不能为空。")
    valid_severity = {"error", "warning", "information", "hint"}
    invalid_severity = [item for item in include_severity if item not in valid_severity]
    if invalid_severity:
        raise ValueError(f"lsp.include_severity 包含非法等级: {', '.join(invalid_severity)}")
    strict_unavailable = _parse_bool(
        raw_lsp.get("strict_unavailable", DEFAULT_LSP_STRICT_UNAVAILABLE),
        field_name="lsp.strict_unavailable",
    )
    diagnostics_stable_window_ms = _parse_positive_int(
        raw_lsp.get("diagnostics_stable_window_ms", DEFAULT_LSP_DIAGNOSTICS_STABLE_WINDOW_MS),
        field_name="lsp.diagnostics_stable_window_ms",
    )
    diagnostics_max_wait_rounds = _parse_positive_int(
        raw_lsp.get("diagnostics_max_wait_rounds", DEFAULT_LSP_DIAGNOSTICS_MAX_WAIT_ROUNDS),
        field_name="lsp.diagnostics_max_wait_rounds",
    )
    java_debug_observation_enabled = _parse_bool(
        raw_lsp.get("java_debug_observation_enabled", DEFAULT_LSP_JAVA_DEBUG_OBSERVATION_ENABLED),
        field_name="lsp.java_debug_observation_enabled",
    )

    raw_languages = raw_lsp.get("languages", {})
    if not isinstance(raw_languages, dict):
        raise ValueError("lsp.languages 必须是对象。")
    languages = {
        "java": _load_lsp_language_settings(raw_languages.get("java"), language="java"),
        "python": _load_lsp_language_settings(raw_languages.get("python"), language="python"),
        "typescript": _load_lsp_language_settings(raw_languages.get("typescript"), language="typescript"),
    }

    raw_ide = raw_lsp.get("ide", {})
    if raw_ide is None:
        raw_ide = {}
    if not isinstance(raw_ide, dict):
        raise ValueError("lsp.ide 必须是对象。")
    return LspSettings(
        enabled=enabled,
        ide_enabled=ide_enabled,
        startup_mode=startup_mode,
        server_idle_ttl_seconds=server_idle_ttl_seconds,
        request_timeout_ms=request_timeout_ms,
        max_diagnostics=max_diagnostics,
        max_chars=max_chars,
        include_severity=include_severity,
        strict_unavailable=strict_unavailable,
        diagnostics_stable_window_ms=diagnostics_stable_window_ms,
        diagnostics_max_wait_rounds=diagnostics_max_wait_rounds,
        java_debug_observation_enabled=java_debug_observation_enabled,
        languages=languages,
        ide=LspIdeSettings(
            transport=str(raw_ide.get("transport", "")).strip(),
            endpoint=str(raw_ide.get("endpoint", "")).strip(),
        ),
    )


def _load_project_mcp_settings(raw_mcp: Any) -> McpSettings:
    if raw_mcp is None:
        raw_mcp = {}
    if not isinstance(raw_mcp, dict):
        raise ValueError("project_runtime.mcp 必须是对象。")

    enabled = _parse_bool(raw_mcp.get("enabled", False), field_name="mcp.enabled")
    discovery_timeout_ms = _parse_positive_int(
        raw_mcp.get("discovery_timeout_ms", 10000),
        field_name="mcp.discovery_timeout_ms",
    )
    call_timeout_ms = _parse_positive_int(
        raw_mcp.get("call_timeout_ms", 120000),
        field_name="mcp.call_timeout_ms",
    )
    raw_servers = raw_mcp.get("servers", {})
    if raw_servers is None:
        raw_servers = {}
    if not isinstance(raw_servers, dict):
        raise ValueError("mcp.servers 必须是对象。")

    servers: dict[str, McpServerSettings] = {}
    for raw_alias, raw_server in raw_servers.items():
        alias = str(raw_alias).strip()
        if not alias:
            raise ValueError("mcp.servers 的别名不能为空。")
        if not isinstance(raw_server, dict):
            raise ValueError(f"mcp.servers.{alias} 必须是对象。")
        server_enabled = _parse_bool(raw_server.get("enabled", True), field_name=f"mcp.servers.{alias}.enabled")
        transport = str(raw_server.get("transport", "stdio")).strip().lower()
        if transport not in {"stdio", "streamable_http"}:
            raise ValueError(f"mcp.servers.{alias}.transport 仅支持 stdio 或 streamable_http。")
        command = str(raw_server.get("command", "")).strip()
        url = str(raw_server.get("url", "")).strip()
        if server_enabled and transport == "stdio" and not command:
            raise ValueError(f"mcp.servers.{alias}.command 不能为空。")
        if server_enabled and transport == "streamable_http" and not url:
            raise ValueError(f"mcp.servers.{alias}.url 不能为空。")
        servers[alias] = McpServerSettings(
            enabled=server_enabled,
            transport=transport,
            command=command,
            args=_parse_string_list(raw_server.get("args", []), field_name=f"mcp.servers.{alias}.args", allow_empty=True),
            env=_parse_string_mapping(raw_server.get("env", {}), field_name=f"mcp.servers.{alias}.env"),
            cwd=str(raw_server.get("cwd", "")).strip(),
            url=url,
            headers=_parse_string_mapping(raw_server.get("headers", {}), field_name=f"mcp.servers.{alias}.headers"),
            expose_to_plan=_parse_bool(
                raw_server.get("expose_to_plan", True),
                field_name=f"mcp.servers.{alias}.expose_to_plan",
            ),
            discovery_timeout_ms=_parse_positive_int(
                raw_server.get("discovery_timeout_ms", discovery_timeout_ms),
                field_name=f"mcp.servers.{alias}.discovery_timeout_ms",
            ),
            call_timeout_ms=_parse_positive_int(
                raw_server.get("call_timeout_ms", call_timeout_ms),
                field_name=f"mcp.servers.{alias}.call_timeout_ms",
            ),
        )
    return McpSettings(
        enabled=enabled,
        discovery_timeout_ms=discovery_timeout_ms,
        call_timeout_ms=call_timeout_ms,
        servers=servers,
    )


def _load_provider_settings(raw_providers: Any) -> dict[str, ProviderSettings]:
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise ValueError("LLM 配置缺少 providers，且至少要配置一个厂商。")

    providers: dict[str, ProviderSettings] = {}
    for raw_name, raw_value in raw_providers.items():
        name = str(raw_name).strip().lower()
        if not name:
            raise ValueError("provider 名称不能为空。")
        if not isinstance(raw_value, dict):
            raise ValueError(f"provider '{name}' 配置必须是对象。")

        base_url = str(raw_value.get("base_url", "")).strip()
        vendor = str(raw_value.get("vendor", "")).strip().lower()
        api_key_env = str(raw_value.get("api_key_env", "")).strip()
        default_model = str(raw_value.get("default_model", "")).strip()
        raw_models = raw_value.get("models")
        raw_api_mode = str(raw_value.get("api_mode", "responses")).strip().lower()
        timeout_seconds_raw = raw_value.get("timeout_seconds", 60)
        if not base_url or not vendor or not api_key_env:
            raise ValueError(f"provider '{name}' 必须配置 vendor、base_url、api_key_env。")
        if raw_api_mode not in {"responses", "chat_completions"}:
            raise ValueError(f"provider '{name}'.api_mode 仅支持 responses 或 chat_completions。")
        if not isinstance(raw_models, dict) or not raw_models:
            raise ValueError(f"provider '{name}'.models 必须是非空对象。")
        models: dict[str, ModelSettings] = {}
        for raw_model_name, raw_model_value in raw_models.items():
            model_name = str(raw_model_name).strip()
            if not model_name:
                raise ValueError(f"provider '{name}'.models 中的模型名称不能为空。")
            if raw_model_value is not None and not isinstance(raw_model_value, dict):
                raise ValueError(f"provider '{name}'.models.{model_name} 必须是对象或 null。")
            raw_model_settings = raw_model_value or {}
            raw_model_max_tokens = raw_model_settings.get("max_tokens")
            model_max_tokens = None
            if raw_model_max_tokens is not None:
                model_max_tokens = _parse_positive_int(
                    raw_model_max_tokens,
                    field_name=f"provider '{name}'.models.{model_name}.max_tokens",
                )
            models[model_name] = ModelSettings(max_tokens=model_max_tokens)
        if not default_model:
            raise ValueError(f"provider '{name}'.default_model 不能为空。")
        if default_model not in models:
            raise ValueError(f"provider '{name}'.default_model '{default_model}' 未在 models 中定义。")
        try:
            timeout_seconds = float(timeout_seconds_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"provider '{name}'.timeout_seconds 必须是数字。") from exc
        if timeout_seconds <= 0:
            raise ValueError(f"provider '{name}'.timeout_seconds 必须大于 0。")

        providers[name] = ProviderSettings(
            name=name,
            vendor=vendor,
            base_url=base_url,
            default_model=default_model,
            models=models,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            api_mode=raw_api_mode,  # type: ignore[arg-type]
        )
    return providers


def _load_llm_defaults(raw_defaults: Any) -> LLMDefaultsSettings:
    if raw_defaults is None:
        raw_defaults = {}
    if not isinstance(raw_defaults, dict):
        raise ValueError("LLM 配置中的 defaults 必须是对象。")
    return LLMDefaultsSettings(
        max_tokens=_parse_positive_int(raw_defaults.get("max_tokens"), field_name="defaults.max_tokens"),
    )


def _load_agent_defaults(raw_agent_defaults: Any, providers: dict[str, ProviderSettings]) -> dict[MainAgentMode, AgentDefaultSettings]:
    if not isinstance(raw_agent_defaults, dict):
        raise ValueError("LLM 配置缺少 agent_defaults。")

    defaults: dict[MainAgentMode, AgentDefaultSettings] = {}
    for agent in ("build", "plan"):
        raw_value = raw_agent_defaults.get(agent)
        if not isinstance(raw_value, dict):
            raise ValueError(f"agent_defaults.{agent} 必须是对象。")
        provider = str(raw_value.get("provider", "")).strip().lower()
        model = str(raw_value.get("model", "")).strip()
        if provider not in providers:
            raise ValueError(f"agent_defaults.{agent}.provider '{provider}' 未在 providers 中定义。")
        if not model:
            raise ValueError(f"agent_defaults.{agent}.model 不能为空。")
        if model not in providers[provider].models:
            raise ValueError(f"agent_defaults.{agent}.model '{model}' 未在 provider '{provider}' 的 models 中定义。")
        defaults[agent] = AgentDefaultSettings(provider=provider, model=model)
    return defaults


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    payload = _load_runtime_payload()
    defaults = _load_llm_defaults(payload.get("defaults"))
    providers = _load_provider_settings(payload.get("providers"))
    agent_defaults = _load_agent_defaults(payload.get("agent_defaults"), providers)
    return RuntimeSettings(defaults=defaults, providers=providers, agent_defaults=agent_defaults)


@lru_cache(maxsize=1)
def get_project_runtime_settings() -> ProjectRuntimeSettings:
    payload = _load_project_runtime_payload()
    compaction_default, compaction_vendors = _load_project_compaction_settings(payload.get("compaction"))
    file_extraction_default, file_extraction_vendors = _load_project_file_extraction_settings(payload.get("file_extraction"))
    agent_loop = _load_project_agent_loop_settings(payload.get("agent_loop"))
    subagent_loop = _load_project_subagent_loop_settings(payload.get("subagent_loop"))
    logging_settings = _load_project_logging_settings(payload.get("logging"))
    session_memory = _load_project_session_memory_settings(payload.get("session_memory"))
    lsp_settings = _load_project_lsp_settings(payload.get("lsp"))
    mcp_settings = _load_project_mcp_settings(payload.get("mcp"))
    return ProjectRuntimeSettings(
        compaction_default=compaction_default,
        compaction_vendors=compaction_vendors,
        file_extraction_default=file_extraction_default,
        file_extraction_vendors=file_extraction_vendors,
        agent_loop=agent_loop,
        subagent_loop=subagent_loop,
        logging=logging_settings,
        session_memory=session_memory,
        lsp=lsp_settings,
        mcp=mcp_settings,
    )


def resolve_compaction_settings(vendor: str | None = None) -> CompactionSettings:
    settings = get_project_runtime_settings()
    normalized_vendor = (vendor or "").strip().lower()
    if normalized_vendor and normalized_vendor in settings.compaction_vendors:
        return settings.compaction_vendors[normalized_vendor]
    return settings.compaction_default


def resolve_file_extraction_settings(vendor: str | None = None) -> FileExtractionSettings:
    settings = get_project_runtime_settings()
    normalized_vendor = (vendor or "").strip().lower()
    if normalized_vendor and normalized_vendor in settings.file_extraction_vendors:
        return settings.file_extraction_vendors[normalized_vendor]
    return settings.file_extraction_default


def resolve_agent_loop_settings() -> AgentLoopSettings:
    return get_project_runtime_settings().agent_loop


def resolve_subagent_loop_settings() -> SubagentLoopSettings:
    return get_project_runtime_settings().subagent_loop


def resolve_logging_settings() -> LoggingSettings:
    return get_project_runtime_settings().logging


def resolve_session_memory_settings() -> SessionMemorySettings:
    return get_project_runtime_settings().session_memory


def get_lsp_settings() -> LspSettings:
    return get_project_runtime_settings().lsp


def get_mcp_settings() -> McpSettings:
    return get_project_runtime_settings().mcp


def clear_runtime_settings_cache() -> None:
    get_runtime_settings.cache_clear()
    get_project_runtime_settings.cache_clear()


def resolve_llm_config(agent: MainAgentMode, provider_name: str | None = None, model_name: str | None = None) -> ResolvedLLMConfig:
    settings = get_runtime_settings()
    agent_default = settings.agent_defaults[agent]
    provider_key = (provider_name or agent_default.provider).strip().lower()
    provider = settings.providers.get(provider_key)
    if provider is None:
        raise ValueError(f"未找到名为 '{provider_key}' 的大模型厂商配置。")

    api_key = os.getenv(provider.api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"缺少 API Key，请配置环境变量 {provider.api_key_env}。")

    explicit_model = str(model_name or "").strip()
    if explicit_model:
        model = explicit_model
    elif provider_name:
        model = provider.default_model
    else:
        model = agent_default.model
    if model not in provider.models:
        raise ValueError(f"provider '{provider.name}' 未定义模型 '{model}'。")
    model_settings = provider.models[model]
    effective_max_tokens = model_settings.max_tokens
    if effective_max_tokens is None:
        effective_max_tokens = settings.defaults.max_tokens
    return ResolvedLLMConfig(
        agent=agent,
        provider=provider.name,
        vendor=provider.vendor,
        model=model,
        max_tokens=effective_max_tokens,
        api_mode=provider.api_mode,
        base_url=provider.base_url,
        api_key=api_key,
        timeout_seconds=provider.timeout_seconds,
    )


def build_runtime_options() -> dict[str, Any]:
    settings = get_runtime_settings()
    workspace = get_workspace()
    providers = [
        {
            "name": provider.name,
            "vendor": provider.vendor,
            "default_model": provider.default_model,
            "models": list(provider.model_names),
            "api_mode": provider.api_mode,
        }
        for provider in settings.providers.values()
    ]
    agents = [
        {
            "name": agent,
            "default_provider": settings.agent_defaults[agent].provider,
            "default_model": settings.agent_defaults[agent].model,
            "api_mode": settings.providers[settings.agent_defaults[agent].provider].api_mode,
        }
        for agent in ("build", "plan")
    ]
    slash_commands = [
        {
            "name": command.name,
            "description": command.description,
            "usage": command.usage,
            "placeholder": command.placeholder,
        }
        for command in list_visible_slash_commands()
    ]
    return {
        "default_agent": "build",
        "agents": agents,
        "providers": providers,
        "slash_commands": slash_commands,
        "workspace_root": str(workspace.root),
        "workspace_name": workspace.workspace_name,
        "has_agents_md": workspace.has_agents_md,
        "launch_mode": workspace.launch_mode,
    }
