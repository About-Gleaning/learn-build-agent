"""TypeScript LSP Server adapter.

本模块提供 TypeScript 语言服务器适配器，
统一接入 TypeScript/JavaScript 代码导航与 diagnostics 能力。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .base import LspPreflightIssue, LspServerAdapter


class TypeScriptLspServerAdapter(LspServerAdapter):
    """Adapter for typescript-language-server."""

    language = "typescript"
    language_id = "typescript"
    server_name = "typescript-language-server"

    def __init__(
        self,
        *,
        command: tuple[str, ...] = ("typescript-language-server", "--stdio"),
        file_extensions: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx"),
        workspace_markers: tuple[str, ...] = ("tsconfig.json", "package.json"),
        init_options: dict[str, Any] | None = None,
    ) -> None:
        self._command = command
        self._file_extensions = file_extensions
        self._workspace_markers = workspace_markers
        self._init_options = init_options or {}

    def supports_file(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self._file_extensions

    def get_language_id(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".tsx":
            return "typescriptreact"
        if suffix == ".js":
            return "javascript"
        if suffix == ".jsx":
            return "javascriptreact"
        return "typescript"

    def build_command(self, workspace_root: Path) -> list[str]:
        del workspace_root
        return list(self._command)

    def build_initialize_params(self, workspace_root: Path, *, file_path: Path | None = None) -> dict[str, Any]:
        params = super().build_initialize_params(workspace_root, file_path=file_path)
        params["initializationOptions"] = dict(self._init_options)
        return params

    def select_workspace_root(self, file_path: Path, workspace_root: Path) -> Path:
        selected_root, _ = self.select_workspace_root_with_reason(file_path, workspace_root)
        return selected_root

    def _get_search_start(self, file_path: Path) -> Path:
        return file_path.parent if file_path.suffix else file_path

    def _iter_search_roots(self, start: Path, boundary: Path) -> list[Path]:
        start_resolved = start.resolve()
        boundary_resolved = boundary.resolve()
        try:
            start_resolved.relative_to(boundary_resolved)
        except ValueError:
            return [boundary_resolved]

        search_roots: list[Path] = []
        current = start_resolved
        while True:
            search_roots.append(current)
            if current == boundary_resolved or current.parent == current:
                break
            current = current.parent
        return search_roots

    def select_workspace_root_with_reason(self, file_path: Path, workspace_root: Path) -> tuple[Path, str]:
        current = self._get_search_start(file_path)
        search_roots = self._iter_search_roots(current, workspace_root)

        for marker in self._workspace_markers:
            for parent in search_roots:
                if (parent / marker).exists():
                    return parent, f"found_{marker}"

        for parent in search_roots:
            if (parent / ".git").exists():
                return parent, "found_git_root"

        return search_roots[0], "workspace_boundary_fallback"

    def diagnostics_settle_ms(self) -> int:
        return 0

    def detect_preflight_issue(self, *, file_path: Path, workspace_root: Path) -> LspPreflightIssue | None:
        del file_path, workspace_root
        if not self._command:
            return LspPreflightIssue(
                message="TypeScript LSP 命令未配置",
                issue_code="command_not_configured",
                project_state="lsp_not_configured",
                details={
                    "suggestion": "请先安装 typescript-language-server：npm install -g typescript typescript-language-server"
                },
            )

        executable = self._command[0]
        if shutil.which(executable):
            return None

        return LspPreflightIssue(
            message=f"TypeScript LSP 未找到可执行文件：{executable}",
            issue_code="executable_not_found",
            project_state="lsp_not_installed",
            details={
                "suggestion": "请先安装 typescript-language-server：npm install -g typescript typescript-language-server"
            },
        )


def build_default_typescript_adapter() -> TypeScriptLspServerAdapter:
    from ...config.settings import get_lsp_settings

    settings = get_lsp_settings()
    if settings.languages and "typescript" in settings.languages:
        lang_settings = settings.languages["typescript"]
        return TypeScriptLspServerAdapter(
            command=lang_settings.command,
            file_extensions=lang_settings.file_extensions,
            workspace_markers=lang_settings.workspace_markers,
            init_options=lang_settings.init_options,
        )
    return TypeScriptLspServerAdapter()
