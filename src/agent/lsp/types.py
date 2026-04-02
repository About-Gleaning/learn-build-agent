from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LSP_SEVERITIES = ("error", "warning", "information", "hint")


@dataclass(frozen=True)
class LspPosition:
    line: int
    character: int


@dataclass(frozen=True)
class LspRange:
    start: LspPosition
    end: LspPosition


@dataclass(frozen=True)
class LspDiagnostic:
    severity: str
    message: str
    code: str | None
    source: str | None
    range: LspRange

    def to_metadata(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "range": {
                "start": {
                    "line": self.range.start.line,
                    "character": self.range.start.character,
                },
                "end": {
                    "line": self.range.end.line,
                    "character": self.range.end.character,
                },
            },
        }


@dataclass(frozen=True)
class DocumentSnapshot:
    file_path: str
    uri: str
    language_id: str
    version: int
    current_text: str
    opened: bool
    last_synced_at_ns: int


@dataclass(frozen=True)
class LspServerStatus:
    server_key: str
    server_name: str
    workspace_root: str
    language: str
    adapter_mode: str
    pid: int | None
    data_dir: str = ""
    workspace_selection_reason: str = ""
    java_maven_profiles: tuple[str, ...] = ()
    java_maven_profiles_source: str = ""
    java_maven_local_repository: str = ""


