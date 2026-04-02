from __future__ import annotations

import atexit
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.settings import get_lsp_settings
from ..runtime.workspace import get_workspace
from .documents import get_document_store
from .filters import filter_diagnostics
from .protocol import JsonRpcEndpoint
from .servers.base import LspServerAdapter
from .types import LspDiagnostic, LspDiagnosticsResult, LspPosition, LspQueryResult, LspRange, LspServerStatus

logger = logging.getLogger(__name__)

_DIAGNOSTICS_RETRY_TIMEOUT_MS = 1500

_LSP_SEVERITY_MAP = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}


@dataclass(frozen=True)
class _DiagnosticsWaitResult:
    diagnostics: list[LspDiagnostic]
    sequence: int
    wait_rounds: int
    wait_ms: int
    settled: bool


@dataclass(frozen=True)
class _PublishEvent:
    uri: str
    diagnostics_count: int
    sequence: int
    updated_at_ns: int


@dataclass
class _PublishedDiagnostics:
    diagnostics: list[LspDiagnostic] = field(default_factory=list)
    sequence: int = 0
    updated_at_ns: int = 0


@dataclass
class _ServerEvent:
    level: str = ""
    message: str = ""
    updated_at_ns: int = 0


@dataclass
class _PendingServerStart:
    condition: threading.Condition = field(default_factory=threading.Condition)
    completed: bool = False
    server: ManagedLspServer | None = None
    error: Exception | None = None


@dataclass
class ManagedLspServer:
    status: LspServerStatus
    adapter: LspServerAdapter
    endpoint: JsonRpcEndpoint
    process: subprocess.Popen[bytes]
    capabilities: dict[str, Any] = field(default_factory=dict)
    diagnostics_by_uri: dict[str, _PublishedDiagnostics] = field(default_factory=dict)
    publish_events: list[_PublishEvent] = field(default_factory=list)
    status_events: list[_ServerEvent] = field(default_factory=list)
    log_events: list[_ServerEvent] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)
    last_used_at_ns: int = field(default_factory=time.time_ns)

    def touch(self) -> None:
        self.last_used_at_ns = time.time_ns()

    def get_sequence(self, uri: str) -> int:
        with self.condition:
            return self.diagnostics_by_uri.get(uri, _PublishedDiagnostics()).sequence

    def wait_for_diagnostics(self, uri: str, *, previous_sequence: int, timeout_ms: int, settle_ms: int) -> list[LspDiagnostic]:
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000
        with self.condition:
            while True:
                self._raise_if_unavailable(uri)
                published = self.diagnostics_by_uri.get(uri)
                if published is not None and published.sequence > previous_sequence:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待诊断超时: {uri}")
                self.condition.wait(timeout=remaining)
            if settle_ms > 0:
                self.condition.wait(timeout=settle_ms / 1000)
            latest = self.diagnostics_by_uri.get(uri, _PublishedDiagnostics())
            return list(latest.diagnostics)

    def get_published(self, uri: str) -> _PublishedDiagnostics:
        with self.condition:
            published = self.diagnostics_by_uri.get(uri)
            if published is None:
                return _PublishedDiagnostics()
            return _PublishedDiagnostics(
                diagnostics=list(published.diagnostics),
                sequence=published.sequence,
                updated_at_ns=published.updated_at_ns,
            )

    def _raise_if_unavailable(self, uri: str) -> None:
        if self.endpoint.is_alive() and self.process.poll() is None:
            return
        exit_code = self.process.poll()
        if exit_code is None:
            raise RuntimeError(f"LSP 通道已关闭，未收到 diagnostics: {uri}")
        raise RuntimeError(f"LSP 进程已退出（exit_code={exit_code}），未收到 diagnostics: {uri}")

    def close(self) -> None:
        try:
            self.endpoint.request("shutdown", {}, timeout_ms=1000)
        except Exception:
            pass
        try:
            self.endpoint.notify("exit", {})
        except Exception:
            pass
        self.endpoint.close()
        try:
            if self.process.poll() is None:
                self.process.terminate()
        except Exception:
            pass

    def append_status_event(self, level: str, message: str) -> None:
        with self.condition:
            self.status_events.append(
                _ServerEvent(level=level.strip(), message=message.strip(), updated_at_ns=time.time_ns())
            )
            self.status_events = self.status_events[-20:]
            self.condition.notify_all()

    def append_log_event(self, level: str, message: str) -> None:
        with self.condition:
            self.log_events.append(_ServerEvent(level=level.strip(), message=message.strip(), updated_at_ns=time.time_ns()))
            self.log_events = self.log_events[-20:]
            self.condition.notify_all()


class LspManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: dict[str, ManagedLspServer] = {}
        self._starting_servers: dict[str, _PendingServerStart] = {}
        self._documents = get_document_store()

    def collect_diagnostics(
        self,
        adapter: LspServerAdapter,
        *,
        file_path: Path,
        content: str,
    ) -> LspDiagnosticsResult:
        workspace_root, workspace_selection_reason = adapter.select_workspace_root_with_reason(
            file_path.resolve(),
            get_workspace().root.resolve(),
        )
        preflight_issue = adapter.detect_preflight_issue(file_path=file_path.resolve(), workspace_root=workspace_root)
        if preflight_issue is not None:
            resolved_java_settings = (
                adapter.resolve_maven_import_config(file_path=file_path.resolve(), workspace_root=workspace_root)
                if hasattr(adapter, "resolve_maven_import_config")
                else None
            )
            return LspDiagnosticsResult(
                status="project_import_failed",
                lsp_language=adapter.language,
                lsp_server=adapter.server_name,
                lsp_workspace_root=str(workspace_root),
                lsp_data_dir=str(adapter.build_data_dir(workspace_root, file_path=file_path.resolve())),
                lsp_workspace_selection_reason=workspace_selection_reason,
                lsp_server_key=adapter.build_server_key(workspace_root, file_path=file_path.resolve()),
                lsp_snapshot_uri=file_path.resolve().as_uri(),
                lsp_error=preflight_issue.message,
                java_project_issue_code=preflight_issue.issue_code,
                java_project_state=preflight_issue.project_state,
                java_maven_profiles=(
                    resolved_java_settings.profiles if resolved_java_settings is not None else ()
                ),
                java_maven_profiles_source=(
                    resolved_java_settings.profiles_source if resolved_java_settings is not None else ""
                ),
                java_maven_local_repository=(
                    resolved_java_settings.local_repository
                    if resolved_java_settings is not None
                    else adapter.get_language_settings().maven_local_repository
                ),
            )
        server = self.get_or_start(adapter, file_path=file_path)
        previous_sequence = server.get_sequence(file_path.resolve().as_uri())
        snapshot = self.sync_document(server, file_path=file_path, content=content)
        settings = get_lsp_settings()
        try:
            wait_result = self._wait_for_diagnostics_or_issue(
                server,
                snapshot_uri=snapshot.uri,
                previous_sequence=previous_sequence,
                timeout_ms=settings.request_timeout_ms,
                settle_ms=adapter.diagnostics_settle_ms(),
            )
            if isinstance(wait_result, LspDiagnosticsResult):
                return wait_result
            raw_diagnostics = wait_result.diagnostics
            diagnostics_sequence = wait_result.sequence
            diagnostics_wait_rounds = wait_result.wait_rounds
            diagnostics_wait_ms = wait_result.wait_ms
            diagnostics_settled = wait_result.settled
        except TimeoutError:
            logger.warning(
                "LSP diagnostics 首次等待超时，准备补发 didSave 重试: server_key=%s pid=%s uri=%s",
                server.status.server_key,
                server.status.pid,
                snapshot.uri,
            )
            retry_result = self._retry_collect_diagnostics_after_save(
                server,
                snapshot_uri=snapshot.uri,
                previous_sequence=previous_sequence,
                settle_ms=adapter.diagnostics_settle_ms(),
            )
            if retry_result is not None:
                return retry_result
            raw_diagnostics = []
            diagnostics_sequence = 0
            diagnostics_wait_rounds = 0
            diagnostics_wait_ms = 0
            diagnostics_settled = False
        return self._build_filtered_or_project_issue_result(
            server,
            snapshot_uri=snapshot.uri,
            previous_sequence=previous_sequence,
            diagnostics=raw_diagnostics,
            diagnostics_sequence=diagnostics_sequence,
            diagnostics_wait_rounds=diagnostics_wait_rounds,
            diagnostics_wait_ms=diagnostics_wait_ms,
            diagnostics_settled=diagnostics_settled,
        )

    def execute_operation(
        self,
        adapter: LspServerAdapter,
        *,
        operation: str,
        file_path: Path,
        content: str,
        line: int,
        character: int,
    ) -> LspQueryResult:
        workspace_root, workspace_selection_reason = adapter.select_workspace_root_with_reason(
            file_path.resolve(),
            get_workspace().root.resolve(),
        )
        preflight_issue = adapter.detect_preflight_issue(file_path=file_path.resolve(), workspace_root=workspace_root)
        if preflight_issue is not None:
            resolved_java_settings = (
                adapter.resolve_maven_import_config(file_path=file_path.resolve(), workspace_root=workspace_root)
                if hasattr(adapter, "resolve_maven_import_config")
                else None
            )
            return LspQueryResult(
                status="project_import_failed",
                operation=operation,
                lsp_language=adapter.language,
                lsp_server=adapter.server_name,
                lsp_workspace_root=str(workspace_root),
                lsp_data_dir=str(adapter.build_data_dir(workspace_root, file_path=file_path.resolve())),
                lsp_workspace_selection_reason=workspace_selection_reason,
                lsp_server_key=adapter.build_server_key(workspace_root, file_path=file_path.resolve()),
                lsp_snapshot_uri=file_path.resolve().as_uri(),
                lsp_error=preflight_issue.message,
                java_project_issue_code=preflight_issue.issue_code,
                java_project_state=preflight_issue.project_state,
                java_maven_profiles=(
                    resolved_java_settings.profiles if resolved_java_settings is not None else ()
                ),
                java_maven_profiles_source=(
                    resolved_java_settings.profiles_source if resolved_java_settings is not None else ""
                ),
                java_maven_local_repository=(
                    resolved_java_settings.local_repository
                    if resolved_java_settings is not None
                    else adapter.get_language_settings().maven_local_repository
                ),
            )

        server = self.get_or_start(adapter, file_path=file_path)
        snapshot = self.sync_document(server, file_path=file_path, content=content)
        if not self._is_operation_supported(server, operation):
            return LspQueryResult(
                status="operation_unsupported",
                operation=operation,
                lsp_language=server.status.language,
                lsp_server=server.status.server_name,
                lsp_server_pid=server.status.pid,
                lsp_error=f"当前 LSP 不支持 {operation}",
                **self._build_observation_fields(server, snapshot_uri=snapshot.uri),
            )

        request_payload = self._build_operation_request(
            operation=operation,
            snapshot_uri=snapshot.uri,
            line=line,
            character=character,
        )
        selected_item: dict[str, Any] | None = None
        try:
            if request_payload["method"] == "__call_hierarchy_follow_up__":
                hierarchy_result = server.endpoint.request(
                    "textDocument/prepareCallHierarchy",
                    request_payload["prepare_params"],
                    timeout_ms=get_lsp_settings().request_timeout_ms,
                )
                hierarchy_items = self._normalize_call_hierarchy_items(hierarchy_result)
                if not hierarchy_items:
                    return self._build_query_result(
                        server,
                        operation=operation,
                        snapshot_uri=snapshot.uri,
                        result=hierarchy_result if hierarchy_result not in (None, []) else [],
                        call_hierarchy_item=None,
                    )
                selected_item = hierarchy_items[0]
                result = server.endpoint.request(
                    request_payload["follow_up_method"],
                    {"item": selected_item},
                    timeout_ms=get_lsp_settings().request_timeout_ms,
                )
            else:
                result = server.endpoint.request(
                    request_payload["method"],
                    request_payload["params"],
                    timeout_ms=get_lsp_settings().request_timeout_ms,
                )
        except Exception as exc:
            return LspQueryResult(
                status="request_failed",
                operation=operation,
                lsp_language=server.status.language,
                lsp_server=server.status.server_name,
                lsp_server_pid=server.status.pid,
                lsp_error=str(exc)[:300],
                **self._build_observation_fields(server, snapshot_uri=snapshot.uri),
            )
        return self._build_query_result(
            server,
            operation=operation,
            snapshot_uri=snapshot.uri,
            result=result,
            call_hierarchy_item=selected_item,
        )

    def _retry_collect_diagnostics_after_save(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
        previous_sequence: int,
        settle_ms: int,
    ) -> LspDiagnosticsResult | None:
        server.endpoint.notify("textDocument/didSave", {"textDocument": {"uri": snapshot_uri}})
        server.touch()
        retry_timeout_ms = min(_DIAGNOSTICS_RETRY_TIMEOUT_MS, get_lsp_settings().request_timeout_ms)
        try:
            wait_result = self._wait_for_diagnostics_or_issue(
                server,
                snapshot_uri=snapshot_uri,
                previous_sequence=previous_sequence,
                timeout_ms=retry_timeout_ms,
                settle_ms=settle_ms,
            )
            if isinstance(wait_result, LspDiagnosticsResult):
                return wait_result
            raw_diagnostics = wait_result.diagnostics
            diagnostics_sequence = wait_result.sequence
            diagnostics_wait_rounds = wait_result.wait_rounds
            diagnostics_wait_ms = wait_result.wait_ms
            diagnostics_settled = wait_result.settled
        except TimeoutError:
            logger.warning(
                "LSP diagnostics didSave 重试后仍超时，降级返回: server_key=%s pid=%s uri=%s",
                server.status.server_key,
                server.status.pid,
                snapshot_uri,
            )
            return LspDiagnosticsResult(
                status="timeout_degraded",
                lsp_language=server.status.language,
                lsp_server=server.status.server_name,
                lsp_server_pid=server.status.pid,
                lsp_error="等待 diagnostics 超时；已补发 didSave 重试，但仍未收到 publishDiagnostics。",
                diagnostics_previous_sequence=previous_sequence,
                diagnostics_latest_sequence=server.get_sequence(snapshot_uri),
                **self._build_observation_fields(
                    server,
                    snapshot_uri=snapshot_uri,
                ),
            )
        except RuntimeError as exc:
            logger.warning(
                "LSP diagnostics didSave 重试期间通道异常: server_key=%s pid=%s uri=%s error=%s",
                server.status.server_key,
                server.status.pid,
                snapshot_uri,
                exc,
            )
            raise
        logger.info(
            "LSP diagnostics didSave 重试成功: server_key=%s pid=%s uri=%s",
            server.status.server_key,
            server.status.pid,
            snapshot_uri,
        )
        return self._build_filtered_or_project_issue_result(
            server,
            snapshot_uri=snapshot_uri,
            previous_sequence=previous_sequence,
            diagnostics=raw_diagnostics,
            diagnostics_sequence=diagnostics_sequence,
            diagnostics_wait_rounds=diagnostics_wait_rounds,
            diagnostics_wait_ms=diagnostics_wait_ms,
            diagnostics_settled=diagnostics_settled,
        )

    def _wait_for_diagnostics_or_issue(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
        previous_sequence: int,
        timeout_ms: int,
        settle_ms: int,
    ) -> _DiagnosticsWaitResult | LspDiagnosticsResult:
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000
        wait_started_at = time.monotonic()
        wait_rounds = 0
        with server.condition:
            while True:
                server._raise_if_unavailable(snapshot_uri)
                published = server.diagnostics_by_uri.get(snapshot_uri)
                if published is not None and published.sequence > previous_sequence:
                    break
                project_issue = self._build_project_issue_result(
                    server,
                    snapshot_uri=snapshot_uri,
                    previous_sequence=previous_sequence,
                )
                if project_issue is not None:
                    return project_issue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待诊断超时: {snapshot_uri}")
                server.condition.wait(timeout=min(remaining, 0.2))
            latest = server.diagnostics_by_uri.get(snapshot_uri, _PublishedDiagnostics())
            if settle_ms > 0:
                server.condition.wait(timeout=settle_ms / 1000)
                latest = server.diagnostics_by_uri.get(snapshot_uri, _PublishedDiagnostics())

        if server.status.language != "java":
            return _DiagnosticsWaitResult(
                diagnostics=list(latest.diagnostics),
                sequence=latest.sequence,
                wait_rounds=wait_rounds,
                wait_ms=int((time.monotonic() - wait_started_at) * 1000),
                settled=True,
            )
        return self._wait_until_diagnostics_stable(
            server,
            snapshot_uri=snapshot_uri,
            deadline=deadline,
            initial_sequence=latest.sequence,
            wait_started_at=wait_started_at,
        )

    def _wait_until_diagnostics_stable(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
        deadline: float,
        initial_sequence: int,
        wait_started_at: float,
    ) -> _DiagnosticsWaitResult | LspDiagnosticsResult:
        settings = get_lsp_settings()
        stable_window_seconds = settings.diagnostics_stable_window_ms / 1000
        latest = server.get_published(snapshot_uri)
        stable_deadline = min(deadline, time.monotonic() + stable_window_seconds)
        wait_rounds = 0

        while True:
            with server.condition:
                while True:
                    server._raise_if_unavailable(snapshot_uri)
                    published = server.diagnostics_by_uri.get(snapshot_uri)
                    if published is not None and published.sequence > latest.sequence:
                        latest = _PublishedDiagnostics(
                            diagnostics=list(published.diagnostics),
                            sequence=published.sequence,
                            updated_at_ns=published.updated_at_ns,
                        )
                        wait_rounds += 1
                        if wait_rounds >= settings.diagnostics_max_wait_rounds:
                            return _DiagnosticsWaitResult(
                                diagnostics=list(latest.diagnostics),
                                sequence=latest.sequence,
                                wait_rounds=wait_rounds,
                                wait_ms=int((time.monotonic() - wait_started_at) * 1000),
                                settled=False,
                            )
                        stable_deadline = min(deadline, time.monotonic() + stable_window_seconds)
                        break

                    project_issue = self._build_project_issue_result(
                        server,
                        snapshot_uri=snapshot_uri,
                        previous_sequence=initial_sequence,
                    )
                    if project_issue is not None:
                        return project_issue

                    remaining = min(deadline, stable_deadline) - time.monotonic()
                    if remaining <= 0:
                        return _DiagnosticsWaitResult(
                            diagnostics=list(latest.diagnostics),
                            sequence=latest.sequence or initial_sequence,
                            wait_rounds=wait_rounds,
                            wait_ms=int((time.monotonic() - wait_started_at) * 1000),
                            settled=True,
                        )
                    server.condition.wait(timeout=min(remaining, 0.2))

    def _build_project_issue_result(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
        previous_sequence: int = 0,
    ) -> LspDiagnosticsResult | None:
        if server.status.language != "java":
            return None
        issue = _detect_java_project_issue(
            snapshot_uri=snapshot_uri,
            status_events=server.status_events,
            log_events=server.log_events,
            publish_events=server.publish_events,
        )
        if issue is None:
            return None
        return LspDiagnosticsResult(
            status="project_import_failed",
            lsp_language=server.status.language,
            lsp_server=server.status.server_name,
            lsp_server_pid=server.status.pid,
            lsp_error=issue.message,
            diagnostics_previous_sequence=previous_sequence,
            diagnostics_latest_sequence=server.get_sequence(snapshot_uri),
            java_project_issue_code=issue.issue_code,
            java_project_state=issue.project_state,
            **self._build_observation_fields(
                server,
                snapshot_uri=snapshot_uri,
            ),
        )

    def _build_observation_fields(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
    ) -> dict[str, Any]:
        settings = get_lsp_settings()
        recent_publish_uris, received_other_file_diagnostics = _summarize_publish_events(
            server.publish_events,
            snapshot_uri=snapshot_uri,
        )
        observation_fields = {
            "lsp_workspace_root": server.status.workspace_root,
            "lsp_data_dir": server.status.data_dir,
            "lsp_workspace_selection_reason": server.status.workspace_selection_reason,
            "lsp_server_key": server.status.server_key,
            "lsp_snapshot_uri": snapshot_uri,
            "recent_status_summary": _summarize_server_events(server.status_events),
            "recent_log_summary": _summarize_server_events(server.log_events),
            "recent_publish_uris": recent_publish_uris,
            "received_other_file_diagnostics": received_other_file_diagnostics,
            "java_maven_profiles": server.status.java_maven_profiles,
            "java_maven_profiles_source": server.status.java_maven_profiles_source,
            "java_maven_local_repository": server.status.java_maven_local_repository,
            "java_debug_observation_enabled": settings.java_debug_observation_enabled,
        }
        if settings.java_debug_observation_enabled and server.status.language == "java":
            observation_fields.update(
                {
                    "debug_status_events": _format_debug_server_events(server.status_events),
                    "debug_log_events": _format_debug_server_events(server.log_events),
                    "debug_publish_events": _format_debug_publish_events(server.publish_events),
                    "debug_issue_probe": _build_java_issue_probe(
                        snapshot_uri=snapshot_uri,
                        status_events=server.status_events,
                        log_events=server.log_events,
                    ),
                }
            )
        return observation_fields

    def _build_filtered_or_project_issue_result(
        self,
        server: ManagedLspServer,
        *,
        snapshot_uri: str,
        previous_sequence: int,
        diagnostics: list[LspDiagnostic],
        diagnostics_sequence: int,
        diagnostics_wait_rounds: int,
        diagnostics_wait_ms: int,
        diagnostics_settled: bool,
    ) -> LspDiagnosticsResult:
        settings = get_lsp_settings()
        filtered_result = filter_diagnostics(
            diagnostics,
            include_severity=settings.include_severity,
            max_diagnostics=settings.max_diagnostics,
            max_chars=settings.max_chars,
            lsp_language=server.status.language,
            lsp_server=server.status.server_name,
            lsp_server_pid=server.status.pid,
            diagnostics_sequence=diagnostics_sequence,
            diagnostics_previous_sequence=previous_sequence,
            diagnostics_latest_sequence=diagnostics_sequence,
            diagnostics_wait_rounds=diagnostics_wait_rounds,
            diagnostics_wait_ms=diagnostics_wait_ms,
            diagnostics_settled=diagnostics_settled,
            **self._build_observation_fields(
                server,
                snapshot_uri=snapshot_uri,
            ),
        )
        if server.status.language != "java":
            return filtered_result
        issue = _detect_java_project_issue(
            snapshot_uri=snapshot_uri,
            status_events=server.status_events,
            log_events=server.log_events,
            publish_events=server.publish_events,
        )
        if issue is None:
            return filtered_result
        return LspDiagnosticsResult(
            status="project_import_failed",
            diagnostics=(),
            diagnostics_total=0,
            diagnostics_summary="",
            diagnostics_truncated=filtered_result.diagnostics_truncated,
            lsp_language=filtered_result.lsp_language,
            lsp_server=filtered_result.lsp_server,
            lsp_server_pid=filtered_result.lsp_server_pid,
            lsp_error=issue.message,
            raw_diagnostics_total=filtered_result.raw_diagnostics_total,
            diagnostics_sequence=filtered_result.diagnostics_sequence,
            diagnostics_previous_sequence=filtered_result.diagnostics_previous_sequence,
            diagnostics_latest_sequence=filtered_result.diagnostics_latest_sequence,
            diagnostics_wait_rounds=filtered_result.diagnostics_wait_rounds,
            diagnostics_wait_ms=filtered_result.diagnostics_wait_ms,
            diagnostics_settled=filtered_result.diagnostics_settled,
            lsp_workspace_root=filtered_result.lsp_workspace_root,
            lsp_data_dir=filtered_result.lsp_data_dir,
            lsp_workspace_selection_reason=filtered_result.lsp_workspace_selection_reason,
            lsp_server_key=filtered_result.lsp_server_key,
            lsp_snapshot_uri=filtered_result.lsp_snapshot_uri,
            recent_status_summary=filtered_result.recent_status_summary,
            recent_log_summary=filtered_result.recent_log_summary,
            recent_publish_uris=filtered_result.recent_publish_uris,
            received_other_file_diagnostics=filtered_result.received_other_file_diagnostics,
            java_project_issue_code=issue.issue_code,
            java_project_state=issue.project_state,
            java_maven_profiles=filtered_result.java_maven_profiles,
            java_maven_profiles_source=filtered_result.java_maven_profiles_source,
            java_maven_local_repository=filtered_result.java_maven_local_repository,
            java_debug_observation_enabled=filtered_result.java_debug_observation_enabled,
            debug_status_events=filtered_result.debug_status_events,
            debug_log_events=filtered_result.debug_log_events,
            debug_publish_events=filtered_result.debug_publish_events,
            debug_issue_probe=filtered_result.debug_issue_probe,
        )

    def get_or_start(self, adapter: LspServerAdapter, *, file_path: Path) -> ManagedLspServer:
        self.cleanup_idle_servers()
        workspace_root, workspace_selection_reason = adapter.select_workspace_root_with_reason(
            file_path.resolve(),
            get_workspace().root.resolve(),
        )
        resolved_java_settings = (
            adapter.resolve_maven_import_config(file_path=file_path.resolve(), workspace_root=workspace_root)
            if hasattr(adapter, "resolve_maven_import_config")
            else None
        )
        server_key = adapter.build_server_key(workspace_root, file_path=file_path.resolve())
        pending_start: _PendingServerStart | None = None
        should_start = False
        while True:
            with self._lock:
                existing = self._servers.get(server_key)
                if existing is not None and existing.process.poll() is None and existing.endpoint.is_alive():
                    existing.touch()
                    return existing
                if existing is not None:
                    existing.close()
                    self._servers.pop(server_key, None)
                    # 旧进程已经退出或失效时，必须同时清掉文档快照，避免新进程误收到 didChange。
                    self._documents.clear_server(server_key)

                pending_start = self._starting_servers.get(server_key)
                if pending_start is None:
                    pending_start = _PendingServerStart()
                    self._starting_servers[server_key] = pending_start
                    should_start = True
                    break

            logger.info("LSP 命中进行中的启动任务，等待复用: server_key=%s workspace_root=%s", server_key, workspace_root)
            with pending_start.condition:
                while not pending_start.completed:
                    pending_start.condition.wait(timeout=0.2)
                if pending_start.server is not None:
                    pending_start.server.touch()
                    return pending_start.server
                if pending_start.error is not None:
                    raise pending_start.error

        command = adapter.build_command(workspace_root)
        initialize_started_at = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        server: ManagedLspServer | None = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace_root),
            )
            status = LspServerStatus(
                server_key=server_key,
                server_name=adapter.server_name or command[0],
                workspace_root=str(workspace_root),
                language=adapter.language,
                adapter_mode=adapter.adapter_mode,
                pid=process.pid,
                data_dir=str(adapter.build_data_dir(workspace_root, file_path=file_path.resolve())),
                workspace_selection_reason=workspace_selection_reason,
                java_maven_profiles=resolved_java_settings.profiles if resolved_java_settings is not None else (),
                java_maven_profiles_source=(
                    resolved_java_settings.profiles_source if resolved_java_settings is not None else ""
                ),
                java_maven_local_repository=(
                    resolved_java_settings.local_repository
                    if resolved_java_settings is not None
                    else adapter.get_language_settings().maven_local_repository
                ),
            )
            server = ManagedLspServer(
                status=status,
                adapter=adapter,
                endpoint=JsonRpcEndpoint(process, notification_handler=lambda method, params: self._handle_message(server_key, method, params)),
                process=process,
            )
            with pending_start.condition:
                pending_start.server = server
            logger.info(
                "LSP 开始启动并等待 initialize: server_key=%s workspace_root=%s pid=%s",
                server_key,
                workspace_root,
                process.pid,
            )
            initialize_result = server.endpoint.request(
                "initialize",
                adapter.build_initialize_params(workspace_root, file_path=file_path.resolve()),
                timeout_ms=get_lsp_settings().request_timeout_ms,
            )
            if isinstance(initialize_result, dict):
                capabilities = initialize_result.get("capabilities")
                if isinstance(capabilities, dict):
                    server.capabilities = capabilities
            server.endpoint.notify("initialized", {})
            elapsed_ms = int((time.monotonic() - initialize_started_at) * 1000)
            logger.info("LSP initialize 成功: server_key=%s pid=%s elapsed_ms=%s", server_key, process.pid, elapsed_ms)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - initialize_started_at) * 1000)
            logger.warning(
                "LSP initialize 失败: server_key=%s workspace_root=%s pid=%s elapsed_ms=%s error=%s",
                server_key,
                workspace_root,
                None if process is None else process.pid,
                elapsed_ms,
                exc,
            )
            if server is not None:
                server.close()
            elif process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                except Exception:
                    pass
            self._documents.clear_server(server_key)
            with self._lock:
                self._starting_servers.pop(server_key, None)
            with pending_start.condition:
                pending_start.server = None
                pending_start.error = exc
                pending_start.completed = True
                pending_start.condition.notify_all()
            raise

        with self._lock:
            self._servers[server_key] = server
            self._starting_servers.pop(server_key, None)
        with pending_start.condition:
            pending_start.server = server
            pending_start.completed = True
            pending_start.condition.notify_all()
        server.touch()
        return server

    def sync_document(self, server: ManagedLspServer, *, file_path: Path, content: str):
        snapshot = self._documents.get(server.status.server_key, file_path)
        if snapshot is None or not snapshot.opened:
            opened = self._documents.open_document(
                server.status.server_key,
                file_path,
                language_id=server.adapter.language_id,
                text=content,
            )
            server.endpoint.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": opened.uri,
                        "languageId": opened.language_id,
                        "version": opened.version,
                        "text": opened.current_text,
                    }
                },
            )
            server.touch()
            return opened

        updated = self._documents.update_document(server.status.server_key, file_path, text=content)
        server.endpoint.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": updated.uri,
                    "version": updated.version,
                },
                "contentChanges": [{"text": updated.current_text}],
            },
        )
        server.touch()
        return updated

    def cleanup_idle_servers(self) -> None:
        ttl_ns = get_lsp_settings().server_idle_ttl_seconds * 1_000_000_000
        now_ns = time.time_ns()
        stale_keys: list[str] = []
        with self._lock:
            for server_key, server in self._servers.items():
                if server.process.poll() is not None or now_ns - server.last_used_at_ns > ttl_ns:
                    stale_keys.append(server_key)
            for server_key in stale_keys:
                server = self._servers.pop(server_key)
                server.close()
                # server 被 TTL 回收或异常退出后，新的同 key 进程必须从 didOpen 重新建链。
                self._documents.clear_server(server_key)

    def shutdown_all(self) -> None:
        with self._lock:
            servers = [(server.status.server_key, server) for server in self._servers.values()]
            self._servers.clear()
        for server_key, server in servers:
            server.close()
            self._documents.clear_server(server_key)

    def _handle_message(self, server_key: str, method: str, params: Any) -> None:
        with self._lock:
            server = self._servers.get(server_key)
            if server is None:
                pending_start = self._starting_servers.get(server_key)
                server = None if pending_start is None else pending_start.server
        if server is None:
            return

        if method == "language/status" and isinstance(params, dict):
            server.append_status_event(str(params.get("type", "")), str(params.get("message", "")))
            return
        if method == "window/logMessage" and isinstance(params, dict):
            server.append_log_event(str(params.get("type", "")), str(params.get("message", "")))
            return
        if method != "textDocument/publishDiagnostics" or not isinstance(params, dict):
            return
        uri = str(params.get("uri", "")).strip()
        if not uri:
            return
        raw_diagnostics = params.get("diagnostics")
        if not isinstance(raw_diagnostics, list):
            raw_diagnostics = []
        diagnostics = [_convert_diagnostic(item) for item in raw_diagnostics if isinstance(item, dict)]
        with server.condition:
            published = server.diagnostics_by_uri.get(uri, _PublishedDiagnostics())
            updated_at_ns = time.time_ns()
            server.diagnostics_by_uri[uri] = _PublishedDiagnostics(
                diagnostics=diagnostics,
                sequence=published.sequence + 1,
                updated_at_ns=updated_at_ns,
            )
            server.publish_events.append(
                _PublishEvent(
                    uri=uri,
                    diagnostics_count=len(diagnostics),
                    sequence=published.sequence + 1,
                    updated_at_ns=updated_at_ns,
                )
            )
            server.publish_events = server.publish_events[-20:]
            server.condition.notify_all()

    def _build_query_result(
        self,
        server: ManagedLspServer,
        *,
        operation: str,
        snapshot_uri: str,
        result: Any,
        call_hierarchy_item: dict[str, Any] | None,
    ) -> LspQueryResult:
        return LspQueryResult(
            status="completed",
            operation=operation,
            result=result,
            result_count=_count_lsp_result_items(result),
            call_hierarchy_item=call_hierarchy_item,
            lsp_language=server.status.language,
            lsp_server=server.status.server_name,
            lsp_server_pid=server.status.pid,
            **self._build_observation_fields(server, snapshot_uri=snapshot_uri),
        )

    def _is_operation_supported(self, server: ManagedLspServer, operation: str) -> bool:
        capabilities = server.capabilities
        if operation == "goToDefinition":
            return _capability_enabled(capabilities.get("definitionProvider"))
        if operation == "findReferences":
            return _capability_enabled(capabilities.get("referencesProvider"))
        if operation == "hover":
            return _capability_enabled(capabilities.get("hoverProvider"))
        if operation == "documentSymbol":
            return _capability_enabled(capabilities.get("documentSymbolProvider"))
        if operation == "workspaceSymbol":
            return _capability_enabled(capabilities.get("workspaceSymbolProvider"))
        if operation == "goToImplementation":
            return _capability_enabled(capabilities.get("implementationProvider"))
        if operation == "prepareCallHierarchy":
            return _capability_enabled(capabilities.get("callHierarchyProvider"))
        if operation in {"incomingCalls", "outgoingCalls"}:
            return _capability_enabled(capabilities.get("callHierarchyProvider"))
        return False

    def _build_operation_request(
        self,
        *,
        operation: str,
        snapshot_uri: str,
        line: int,
        character: int,
    ) -> dict[str, Any]:
        text_document = {"uri": snapshot_uri}
        position = {"line": line, "character": character}
        if operation == "goToDefinition":
            return {"method": "textDocument/definition", "params": {"textDocument": text_document, "position": position}}
        if operation == "findReferences":
            return {
                "method": "textDocument/references",
                "params": {
                    "textDocument": text_document,
                    "position": position,
                    "context": {"includeDeclaration": False},
                },
            }
        if operation == "hover":
            return {"method": "textDocument/hover", "params": {"textDocument": text_document, "position": position}}
        if operation == "documentSymbol":
            return {"method": "textDocument/documentSymbol", "params": {"textDocument": text_document}}
        if operation == "workspaceSymbol":
            return {"method": "workspace/symbol", "params": {"query": ""}}
        if operation == "goToImplementation":
            return {"method": "textDocument/implementation", "params": {"textDocument": text_document, "position": position}}
        if operation == "prepareCallHierarchy":
            return {
                "method": "textDocument/prepareCallHierarchy",
                "params": {"textDocument": text_document, "position": position},
            }
        if operation == "incomingCalls":
            return {
                "method": "__call_hierarchy_follow_up__",
                "prepare_params": {"textDocument": text_document, "position": position},
                "follow_up_method": "callHierarchy/incomingCalls",
            }
        if operation == "outgoingCalls":
            return {
                "method": "__call_hierarchy_follow_up__",
                "prepare_params": {"textDocument": text_document, "position": position},
                "follow_up_method": "callHierarchy/outgoingCalls",
            }
        raise ValueError(f"未支持的 LSP operation: {operation}")

    def _normalize_call_hierarchy_items(self, result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            return [result]
        return []


def _convert_diagnostic(payload: dict[str, Any]) -> LspDiagnostic:
    raw_range = payload.get("range") if isinstance(payload.get("range"), dict) else {}
    raw_start = raw_range.get("start") if isinstance(raw_range.get("start"), dict) else {}
    raw_end = raw_range.get("end") if isinstance(raw_range.get("end"), dict) else {}
    severity = _LSP_SEVERITY_MAP.get(int(payload.get("severity", 3)), "information")
    code = payload.get("code")
    if code is not None:
        code = str(code)
    source = payload.get("source")
    if source is not None:
        source = str(source)
    return LspDiagnostic(
        severity=severity,
        code=code,
        source=source,
        message=str(payload.get("message", "")).strip(),
        range=LspRange(
            start=LspPosition(
                line=int(raw_start.get("line", 0)),
                character=int(raw_start.get("character", 0)),
            ),
            end=LspPosition(
                line=int(raw_end.get("line", 0)),
                character=int(raw_end.get("character", 0)),
            ),
        ),
    )


def _capability_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, dict)


