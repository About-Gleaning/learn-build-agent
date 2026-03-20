import os
from dataclasses import dataclass
from typing import Any

import pytest
from openai import OpenAI

from agent.config.settings import resolve_llm_config


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


RUN_QWEN_LIVE_TESTS = _env_enabled("RUN_QWEN_LIVE_TESTS")
QWEN_LIVE_STRICT = _env_enabled("QWEN_LIVE_STRICT")


@dataclass(frozen=True)
class ToolSchemaCase:
    name: str
    description: str
    parameters: dict[str, Any] | None


SCHEMA_CASES: list[ToolSchemaCase] = [
    ToolSchemaCase(
        name="no_parameters",
        description="完全省略 parameters，验证无参工具是否可被接受。",
        parameters=None,
    ),
    ToolSchemaCase(
        name="string_property",
        description="最小 object -> string 参数。",
        parameters={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
    ),
    ToolSchemaCase(
        name="string_with_enum",
        description="string + enum。",
        parameters={
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["fast", "deep"]}},
            "required": ["mode"],
        },
    ),
    ToolSchemaCase(
        name="string_with_description",
        description="string + description。",
        parameters={
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "任务说明"}},
            "required": ["prompt"],
        },
    ),
    ToolSchemaCase(
        name="integer_property",
        description="object -> integer 参数。",
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
        },
    ),
    ToolSchemaCase(
        name="number_property",
        description="object -> number 参数。",
        parameters={
            "type": "object",
            "properties": {"timeout": {"type": "number"}},
            "required": ["timeout"],
        },
    ),
    ToolSchemaCase(
        name="boolean_property",
        description="object -> boolean 参数。",
        parameters={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
    ),
    ToolSchemaCase(
        name="array_of_string",
        description="object -> array[string] 参数。",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["items"],
        },
    ),
    ToolSchemaCase(
        name="array_of_object",
        description="object -> array[object] 参数。",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            },
            "required": ["items"],
        },
    ),
    ToolSchemaCase(
        name="nested_object",
        description="object -> nested object 参数。",
        parameters={
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                }
            },
            "required": ["payload"],
        },
    ),
    ToolSchemaCase(
        name="empty_object_schema",
        description="空 object schema。",
        parameters={"type": "object"},
    ),
    ToolSchemaCase(
        name="with_additional_properties",
        description="包含 additionalProperties。",
        parameters={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
    ),
    ToolSchemaCase(
        name="with_default",
        description="包含 default。",
        parameters={
            "type": "object",
            "properties": {"agent": {"type": "string", "default": "explore"}},
        },
    ),
    ToolSchemaCase(
        name="required_missing_property",
        description="required 引用了不存在的属性。",
        parameters={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["missing"],
        },
    ),
]


def _read_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _truncate(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_client() -> tuple[OpenAI, str]:
    model_name = os.getenv("QWEN_LIVE_MODEL", "").strip() or "qwen3.5-flash"
    llm_config = resolve_llm_config("plan", provider_name="qwen", model_name=model_name)
    client = OpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=llm_config.timeout_seconds,
    )
    return client, llm_config.model


def _build_tool_payload(case: ToolSchemaCase) -> dict[str, Any]:
    tool_payload: dict[str, Any] = {
        "type": "function",
        "name": f"schema_probe_{case.name}",
        "description": case.description,
    }
    if case.parameters is not None:
        tool_payload["parameters"] = case.parameters
    return tool_payload


def _call_qwen_with_schema(case: ToolSchemaCase) -> dict[str, Any]:
    client, model = _build_client()
    tool_payload = _build_tool_payload(case)
    request_payload = {
        "model": model,
        "input": [{"role": "user", "content": "hello"}],
        "tools": [tool_payload],
        "store": False,
        "max_output_tokens": 32,
    }

    try:
        response = client.responses.create(**request_payload)
    except Exception as exc:  # noqa: BLE001 - 这里需要原样采集真实 provider 行为
        error_message = _truncate(exc, limit=400)
        error_type = type(exc).__name__
        normalized_error_type = "schema_rejected" if "InvalidParameter" in error_message else "request_error"
        return {
            "case": case.name,
            "ok": False,
            "error_type": normalized_error_type,
            "exception_type": error_type,
            "detail": error_message,
            "schema": case.parameters,
        }

    response_status = _read_value(response, "status", "unknown")
    response_error = _read_value(response, "error")
    if str(response_status).strip().lower() != "completed":
        detail = _truncate(_read_value(response_error, "message", response_error), limit=400)
        error_code = _read_value(response_error, "code", "")
        normalized_error_type = "schema_rejected" if "InvalidParameter" in detail else "response_failed"
        return {
            "case": case.name,
            "ok": False,
            "error_type": normalized_error_type,
            "exception_type": "ResponseError",
            "detail": detail,
            "error_code": error_code,
            "status": response_status,
            "schema": case.parameters,
        }

    return {
        "case": case.name,
        "ok": True,
        "status": response_status,
        "response_id": _read_value(response, "id", ""),
        "schema": case.parameters,
    }


def _format_case_result(result: dict[str, Any]) -> str:
    schema = result.get("schema")
    schema_summary = "None" if schema is None else _truncate(schema, limit=180)
    if result.get("ok"):
        return (
            f"[OK] case={result['case']} status={result.get('status', 'unknown')} "
            f"response_id={result.get('response_id', '')} schema={schema_summary}"
        )
    return (
        f"[FAIL] case={result['case']} status={result.get('status', 'unknown')} "
        f"error_type={result.get('error_type', 'unknown')} "
        f"exception_type={result.get('exception_type', 'unknown')} detail={result.get('detail', '')} "
        f"schema={schema_summary}"
    )


@pytest.mark.skipif(
    not RUN_QWEN_LIVE_TESTS,
    reason="未开启 qwen 真实接口测试。请设置 RUN_QWEN_LIVE_TESTS=1，并确保 QWEN_API_KEY 可用。",
)
def test_qwen_responses_live_tool_schema_matrix():
    results: list[dict[str, Any]] = []
    for case in SCHEMA_CASES:
        result = _call_qwen_with_schema(case)
        results.append(result)
        print(_format_case_result(result))

    request_errors = [item for item in results if item.get("error_type") == "request_error"]
    if request_errors:
        formatted = "\n".join(_format_case_result(item) for item in request_errors)
        pytest.fail(f"qwen 真实接口测试出现请求级错误，请先检查网络、鉴权或 SDK 调用链：\n{formatted}")

    if QWEN_LIVE_STRICT:
        schema_failures = [item for item in results if not item.get("ok")]
        if schema_failures:
            formatted = "\n".join(_format_case_result(item) for item in schema_failures)
            pytest.fail(f"QWEN_LIVE_STRICT=1，以下 schema case 被 qwen 拒绝：\n{formatted}")