@dataclass(frozen=True)
class LspDiagnosticsResult:
    status: str
    diagnostics: tuple[LspDiagnostic, ...] = ()
    diagnostics_total: int = 0
    diagnostics_summary: str = ""
    diagnostics_truncated: bool = False
    output_excerpt: str = ""
    lsp_language: str | None = None
    lsp_server: str | None = None
    lsp_server_pid: int | None = None
    lsp_error: str | None = None
    raw_diagnostics_total: int = 0
    diagnostics_sequence: int = 0
    diagnostics_previous_sequence: int = 0
    diagnostics_latest_sequence: int = 0
    diagnostics_wait_rounds: int = 0
    diagnostics_wait_ms: int = 0
    diagnostics_settled: bool = False
    lsp_workspace_root: str | None = None
    lsp_data_dir: str | None = None
    lsp_workspace_selection_reason: str | None = None
    lsp_server_key: str | None = None
    lsp_snapshot_uri: str | None = None
    recent_status_summary: str = ""
    recent_log_summary: str = ""
    recent_publish_uris: str = ""
    received_other_file_diagnostics: bool = False
    java_project_issue_code: str | None = None
    java_project_state: str | None = None
    java_maven_profiles: tuple[str, ...] = ()
    java_maven_profiles_source: str = ""
    java_maven_local_repository: str = ""
    java_debug_observation_enabled: bool = False
    debug_status_events: str = ""
    debug_log_events: str = ""
    debug_publish_events: str = ""
    debug_issue_probe: str = ""

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "diagnostics": [item.to_metadata() for item in self.diagnostics],
            "diagnostics_status": self.status,
            "diagnostics_summary": self.diagnostics_summary,
            "diagnostics_total": self.diagnostics_total,
            "diagnostics_truncated": self.diagnostics_truncated,
            "raw_diagnostics_total": self.raw_diagnostics_total,
            "diagnostics_sequence": self.diagnostics_sequence,
            "diagnostics_previous_sequence": self.diagnostics_previous_sequence,
            "diagnostics_latest_sequence": self.diagnostics_latest_sequence,
            "diagnostics_wait_rounds": self.diagnostics_wait_rounds,
            "diagnostics_wait_ms": self.diagnostics_wait_ms,
            "diagnostics_settled": self.diagnostics_settled,
            "recent_status_summary": self.recent_status_summary,
            "recent_log_summary": self.recent_log_summary,
            "recent_publish_uris": self.recent_publish_uris,
            "received_other_file_diagnostics": self.received_other_file_diagnostics,
            "java_debug_observation_enabled": self.java_debug_observation_enabled,
        }
        if self.java_project_issue_code:
            metadata["java_project_issue_code"] = self.java_project_issue_code
        if self.java_project_state:
            metadata["java_project_state"] = self.java_project_state
        if self.java_maven_profiles:
            metadata["java_maven_profiles"] = list(self.java_maven_profiles)
        if self.java_maven_profiles_source:
            metadata["java_maven_profiles_source"] = self.java_maven_profiles_source
        if self.java_maven_local_repository:
            metadata["java_maven_local_repository"] = self.java_maven_local_repository
        if self.debug_status_events:
            metadata["debug_status_events"] = self.debug_status_events
        if self.debug_log_events:
            metadata["debug_log_events"] = self.debug_log_events
        if self.debug_publish_events:
            metadata["debug_publish_events"] = self.debug_publish_events
        if self.debug_issue_probe:
            metadata["debug_issue_probe"] = self.debug_issue_probe
        if self.lsp_language:
            metadata["lsp_language"] = self.lsp_language
        if self.lsp_server:
            metadata["lsp_server"] = self.lsp_server
        if self.lsp_server_pid is not None:
            metadata["lsp_server_pid"] = self.lsp_server_pid
        if self.lsp_workspace_root:
            metadata["lsp_workspace_root"] = self.lsp_workspace_root
        if self.lsp_data_dir:
            metadata["lsp_data_dir"] = self.lsp_data_dir
        if self.lsp_workspace_selection_reason:
            metadata["lsp_workspace_selection_reason"] = self.lsp_workspace_selection_reason
        if self.lsp_server_key:
            metadata["lsp_server_key"] = self.lsp_server_key
        if self.lsp_snapshot_uri:
            metadata["lsp_snapshot_uri"] = self.lsp_snapshot_uri
        if self.lsp_error:
            metadata["lsp_error"] = self.lsp_error
        return metadata

    def build_observation_excerpt(self) -> str:
        lines: list[str] = []
        if self.lsp_workspace_root:
            lines.append(f"workspace_root={self.lsp_workspace_root}")
        if self.lsp_data_dir:
            lines.append(f"data_dir={self.lsp_data_dir}")
        if self.lsp_workspace_selection_reason:
            lines.append(f"workspace_selection_reason={self.lsp_workspace_selection_reason}")
        if self.lsp_server_key:
            lines.append(f"server_key={self.lsp_server_key}")
        if self.lsp_snapshot_uri:
            lines.append(f"snapshot_uri={self.lsp_snapshot_uri}")
        if self.diagnostics_previous_sequence or self.diagnostics_latest_sequence:
            lines.append(
                "diagnostics_sequence="
                f"{self.diagnostics_previous_sequence}->{self.diagnostics_latest_sequence}"
            )
        if self.recent_status_summary:
            lines.append(f"recent_status={self.recent_status_summary}")
        if self.recent_log_summary:
            lines.append(f"recent_log={self.recent_log_summary}")
        if self.recent_publish_uris:
            lines.append(f"recent_publish={self.recent_publish_uris}")
        if self.received_other_file_diagnostics:
            lines.append("received_other_file_diagnostics=true")
        if self.java_project_issue_code:
            lines.append(f"java_project_issue_code={self.java_project_issue_code}")
        if self.java_project_state:
            lines.append(f"java_project_state={self.java_project_state}")
        if self.java_maven_profiles:
            lines.append(f"java_maven_profiles={','.join(self.java_maven_profiles)}")
        if self.java_maven_profiles_source:
            lines.append(f"java_maven_profiles_source={self.java_maven_profiles_source}")
        if self.java_maven_local_repository:
            lines.append(f"java_maven_local_repository={self.java_maven_local_repository}")
        if not lines:
            return ""
        return "\nLSP 观测信息：\n" + "\n".join(lines)

    def build_llm_excerpt(self) -> str:
        lines: list[str] = [f"LSP 状态：{self.status}"]

        summary = self._build_llm_summary()
        if summary:
            lines.append(f"摘要：{summary}")

        reason = self._build_llm_reason()
        if reason:
            lines.append(f"原因：{reason}")

        hint = self._build_llm_hint()
        if hint:
            lines.append(f"补充：{hint}")

        if self.output_excerpt:
            lines.append(self.output_excerpt.lstrip("\n"))

        return "\n" + "\n".join(lines)

    def _has_error_diagnostics(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    def _build_llm_summary(self) -> str:
        if self.status in {"completed", "filtered_empty"}:
            if self._has_error_diagnostics():
                if self.diagnostics_summary:
                    return f"当前文件存在 {self.diagnostics_summary}，请继续修复当前文件。"
                return "当前文件存在 error 级别 diagnostics，请继续修复当前文件。"
            return "当前文件未发现 error 级别 diagnostics。"
        if self.status == "server_unavailable":
            return "LSP 当前不可用，本次无法提供 diagnostics。"
        if self.status == "project_import_failed":
            return "工程导入失败，当前 diagnostics 不可用，请先修复工程配置问题。"
        if self.status == "timeout_degraded":
            return "本次 diagnostics 未完整返回，请谨慎依赖本次结果。"
        if self.status == "not_enabled":
            return "当前未启用 LSP diagnostics。"
        if self.status == "unsupported_language":
            return "当前文件类型暂未接入 LSP diagnostics。"
        return ""

    def _build_llm_reason(self) -> str:
        if self.lsp_error:
            return self.lsp_error
        return ""

    def _build_llm_hint(self) -> str:
        hints: list[str] = []
        if self.java_project_issue_code:
            issue = self.java_project_issue_code
            if self.java_project_state:
                issue += f"（{self.java_project_state}）"
            hints.append(f"工程问题标记：{issue}")
        elif self.java_project_state:
            hints.append(f"工程状态：{self.java_project_state}")
        if self.lsp_workspace_selection_reason and self.status in {"timeout_degraded", "project_import_failed"}:
            hints.append(f"workspace_selection_reason={self.lsp_workspace_selection_reason}")
        if self.recent_publish_uris and self.status == "timeout_degraded":
            hints.append(f"recent_publish={self.recent_publish_uris}")
        return "；".join(hints)


@dataclass(frozen=True)
class LspQueryResult:
    status: str
    operation: str
    result: Any = None
    result_count: int | None = None
    call_hierarchy_item: dict[str, Any] | None = None
    lsp_language: str | None = None
    lsp_server: str | None = None
    lsp_server_pid: int | None = None
    lsp_error: str | None = None
    lsp_workspace_root: str | None = None
    lsp_data_dir: str | None = None
    lsp_workspace_selection_reason: str | None = None
    lsp_server_key: str | None = None
    lsp_snapshot_uri: str | None = None
    recent_status_summary: str = ""
    recent_log_summary: str = ""
    recent_publish_uris: str = ""
    received_other_file_diagnostics: bool = False
    java_project_issue_code: str | None = None
    java_project_state: str | None = None
    java_maven_profiles: tuple[str, ...] = ()
    java_maven_profiles_source: str = ""
    java_maven_local_repository: str = ""
    java_debug_observation_enabled: bool = False
    debug_status_events: str = ""
    debug_log_events: str = ""
    debug_publish_events: str = ""
    debug_issue_probe: str = ""

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "lsp_operation": self.operation,
            "result": self.result,
            "result_count": self.result_count,
        }
        if self.call_hierarchy_item is not None:
            metadata["call_hierarchy_item"] = self.call_hierarchy_item
        if self.java_project_issue_code:
            metadata["java_project_issue_code"] = self.java_project_issue_code
        if self.java_project_state:
            metadata["java_project_state"] = self.java_project_state
        if self.java_maven_profiles:
            metadata["java_maven_profiles"] = list(self.java_maven_profiles)
        if self.java_maven_profiles_source:
            metadata["java_maven_profiles_source"] = self.java_maven_profiles_source
        if self.java_maven_local_repository:
            metadata["java_maven_local_repository"] = self.java_maven_local_repository
        if self.debug_status_events:
            metadata["debug_status_events"] = self.debug_status_events
        if self.debug_log_events:
            metadata["debug_log_events"] = self.debug_log_events
        if self.debug_publish_events:
            metadata["debug_publish_events"] = self.debug_publish_events
        if self.debug_issue_probe:
            metadata["debug_issue_probe"] = self.debug_issue_probe
        if self.lsp_language:
            metadata["lsp_language"] = self.lsp_language
        if self.lsp_server:
            metadata["lsp_server"] = self.lsp_server
        if self.lsp_server_pid is not None:
            metadata["lsp_server_pid"] = self.lsp_server_pid
        if self.lsp_workspace_root:
            metadata["lsp_workspace_root"] = self.lsp_workspace_root
        if self.lsp_data_dir:
            metadata["lsp_data_dir"] = self.lsp_data_dir
        if self.lsp_workspace_selection_reason:
            metadata["lsp_workspace_selection_reason"] = self.lsp_workspace_selection_reason
        if self.lsp_server_key:
            metadata["lsp_server_key"] = self.lsp_server_key
        if self.lsp_snapshot_uri:
            metadata["lsp_snapshot_uri"] = self.lsp_snapshot_uri
        if self.recent_status_summary:
            metadata["recent_status_summary"] = self.recent_status_summary
        if self.recent_log_summary:
            metadata["recent_log_summary"] = self.recent_log_summary
        if self.recent_publish_uris:
            metadata["recent_publish_uris"] = self.recent_publish_uris
        metadata["received_other_file_diagnostics"] = self.received_other_file_diagnostics
        metadata["java_debug_observation_enabled"] = self.java_debug_observation_enabled
        if self.lsp_error:
            metadata["lsp_error"] = self.lsp_error
        return metadata


@dataclass(frozen=True)
class LspLanguageServerConfig:
    enabled: bool
    command: tuple[str, ...]
    file_extensions: tuple[str, ...]
    workspace_markers: tuple[str, ...]
    init_options: dict[str, Any] = field(default_factory=dict)
    maven_local_repository: str = ""


def build_file_uri(file_path: Path) -> str:
    return file_path.resolve().as_uri()