def _count_lsp_result_items(result: Any) -> int | None:
    if result is None:
        return 0
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return 1
    return None


_JAVA_PROJECT_ERROR_PATTERNS = (
    "Initialization failed",
    "does not resolve to a ICompilationUnit",
    "Errors occurred during the build",
)


@dataclass(frozen=True)
class _JavaProjectIssue:
    message: str
    issue_code: str
    project_state: str


def _detect_java_project_issue(
    *,
    snapshot_uri: str,
    status_events: list[_ServerEvent],
    log_events: list[_ServerEvent],
    publish_events: list[_PublishEvent],
) -> _JavaProjectIssue | None:
    combined_lines: list[str] = []
    for event in status_events[-10:]:
        if event.message:
            combined_lines.append(event.message)
    for event in log_events[-10:]:
        if event.message:
            combined_lines.extend(line.strip() for line in event.message.splitlines() if line.strip())

    normalized_text = "\n".join(combined_lines)
    if not normalized_text:
        return None

    if "does not resolve to a ICompilationUnit" in normalized_text:
        detail = _extract_matching_line(normalized_text, ("does not resolve to a ICompilationUnit", snapshot_uri))
        return _JavaProjectIssue(
            message=f"Java 工程尚未完成导入，当前文件还未进入编译单元：{detail}",
            issue_code="compilation_unit_missing",
            project_state="compilation_unit_missing",
        )

    if "does not exist Java Model Exception" in normalized_text or "Error in Java Model (code 969)" in normalized_text:
        detail = _extract_matching_line(normalized_text, ("Error in Java Model (code 969)",))
        project_state = "partial_java_model" if _has_other_file_diagnostics(publish_events, snapshot_uri=snapshot_uri) else "java_model_missing"
        return _JavaProjectIssue(
            message=f"Java 工程导入未稳定完成，当前源码包尚未进入 Java Model：{detail}",
            issue_code="java_model_exception_969",
            project_state=project_state,
        )

    if ".m2" in normalized_text and "Operation not permitted" in normalized_text:
        detail = _extract_matching_line(normalized_text, (".m2", "Operation not permitted"))
        return _JavaProjectIssue(
            message=f"Java 工程导入失败，Maven 本地仓库不可写：{detail}",
            issue_code="maven_repo_not_writable",
            project_state="maven_import_failed",
        )

    if "Errors occurred during the build" in normalized_text:
        detail = _extract_matching_line(normalized_text, ("Errors occurred during the build",))
        return _JavaProjectIssue(
            message=f"Java 工程构建失败：{detail}",
            issue_code="build_failed",
            project_state="build_failed",
        )

    if "Initialization failed" in normalized_text:
        detail = _extract_matching_line(normalized_text, ("Initialization failed",))
        return _JavaProjectIssue(
            message=f"Java 工程导入失败：{detail}",
            issue_code="initialization_failed",
            project_state="initialization_failed",
        )

    if any(pattern in normalized_text for pattern in _JAVA_PROJECT_ERROR_PATTERNS):
        return _JavaProjectIssue(
            message=f"Java 工程初始化异常：{_condense_text(normalized_text)}",
            issue_code="java_project_error",
            project_state="java_project_error",
        )
    return None


