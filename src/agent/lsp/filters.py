from __future__ import annotations

from pathlib import Path

from .types import LSP_SEVERITIES, LspDiagnostic, LspDiagnosticsResult

_SEVERITY_PRIORITY = {name: index for index, name in enumerate(LSP_SEVERITIES)}


def filter_diagnostics(
    diagnostics: list[LspDiagnostic],
    *,
    include_severity: tuple[str, ...],
    max_diagnostics: int,
    max_chars: int,
    lsp_language: str | None = None,
    lsp_server: str | None = None,
    lsp_server_pid: int | None = None,
    diagnostics_sequence: int = 0,
    diagnostics_previous_sequence: int = 0,
    diagnostics_latest_sequence: int = 0,
    diagnostics_wait_rounds: int = 0,
    diagnostics_wait_ms: int = 0,
    diagnostics_settled: bool = False,
    lsp_workspace_root: str | None = None,
    lsp_data_dir: str | None = None,
    lsp_workspace_selection_reason: str | None = None,
    lsp_server_key: str | None = None,
    lsp_snapshot_uri: str | None = None,
    recent_status_summary: str = "",
    recent_log_summary: str = "",
    recent_publish_uris: str = "",
    received_other_file_diagnostics: bool = False,
    java_project_issue_code: str | None = None,
    java_project_state: str | None = None,
    java_maven_profiles: tuple[str, ...] = (),
    java_maven_local_repository: str = "",
    java_debug_observation_enabled: bool = False,
    debug_status_events: str = "",
    debug_log_events: str = "",
    debug_publish_events: str = "",
    debug_issue_probe: str = "",
) -> LspDiagnosticsResult:
    filtered = [item for item in diagnostics if item.severity in include_severity]
    filtered.sort(
        key=lambda item: (
            _SEVERITY_PRIORITY.get(item.severity, len(_SEVERITY_PRIORITY)),
            item.range.start.line,
            item.range.start.character,
            item.message,
        )
    )

    deduped: list[LspDiagnostic] = []
    seen: set[tuple[object, ...]] = set()
    for item in filtered:
        signature = (
            item.severity,
            item.code,
            item.source,
            item.message,
            item.range.start.line,
            item.range.start.character,
            item.range.end.line,
            item.range.end.character,
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)

    total_before_truncation = len(deduped)
    selected: list[LspDiagnostic] = []
    consumed_chars = 0
    truncated = False
    for item in deduped:
        rendered_length = len(item.message)
        if selected and len(selected) >= max_diagnostics:
            truncated = True
            break
        if selected and consumed_chars + rendered_length > max_chars:
            truncated = True
            break
        if not selected and rendered_length > max_chars:
            item = LspDiagnostic(
                severity=item.severity,
                code=item.code,
                source=item.source,
                message=item.message[: max(0, max_chars - 1)] + "…",
                range=item.range,
            )
            truncated = True
        selected.append(item)
        consumed_chars += len(item.message)

    summary = build_diagnostics_summary(selected)
    status = "completed" if selected else "filtered_empty"
    return LspDiagnosticsResult(
        status=status,
        diagnostics=tuple(selected),
        diagnostics_total=len(selected),
        diagnostics_summary=summary,
        diagnostics_truncated=truncated or total_before_truncation > len(selected),
        lsp_language=lsp_language,
        lsp_server=lsp_server,
        lsp_server_pid=lsp_server_pid,
        raw_diagnostics_total=total_before_truncation,
        diagnostics_sequence=diagnostics_sequence,
        diagnostics_previous_sequence=diagnostics_previous_sequence,
        diagnostics_latest_sequence=diagnostics_latest_sequence,
        diagnostics_wait_rounds=diagnostics_wait_rounds,
        diagnostics_wait_ms=diagnostics_wait_ms,
        diagnostics_settled=diagnostics_settled,
        lsp_workspace_root=lsp_workspace_root,
        lsp_data_dir=lsp_data_dir,
        lsp_workspace_selection_reason=lsp_workspace_selection_reason,
        lsp_server_key=lsp_server_key,
        lsp_snapshot_uri=lsp_snapshot_uri,
        recent_status_summary=recent_status_summary,
        recent_log_summary=recent_log_summary,
        recent_publish_uris=recent_publish_uris,
        received_other_file_diagnostics=received_other_file_diagnostics,
        java_project_issue_code=java_project_issue_code,
        java_project_state=java_project_state,
        java_maven_profiles=java_maven_profiles,
        java_maven_local_repository=java_maven_local_repository,
        java_debug_observation_enabled=java_debug_observation_enabled,
        debug_status_events=debug_status_events,
        debug_log_events=debug_log_events,
        debug_publish_events=debug_publish_events,
        debug_issue_probe=debug_issue_probe,
    )


def build_diagnostics_summary(diagnostics: list[LspDiagnostic] | tuple[LspDiagnostic, ...]) -> str:
    if not diagnostics:
        return ""
    counts = {severity: 0 for severity in ("error", "warning", "information", "hint")}
    for item in diagnostics:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    parts = [f"{count} 个{severity}" for severity, count in counts.items() if count > 0]
    return "，".join(parts)


def render_diagnostics_excerpt(
    file_path: Path,
    diagnostics: list[LspDiagnostic] | tuple[LspDiagnostic, ...],
    *,
    severity: str = "error",
) -> str:
    matched = [item for item in diagnostics if item.severity == severity]
    if not matched:
        return ""

    level = severity.upper()
    lines = [
        f"{level} [{item.range.start.line + 1}:{item.range.start.character + 1}] {item.message}"
        for item in matched
    ]
    return (
        "\nLSP 检测到当前文件存在错误，请继续修复：\n"
        f"<diagnostics file=\"{file_path}\">\n"
        + "\n".join(lines)
        + "\n</diagnostics>"
    )
