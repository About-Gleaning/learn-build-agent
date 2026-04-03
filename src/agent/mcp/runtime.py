import asyncio
import concurrent.futures
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any

from ..config.settings import McpServerSettings, get_mcp_settings
from ..tools.handlers import build_tool_failure, build_tool_success

logger = logging.getLogger(__name__)

_MCP_IMPORT_ERROR: Exception | None = None
_HTTPX_IMPORT_ERROR: Exception | None = None

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
except Exception as exc:  # pragma: no cover - 依赖缺失时走降级逻辑
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = exc

try:
    import httpx
except Exception as exc:  # pragma: no cover - 依赖缺失时走降级逻辑
    httpx = None  # type: ignore[assignment]
    _HTTPX_IMPORT_ERROR = exc


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CACHE_LOCK = threading.Lock()
_SERVER_CACHE: dict[str, "McpServerSnapshot"] = {}
_RUNNER_LOCK = threading.Lock()
_ASYNC_RUNNER: "_AsyncioThreadRunner | None" = None


@dataclass(frozen=True)
class McpToolDescriptor:
    server_alias: str
    tool_name: str
    prefixed_name: str
    description: str
    parameters: dict[str, Any]
    expose_to_plan: bool


@dataclass(frozen=True)
class McpServerWarning:
    server_alias: str
    message: str


@dataclass(frozen=True)
class McpRuntimeAlert:
    server_alias: str
    code: str
    message: str


@dataclass(frozen=True)
class McpServerSnapshot:
    fingerprint: str
    tools: tuple[McpToolDescriptor, ...]
    warnings: tuple[McpServerWarning, ...]


@dataclass(frozen=True)
class _ToolCallOutcome:
    result: Any
    close_warning: str = ""


class _McpToolCallError(RuntimeError):
    """保留主异常与关闭阶段告警，避免关闭失败覆盖真实根因。"""

    def __init__(self, primary_exception: Exception, *, close_warning: str = "") -> None:
        self.primary_exception = primary_exception
        self.close_warning = close_warning
        super().__init__(_format_exception_summary(primary_exception))


class _AsyncioThreadRunner:
    """在专用线程中托管 MCP 事件循环，避免跨线程关闭 async 资源。"""

    def __init__(self) -> None:
        self._loop_ready = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, name="mcp-runtime-loop", daemon=True)
        self._thread.start()
        self._loop_ready.wait()
        if self._loop is None:
            raise RuntimeError("MCP 后台事件循环初始化失败。")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._closed.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive() and self._loop is not None and not self._closed.is_set()

    def run(self, coro: Any) -> Any:
        if not self.is_alive():
            raise RuntimeError("MCP 后台事件循环不可用。")
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError as exc:
            raise RuntimeError("MCP 后台任务被取消。") from exc

    def close(self) -> None:
        with self._lock:
            if not self.is_alive():
                return
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._closed.set()


def clear_mcp_runtime_cache() -> None:
    with _CACHE_LOCK:
        _SERVER_CACHE.clear()


def _shutdown_asyncio_thread_runner() -> None:
    global _ASYNC_RUNNER
    with _RUNNER_LOCK:
        runner = _ASYNC_RUNNER
        _ASYNC_RUNNER = None
    if runner is not None:
        runner.close()


def list_mcp_tools(mode: str | None = None) -> tuple[list[dict[str, Any]], list[McpServerWarning]]:
    settings = get_mcp_settings()
    if not settings.enabled:
        return [], []

    normalized_mode = (mode or "").strip().lower()
    tool_specs: list[dict[str, Any]] = []
    warnings: list[McpServerWarning] = []
    for server_alias, server_settings in (settings.servers or {}).items():
        if not _should_discover_server_in_mode(server_settings, normalized_mode):
            continue
        snapshot = _get_server_snapshot(server_alias, server_settings)
        warnings.extend(snapshot.warnings)
        for tool in snapshot.tools:
            if normalized_mode == "plan" and not tool.expose_to_plan:
                continue
            tool_specs.append(_build_tool_spec(tool))
    return tool_specs, warnings