def _has_other_file_diagnostics(events: list[_PublishEvent], *, snapshot_uri: str) -> bool:
    return any(event.uri != snapshot_uri for event in events[-20:])


def _format_debug_server_events(events: list[_ServerEvent]) -> str:
    if not events:
        return ""
    parts: list[str] = []
    for event in events[-20:]:
        parts.append(
            f"{event.updated_at_ns}:{event.level or '-'}:{_condense_text(event.message)}"
        )
    return " | ".join(parts)[:4000]


def _format_debug_publish_events(events: list[_PublishEvent]) -> str:
    if not events:
        return ""
    parts: list[str] = []
    for event in events[-20:]:
        parts.append(
            f"{event.updated_at_ns}:{_condense_uri(event.uri)}#{event.sequence}({event.diagnostics_count})"
        )
    return " | ".join(parts)[:4000]


def _build_java_issue_probe(
    *,
    snapshot_uri: str,
    status_events: list[_ServerEvent],
    log_events: list[_ServerEvent],
) -> str:
    combined_lines: list[str] = []
    for event in status_events[-10:]:
        if event.message:
            combined_lines.append(event.message)
    for event in log_events[-10:]:
        if event.message:
            combined_lines.extend(line.strip() for line in event.message.splitlines() if line.strip())
    normalized_text = "\n".join(combined_lines)
    if not normalized_text:
        return "normalized_text_empty=true"
    return (
        f"contains_code_969={'Error in Java Model (code 969)' in normalized_text} "
        f"contains_java_model_exception={'Java Model Exception' in normalized_text} "
        f"contains_refreshing={'Refreshing' in normalized_text} "
        f"contains_snapshot_uri={snapshot_uri in normalized_text} "
        f"contains_compilation_unit={'does not resolve to a ICompilationUnit' in normalized_text} "
        f"contains_build_errors={'Errors occurred during the build' in normalized_text} "
        f"contains_initialization_failed={'Initialization failed' in normalized_text}"
    )[:1000]


