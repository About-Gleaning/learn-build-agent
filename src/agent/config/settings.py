import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from ..runtime.workspace import get_workspace

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


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    vendor: str
    base_url: str
    default_model: str
    models: tuple[str, ...]
    api_key_env: str
    timeout_seconds: float
    api_mode: LLMApiMode


@dataclass(frozen=True)
class AgentDefaultSettings:
    provider: str
    model: str


@dataclass(frozen=True)
class RuntimeSettings:
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


@dataclass(frozen=True)
class FileExtractionSettings:
    allowed_extensions: tuple[str, ...] = DEFAULT_FILE_EXTRACTION_ALLOWED_EXTENSIONS
    cleanup_mode: str = DEFAULT_FILE_EXTRACTION_CLEANUP_MODE


@dataclass(frozen=True)
class AgentLoopSettings:
    max_rounds: int = DEFAULT_AGENT_LOOP_MAX_ROUNDS


@dataclass(frozen=True)
class ResolvedLLMConfig:
    agent: MainAgentMode
    provider: str
    vendor: str
    model: str
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
        models: list[str] = []
        for raw_model_name, raw_model_value in raw_models.items():
            model_name = str(raw_model_name).strip()
            if not model_name:
                raise ValueError(f"provider '{name}'.models 中的模型名称不能为空。")
            if raw_model_value is not None and not isinstance(raw_model_value, dict):
                raise ValueError(f"provider '{name}'.models.{model_name} 必须是对象或 null。")
            models.append(model_name)
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
            models=tuple(models),
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            api_mode=raw_api_mode,  # type: ignore[arg-type]
        )
    return providers


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
    providers = _load_provider_settings(payload.get("providers"))
    agent_defaults = _load_agent_defaults(payload.get("agent_defaults"), providers)
    return RuntimeSettings(providers=providers, agent_defaults=agent_defaults)


@lru_cache(maxsize=1)
def get_project_runtime_settings() -> ProjectRuntimeSettings:
    payload = _load_project_runtime_payload()
    compaction_default, compaction_vendors = _load_project_compaction_settings(payload.get("compaction"))
    file_extraction_default, file_extraction_vendors = _load_project_file_extraction_settings(payload.get("file_extraction"))
    agent_loop = _load_project_agent_loop_settings(payload.get("agent_loop"))
    return ProjectRuntimeSettings(
        compaction_default=compaction_default,
        compaction_vendors=compaction_vendors,
        file_extraction_default=file_extraction_default,
        file_extraction_vendors=file_extraction_vendors,
        agent_loop=agent_loop,
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

    api_key = os.getenv(provider.api_key_env, "").strip() or os.getenv("API_KEY", "").strip()
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
    return ResolvedLLMConfig(
        agent=agent,
        provider=provider.name,
        vendor=provider.vendor,
        model=model,
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
            "models": list(provider.models),
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
    return {
        "default_agent": "build",
        "agents": agents,
        "providers": providers,
        "workspace_root": str(workspace.root),
        "workspace_name": workspace.workspace_name,
        "has_agents_md": workspace.has_agents_md,
        "launch_mode": workspace.launch_mode,
    }