def describe_mcp_warnings_for_mode(mode: str | None = None) -> str:
    _, warnings = list_mcp_tools(mode)
    if not warnings:
        return ""
    lines = ["以下 MCP server 当前不可用，已自动跳过："]
    for warning in warnings:
        lines.append(f"- {warning.server_alias}: {warning.message}")
    return "\n".join(lines)


def describe_mcp_runtime_alerts_for_mode(mode: str | None = None) -> list[McpRuntimeAlert]:
    _, warnings = list_mcp_tools(mode)
    alerts: list[McpRuntimeAlert] = []
    for warning in warnings:
        alerts.append(
            McpRuntimeAlert(
                server_alias=warning.server_alias,
                code="mcp_server_unavailable",
                message=warning.message,
            )
        )
    return alerts


def execute_mcp_tool(
    prefixed_tool_name: str,
    arguments: dict[str, Any],
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    server_alias, tool_name = _split_prefixed_tool_name(prefixed_tool_name)
    if not server_alias or not tool_name:
        return build_tool_failure(
            f"Error: 非法 MCP tool 名称: {prefixed_tool_name}",
            error_code="mcp_tool_name_invalid",
        )

    settings = get_mcp_settings()
    if not settings.enabled:
        return build_tool_failure("Error: MCP 功能未启用。", error_code="mcp_disabled")

    server_settings = (settings.servers or {}).get(server_alias)
    if server_settings is None or not server_settings.enabled:
        return build_tool_failure(
            f"Error: 未找到启用的 MCP server: {server_alias}",
            error_code="mcp_server_not_found",
        )
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == "plan" and not server_settings.expose_to_plan:
        return build_tool_failure(
            f"Error: plan 模式下不允许执行 MCP tool: {prefixed_tool_name}",
            error_code="mcp_tool_not_allowed_in_plan",
            mcp_server_alias=server_alias,
            mcp_tool_name=tool_name,
            mcp_transport=server_settings.transport,
        )

    snapshot = _get_server_snapshot(server_alias, server_settings)
    if snapshot.warnings:
        latest_warning = snapshot.warnings[-1].message
        return build_tool_failure(
            f"Error: MCP server 不可用: {latest_warning}",
            error_code="mcp_server_unavailable",
            mcp_server_alias=server_alias,
            mcp_tool_name=tool_name,
            mcp_transport=server_settings.transport,
        )

    try:
        outcome = _run_sync(
            _call_server_tool(
                server_alias,
                server_settings,
                tool_name,
                arguments,
            )
        )
    except Exception as exc:
        error_type, error_summary, close_warning = _extract_tool_call_error_details(exc)
        logger.warning(
            "mcp.call.failed server=%s tool=%s error_type=%s error=%s close_warning=%s",
            server_alias,
            tool_name,
            error_type,
            error_summary,
            close_warning or "-",
        )
        return build_tool_failure(
            _build_tool_call_failure_message(error_summary, close_warning),
            error_code="mcp_tool_call_failed",
            error_type=error_type,
            error_summary=error_summary,
            mcp_server_alias=server_alias,
            mcp_tool_name=tool_name,
            mcp_transport=server_settings.transport,
            close_warning=close_warning,
        )

    close_warning = outcome.close_warning if isinstance(outcome, _ToolCallOutcome) else ""
    result = outcome.result if isinstance(outcome, _ToolCallOutcome) else outcome
    if close_warning:
        logger.warning(
            "mcp.call.close_warning server=%s tool=%s warning=%s",
            server_alias,
            tool_name,
            close_warning,
        )

    output = _stringify_tool_result(result)
    return build_tool_success(
        output,
        mcp_server_alias=server_alias,
        mcp_tool_name=tool_name,
        mcp_transport=server_settings.transport,
    )


def _split_prefixed_tool_name(prefixed_tool_name: str) -> tuple[str, str]:
    normalized = (prefixed_tool_name or "").strip()
    if "__" not in normalized:
        return "", ""
    server_alias, tool_name = normalized.split("__", 1)
    return server_alias.strip(), tool_name.strip()


def _should_discover_server_in_mode(server_settings: McpServerSettings, normalized_mode: str) -> bool:
    if not server_settings.enabled:
        return False
    if normalized_mode == "plan" and not server_settings.expose_to_plan:
        return False
    return True


def _get_server_snapshot(server_alias: str, server_settings: McpServerSettings) -> McpServerSnapshot:
    fingerprint = _build_server_fingerprint(server_settings)
    with _CACHE_LOCK:
        cached = _SERVER_CACHE.get(server_alias)
        if cached is not None and cached.fingerprint == fingerprint:
            return cached

    snapshot = _discover_server_snapshot(server_alias, server_settings, fingerprint)
    # 发现失败只返回告警，不写入长期缓存，避免临时故障把进程卡死到重启。
    if not snapshot.warnings:
        with _CACHE_LOCK:
            _SERVER_CACHE[server_alias] = snapshot
    return snapshot


def _discover_server_snapshot(
    server_alias: str,
    server_settings: McpServerSettings,
    fingerprint: str,
) -> McpServerSnapshot:
    try:
        if _MCP_IMPORT_ERROR is not None:
            raise RuntimeError(f"缺少 mcp 依赖: {_MCP_IMPORT_ERROR}")
        raw_tools = _run_sync(_discover_server_tools(server_alias, server_settings))
        tools = _normalize_discovered_tools(server_alias, server_settings, raw_tools)
        return McpServerSnapshot(
            fingerprint=fingerprint,
            tools=tuple(tools),
            warnings=(),
        )
    except Exception as exc:
        warning = McpServerWarning(server_alias=server_alias, message=str(exc))
        logger.warning("mcp.discover.failed server=%s error=%s", server_alias, exc)
        return McpServerSnapshot(
            fingerprint=fingerprint,
            tools=(),
            warnings=(warning,),
        )


def _build_server_fingerprint(server_settings: McpServerSettings) -> str:
    payload = {
        "enabled": server_settings.enabled,
        "transport": server_settings.transport,
        "command": server_settings.command,
        "args": list(server_settings.args),
        "env": _build_resolved_mapping_fingerprint_payload(server_settings.env),
        "cwd": server_settings.cwd,
        "url": server_settings.url,
        "headers": _build_resolved_mapping_fingerprint_payload(server_settings.headers),
        "expose_to_plan": server_settings.expose_to_plan,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_resolved_mapping_fingerprint_payload(raw_mapping: dict[str, str]) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for key, raw_value in raw_mapping.items():
        resolved_value, missing_env_names = _resolve_value_for_fingerprint(str(raw_value))
        payload[key] = {
            "resolved_sha256": hashlib.sha256(resolved_value.encode("utf-8")).hexdigest(),
            "missing_env_names": missing_env_names,
        }
    return payload


def _resolve_value_for_fingerprint(raw_value: str) -> tuple[str, list[str]]:
    missing_env_names: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.getenv(env_name, "")
        if not env_value:
            missing_env_names.append(env_name)
            return f"__MISSING_ENV__:{env_name}"
        return env_value

    return _ENV_PATTERN.sub(_replace, raw_value), missing_env_names


def _build_tool_spec(tool: McpToolDescriptor) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.prefixed_name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _normalize_discovered_tools(
    server_alias: str,
    server_settings: McpServerSettings,
    raw_tools: list[Any],
) -> list[McpToolDescriptor]:
    descriptors: list[McpToolDescriptor] = []
    seen_names: set[str] = set()
    for raw_tool in raw_tools:
        tool_name = str(_read_value(raw_tool, "name", "")).strip()
        if not tool_name or tool_name in seen_names:
            continue
        seen_names.add(tool_name)
        description = str(_read_value(raw_tool, "description", "")).strip()
        description_prefix = f"来自 MCP server `{server_alias}` 的工具。"
        normalized_description = description_prefix if not description else f"{description_prefix}\n\n{description}"
        raw_schema = _read_value(raw_tool, "inputSchema")
        if raw_schema is None:
            raw_schema = _read_value(raw_tool, "input_schema")
        parameters = _normalize_tool_schema(raw_schema)
        descriptors.append(
            McpToolDescriptor(
                server_alias=server_alias,
                tool_name=tool_name,
                prefixed_name=f"{server_alias}__{tool_name}",
                description=normalized_description,
                parameters=parameters,
                expose_to_plan=server_settings.expose_to_plan,
            )
        )
    return descriptors


def _normalize_tool_schema(raw_schema: Any) -> dict[str, Any]:
    fallback_schema = {
        "type": "object",
        "properties": {},
    }
    if not isinstance(raw_schema, dict):
        return fallback_schema
    schema_type = str(raw_schema.get("type", "object")).strip().lower()
    if schema_type and schema_type != "object":
        return fallback_schema
    properties = raw_schema.get("properties", {})
    if properties is not None and not isinstance(properties, dict):
        properties = {}
    normalized = {
        "type": "object",
        "properties": properties if isinstance(properties, dict) else {},
    }
    required = raw_schema.get("required")
    if isinstance(required, list):
        normalized["required"] = [str(item) for item in required if str(item).strip()]
    additional_properties = raw_schema.get("additionalProperties")
    if isinstance(additional_properties, bool):
        normalized["additionalProperties"] = additional_properties
    return normalized


def _extract_tool_call_error_details(exc: Exception) -> tuple[str, str, str]:
    if isinstance(exc, _McpToolCallError):
        primary_exception = exc.primary_exception
        close_warning = exc.close_warning
    else:
        primary_exception = exc
        close_warning = ""
    return type(primary_exception).__name__, _format_exception_summary(primary_exception), close_warning


def _build_tool_call_failure_message(error_summary: str, close_warning: str) -> str:
    message = f"Error: MCP tool 调用失败: {error_summary}"
    if close_warning:
        message = f"{message}\n关闭阶段告警: {close_warning}"
    return message


def _format_exception_summary(exc: BaseException, *, depth: int = 0, max_depth: int = 4) -> str:
    exc_type = type(exc).__name__
    exc_message = str(exc).strip()
    summary = exc_type if not exc_message else f"{exc_type}: {exc_message}"
    if depth >= max_depth:
        return summary

    children = getattr(exc, "exceptions", None)
    if not isinstance(children, tuple) or not children:
        return summary

    child_parts: list[str] = []
    for child in children[:3]:
        if isinstance(child, BaseException):
            child_parts.append(_format_exception_summary(child, depth=depth + 1, max_depth=max_depth))
        else:
            child_parts.append(str(child))
    if len(children) > 3:
        child_parts.append(f"... 其余 {len(children) - 3} 个子异常省略")
    return f"{summary} | 子异常: {'; '.join(child_parts)}"


def _stringify_tool_result(result: Any) -> str:
    text_parts: list[str] = []
    for item in _read_value(result, "content", []) or []:
        item_type = str(_read_value(item, "type", "")).strip().lower()
        if item_type == "text":
            text = str(_read_value(item, "text", "")).strip()
            if text:
                text_parts.append(text)
        elif item_type:
            text_parts.append(f"[MCP {item_type} content omitted]")

    structured = _read_value(result, "structuredContent")
    if structured is None:
        structured = _read_value(result, "structured_content")
    if structured not in (None, {}, []):
        text_parts.append(json.dumps(structured, ensure_ascii=False))

    if not text_parts:
        return json.dumps(_normalize_generic_result(result), ensure_ascii=False)
    return "\n".join(text_parts)


def _normalize_generic_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_generic_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_generic_result(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _read_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _resolve_mapping_placeholders(raw_mapping: dict[str, str], *, field_name: str, server_alias: str) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, raw_value in raw_mapping.items():
        value = str(raw_value)

        def _replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            env_value = os.getenv(env_name, "")
            if not env_value:
                raise ValueError(f"{field_name}.{key} 引用了未设置的环境变量 {env_name}")
            return env_value

        resolved[key] = _ENV_PATTERN.sub(_replace, value)
    return resolved


async def _discover_server_tools(server_alias: str, server_settings: McpServerSettings) -> list[Any]:
    async with _open_server_session(server_settings, server_alias=server_alias, tool_name="list_tools") as session_ctx:
        response = await asyncio.wait_for(
            session_ctx.session.list_tools(),
            timeout=server_settings.discovery_timeout_ms / 1000,
        )
        tools = _read_value(response, "tools", [])
        if not isinstance(tools, list):
            return list(tools or [])
        return tools


async def _call_server_tool(
    server_alias: str,
    server_settings: McpServerSettings,
    tool_name: str,
    arguments: dict[str, Any],
) -> _ToolCallOutcome:
    session_ctx: _SessionHandle | None = None
    try:
        async with _open_server_session(server_settings, server_alias=server_alias, tool_name=tool_name) as session_ctx:
            result = await asyncio.wait_for(
                session_ctx.session.call_tool(tool_name, arguments=arguments),
                timeout=server_settings.call_timeout_ms / 1000,
            )
    except Exception as exc:
        close_warning = session_ctx.consume_close_warning() if session_ctx is not None else ""
        if close_warning:
            raise _McpToolCallError(exc, close_warning=close_warning) from exc
        raise
    return _ToolCallOutcome(result=result, close_warning=session_ctx.consume_close_warning())


@dataclass
class _SessionHandle:
    session: Any
    close_warning: str = ""

    def consume_close_warning(self) -> str:
        warning = self.close_warning
        self.close_warning = ""
        return warning


class _SessionContextManager:
    def __init__(self, server_settings: McpServerSettings, *, server_alias: str, tool_name: str = "") -> None:
        self.server_settings = server_settings
        self.server_alias = server_alias or "unknown"
        self.tool_name = tool_name or "unknown"
        self._stack: contextlib.AsyncExitStack | None = None
        self._session_handle: _SessionHandle | None = None

    async def __aenter__(self) -> _SessionHandle:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            if self.server_settings.transport == "stdio":
                if StdioServerParameters is None or stdio_client is None:
                    raise RuntimeError("当前环境缺少 stdio MCP 客户端依赖。")
                resolved_env = _resolve_mapping_placeholders(
                    self.server_settings.env,
                    field_name="mcp.servers.env",
                    server_alias=self.server_alias,
                )
                params = StdioServerParameters(
                    command=self.server_settings.command,
                    args=list(self.server_settings.args),
                    env=resolved_env or None,
                    cwd=self.server_settings.cwd or None,
                )
                transport_ctx = await stack.enter_async_context(stdio_client(params))
            else:
                if streamable_http_client is None:
                    raise RuntimeError("当前环境缺少 streamable HTTP MCP 客户端依赖。")
                if httpx is None:
                    raise RuntimeError(f"缺少 httpx 依赖: {_HTTPX_IMPORT_ERROR}")
                resolved_headers = _resolve_mapping_placeholders(
                    self.server_settings.headers,
                    field_name="mcp.servers.headers",
                    server_alias=self.server_alias,
                )
                http_client = httpx.AsyncClient(
                    headers=resolved_headers or None,
                    timeout=self.server_settings.call_timeout_ms / 1000,
                )
                http_client = await stack.enter_async_context(http_client)
                transport_ctx = await stack.enter_async_context(
                    streamable_http_client(self.server_settings.url, http_client=http_client)
                )

            if self.server_settings.transport == "stdio":
                read_stream, write_stream = transport_ctx
            else:
                read_stream, write_stream, _ = transport_ctx
            session = ClientSession(read_stream, write_stream)
            await stack.enter_async_context(session)
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session_handle = _SessionHandle(session=session)
        return self._session_handle

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stack is None:
            return
        try:
            await self._stack.__aexit__(exc_type, exc, tb)
        except Exception as close_exc:
            close_warning = f"{type(close_exc).__name__}: {close_exc}"
            logger.warning(
                "mcp.session.close_failed server=%s tool=%s warning=%s",
                self.server_alias,
                self.tool_name,
                close_warning,
            )
            if self._session_handle is not None:
                self._session_handle.close_warning = close_warning
            else:
                raise
        finally:
            self._session_handle = None
            self._stack = None


def _open_server_session(
    server_settings: McpServerSettings,
    *,
    server_alias: str,
    tool_name: str = "",
) -> _SessionContextManager:
    return _SessionContextManager(server_settings, server_alias=server_alias, tool_name=tool_name)


def _run_sync(coro: Any) -> Any:
    return _get_asyncio_thread_runner().run(coro)


def _get_asyncio_thread_runner() -> _AsyncioThreadRunner:
    global _ASYNC_RUNNER
    with _RUNNER_LOCK:
        if _ASYNC_RUNNER is None or not _ASYNC_RUNNER.is_alive():
            if _ASYNC_RUNNER is not None:
                _ASYNC_RUNNER.close()
            _ASYNC_RUNNER = _AsyncioThreadRunner()
        return _ASYNC_RUNNER
