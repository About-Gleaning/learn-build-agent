import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()

MainAgentMode = Literal["build", "plan"]

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LLM_PROMPT = os.getenv("LOG_LLM_PROMPT", "false").strip().lower() in {"1", "true", "yes", "on"}
LOG_LLM_PROMPT_LIMIT = max(200, int(os.getenv("LOG_LLM_PROMPT_LIMIT", "2000")))
LLM_CONFIG_PATH = Path(
    os.getenv(
        "LLM_CONFIG_PATH",
        str(Path(__file__).resolve().with_name("llm_runtime.json")),
    )
).resolve()


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    vendor: str
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float


@dataclass(frozen=True)
class AgentDefaultSettings:
    provider: str
    model: str | None = None


@dataclass(frozen=True)
class RuntimeSettings:
    providers: dict[str, ProviderSettings]
    agent_defaults: dict[MainAgentMode, AgentDefaultSettings]


@dataclass(frozen=True)
class ResolvedLLMConfig:
    agent: MainAgentMode
    provider: str
    vendor: str
    model: str
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
        model = str(raw_value.get("model", "")).strip()
        api_key_env = str(raw_value.get("api_key_env", "")).strip()
        timeout_seconds_raw = raw_value.get("timeout_seconds", 60)
        if not base_url or not vendor or not model or not api_key_env:
            raise ValueError(f"provider '{name}' 必须配置 vendor、base_url、model、api_key_env。")
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
            model=model,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
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
        model = str(raw_value.get("model", "")).strip() or None
        if provider not in providers:
            raise ValueError(f"agent_defaults.{agent}.provider '{provider}' 未在 providers 中定义。")
        defaults[agent] = AgentDefaultSettings(provider=provider, model=model)
    return defaults


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    payload = _load_runtime_payload()
    providers = _load_provider_settings(payload.get("providers"))
    agent_defaults = _load_agent_defaults(payload.get("agent_defaults"), providers)
    return RuntimeSettings(providers=providers, agent_defaults=agent_defaults)


def clear_runtime_settings_cache() -> None:
    get_runtime_settings.cache_clear()


def resolve_llm_config(agent: MainAgentMode, provider_name: str | None = None) -> ResolvedLLMConfig:
    settings = get_runtime_settings()
    agent_default = settings.agent_defaults[agent]
    provider_key = (provider_name or agent_default.provider).strip().lower()
    provider = settings.providers.get(provider_key)
    if provider is None:
        raise ValueError(f"未找到名为 '{provider_key}' 的大模型厂商配置。")

    api_key = os.getenv(provider.api_key_env, "").strip() or os.getenv("API_KEY", "").strip()
    if not api_key:
        raise ValueError(f"缺少 API Key，请配置环境变量 {provider.api_key_env}。")

    model = agent_default.model or provider.model
    return ResolvedLLMConfig(
        agent=agent,
        provider=provider.name,
        vendor=provider.vendor,
        model=model,
        base_url=provider.base_url,
        api_key=api_key,
        timeout_seconds=provider.timeout_seconds,
    )


def build_runtime_options() -> dict[str, Any]:
    settings = get_runtime_settings()
    providers = [
        {
            "name": provider.name,
            "vendor": provider.vendor,
            "default_model": provider.model,
        }
        for provider in settings.providers.values()
    ]
    agents = [
        {
            "name": agent,
            "default_provider": settings.agent_defaults[agent].provider,
            "default_model": settings.agent_defaults[agent].model
            or settings.providers[settings.agent_defaults[agent].provider].model,
        }
        for agent in ("build", "plan")
    ]
    return {
        "default_agent": "build",
        "agents": agents,
        "providers": providers,
    }
