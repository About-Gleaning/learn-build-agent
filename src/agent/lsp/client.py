from __future__ import annotations

from pathlib import Path

from ..config.settings import get_lsp_settings
from ..runtime.workspace import get_workspace
from .filters import render_diagnostics_excerpt
from .manager import clear_lsp_manager_state, get_lsp_manager
from .servers.base import LspServerAdapter
from .servers.jdtls import build_default_java_adapter
from .servers.pylsp import build_default_python_adapter
from .types import LspDiagnosticsResult, build_file_uri


class LspClient:
    def __init__(self) -> None:
        self._adapters: tuple[LspServerAdapter, ...] = (
            build_default_java_adapter(),
            build_default_python_adapter(),
        )

    def collect_diagnostics(self, *, file_path: Path, content: str) -> LspDiagnosticsResult:
        settings = get_lsp_settings()
        if not settings.enabled:
            return LspDiagnosticsResult(status="not_enabled")

        adapter = self._match_adapter(file_path)
        if adapter is None:
            return LspDiagnosticsResult(status="unsupported_language")

        language_settings = settings.languages.get(adapter.language)
        if language_settings is None or not language_settings.enabled:
            return LspDiagnosticsResult(status="not_enabled")

        try:
            result = get_lsp_manager().collect_diagnostics(adapter, file_path=file_path, content=content)
        except Exception as exc:
            workspace_root, workspace_selection_reason = adapter.select_workspace_root_with_reason(
                file_path.resolve(),
                get_workspace().root.resolve(),
            )
            resolved_java_settings = (
                adapter.resolve_maven_import_config(file_path=file_path.resolve(), workspace_root=workspace_root)
                if hasattr(adapter, "resolve_maven_import_config")
                else None
            )
            return LspDiagnosticsResult(
                status="server_unavailable",
                lsp_language=adapter.language,
                lsp_server=adapter.server_name,
                lsp_workspace_root=str(workspace_root),
                lsp_data_dir=str(adapter.build_data_dir(workspace_root, file_path=file_path.resolve())),
                lsp_workspace_selection_reason=workspace_selection_reason,
                lsp_server_key=adapter.build_server_key(workspace_root, file_path=file_path.resolve()),
                lsp_snapshot_uri=build_file_uri(file_path),
                lsp_error=str(exc)[:300],
                java_maven_profiles=(
                    resolved_java_settings.profiles if resolved_java_settings is not None else ()
                ),
                java_maven_profiles_source=(
                    resolved_java_settings.profiles_source if resolved_java_settings is not None else ""
                ),
                java_maven_local_repository=(
                    resolved_java_settings.local_repository
                    if resolved_java_settings is not None
                    else language_settings.maven_local_repository
                ),
            )
        excerpt = render_diagnostics_excerpt(file_path, result.diagnostics)
        return LspDiagnosticsResult(
            status=result.status,
            diagnostics=result.diagnostics,
            diagnostics_total=result.diagnostics_total,
            diagnostics_summary=result.diagnostics_summary,
            diagnostics_truncated=result.diagnostics_truncated,
            output_excerpt=excerpt,
            lsp_language=result.lsp_language,
            lsp_server=result.lsp_server,
            lsp_server_pid=result.lsp_server_pid,
            lsp_error=result.lsp_error,
            raw_diagnostics_total=result.raw_diagnostics_total,
            diagnostics_sequence=result.diagnostics_sequence,
            diagnostics_previous_sequence=result.diagnostics_previous_sequence,
            diagnostics_latest_sequence=result.diagnostics_latest_sequence,
            diagnostics_wait_rounds=result.diagnostics_wait_rounds,
            diagnostics_wait_ms=result.diagnostics_wait_ms,
            diagnostics_settled=result.diagnostics_settled,
            lsp_workspace_root=result.lsp_workspace_root,
            lsp_data_dir=result.lsp_data_dir,
            lsp_workspace_selection_reason=result.lsp_workspace_selection_reason,
            lsp_server_key=result.lsp_server_key,
            lsp_snapshot_uri=result.lsp_snapshot_uri,
            recent_status_summary=result.recent_status_summary,
            recent_log_summary=result.recent_log_summary,
            recent_publish_uris=result.recent_publish_uris,
            received_other_file_diagnostics=result.received_other_file_diagnostics,
            java_project_issue_code=result.java_project_issue_code,
            java_project_state=result.java_project_state,
            java_maven_profiles=result.java_maven_profiles,
            java_maven_profiles_source=result.java_maven_profiles_source,
            java_maven_local_repository=result.java_maven_local_repository,
            java_debug_observation_enabled=result.java_debug_observation_enabled,
            debug_status_events=result.debug_status_events,
            debug_log_events=result.debug_log_events,
            debug_publish_events=result.debug_publish_events,
            debug_issue_probe=result.debug_issue_probe,
        )

    def _match_adapter(self, file_path: Path) -> LspServerAdapter | None:
        for adapter in self._adapters:
            if adapter.supports_file(file_path):
                return adapter
        return None


_LSP_CLIENT = LspClient()


def get_lsp_client() -> LspClient:
    return _LSP_CLIENT


def collect_file_diagnostics(*, file_path: Path, content: str) -> LspDiagnosticsResult:
    return _LSP_CLIENT.collect_diagnostics(file_path=file_path, content=content)


def clear_lsp_runtime_state() -> None:
    clear_lsp_manager_state()