def _extract_matching_line(text: str, keywords: tuple[str, ...]) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line and all(keyword in line for keyword in keywords):
            return _condense_text(line)
    return _condense_text(text)


def _condense_text(text: str) -> str:
    condensed = re.sub(r"\s+", " ", text).strip()
    return condensed[:220]


def _summarize_server_events(events: list[_ServerEvent]) -> str:
    if not events:
        return ""
    parts: list[str] = []
    for event in events[-3:]:
        message = _condense_text(event.message)
        if not message:
            continue
        if event.level:
            parts.append(f"{event.level}:{message}")
        else:
            parts.append(message)
    return " | ".join(parts)[:400]


def _condense_uri(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("file://"):
        path = Path(uri.removeprefix("file://"))
        parts = path.parts[-3:]
        return "/".join(parts) if parts else str(path)
    return _condense_text(uri)


def _summarize_publish_events(events: list[_PublishEvent], *, snapshot_uri: str) -> tuple[str, bool]:
    if not events:
        return "", False
    parts: list[str] = []
    received_other = False
    for event in events[-5:]:
        short_uri = _condense_uri(event.uri)
        if event.uri != snapshot_uri:
            received_other = True
        parts.append(f"{short_uri}#{event.sequence}({event.diagnostics_count})")
    return " | ".join(parts)[:400], received_other


_LSP_MANAGER = LspManager()
atexit.register(_LSP_MANAGER.shutdown_all)


def get_lsp_manager() -> LspManager:
    return _LSP_MANAGER


def clear_lsp_manager_state() -> None:
    _LSP_MANAGER.shutdown_all()
    _LSP_MANAGER._documents.clear()
