"""Python LSP Server (python-lsp-server) adapter.

本模块提供 Python 语言服务器 (pylsp) 的适配器实现，
负责与 python-lsp-server 进行交互，提供代码诊断等功能。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ...config.settings import LspLanguageSettings
from .base import LspPreflightIssue, LspServerAdapter


class PyLspServerAdapter(LspServerAdapter):
    """Adapter for python-lsp-server (pylsp).

    Python LSP 服务器适配器，继承自 LspServerAdapter 基类。
    负责管理 pylsp 服务器的生命周期和配置。
    """

    # 语言标识符
    language = "python"
    language_id = "python"
    server_name = "pylsp"

    def __init__(
        self,
        *,
        command: tuple[str, ...] = ("pylsp",),
        file_extensions: tuple[str, ...] = (".py",),
        workspace_markers: tuple[str, ...] = (
            "pyproject.toml",
            "setup.py",
            "requirements.txt",
            "setup.cfg",
        ),
        init_options: dict[str, Any] | None = None,
    ) -> None:
        # 初始化配置参数
        self._command = command  # LSP 服务器启动命令
        self._file_extensions = file_extensions  # 支持的文件扩展名
        self._workspace_markers = workspace_markers  # 工作区标记文件
        self._init_options = init_options or {}  # 初始化选项

    def supports_file(self, file_path: Path) -> bool:
        """Check if this adapter supports the given file.

        检查此适配器是否支持给定的文件。
        通过比较文件扩展名是否在支持的扩展名列表中来判断。
        """
        return file_path.suffix.lower() in self._file_extensions

    def build_command(self, workspace_root: Path) -> list[str]:
        """Build the LSP server startup command.

        构建 LSP 服务器启动命令。
        将配置中定义的命令元组转换为列表格式返回。
        """
        return list(self._command)

    def build_initialize_params(self, workspace_root: Path) -> dict[str, Any]:
        """Build initialization parameters for pylsp.

        构建 pylsp 的初始化参数。
        包含进程 ID、工作区 URI、工作区文件夹、客户端能力等配置。
        """
        workspace_uri = workspace_root.resolve().as_uri()
        return {
            "processId": None,  # 当前进程 ID，设为 None 表示独立进程
            "rootUri": workspace_uri,  # 工作区根目录 URI
            "workspaceFolders": [
                {"uri": workspace_uri, "name": workspace_root.name}
            ],  # 工作区文件夹列表
            "capabilities": {
                "textDocument": {
                    "synchronization": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": True},  # 启用诊断信息发布
                }
            },
            "initializationOptions": self._init_options,  # 额外的初始化选项
        }

    def build_server_key(self, workspace_root: Path) -> str:
        """Build a unique server key for caching purposes.

        构建用于缓存的唯一服务器标识键。
        基于工作区路径的 SHA256 哈希值生成唯一标识符。
        """
        normalized = workspace_root.resolve().as_posix()
        hashed = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        return f"pylsp_{hashed}"

    def select_workspace_root(self, file_path: Path, workspace_root: Path) -> Path:
        """Select the workspace root for the given file.

        For Python, we search upward for common project markers.
        If none found, use the directory containing the file.
        """
        selected_root, _ = self.select_workspace_root_with_reason(file_path, workspace_root)
        return selected_root

    def _get_search_start(self, file_path: Path) -> Path:
        """Return the directory where upward workspace search should start."""
        return file_path.parent if file_path.suffix else file_path

    def _iter_search_roots(self, start: Path, boundary: Path) -> list[Path]:
        """Collect searchable directories without crossing the workspace boundary.

        Python LSP 只能在当前工作区内向上搜索项目标记，避免越过 `workspace_root`
        把外层仓库或兄弟目录错误地纳入当前 LSP 上下文。
        """
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

    def select_workspace_root_with_reason(
        self, file_path: Path, workspace_root: Path
    ) -> tuple[Path, str]:
        """Select the workspace root for the given file with reason."""
        current = self._get_search_start(file_path)
        search_roots = self._iter_search_roots(current, workspace_root)

        for parent in search_roots:
            for marker in self._workspace_markers:
                if (parent / marker).exists():
                    return parent, f"found_{marker}"

        for parent in search_roots:
            if (parent / ".git").exists():
                return parent, "found_git_root"

        # 边界内未命中任何 marker 时，继续使用当前文件目录，避免抬升到工作区外。
        return current, "workspace_boundary_fallback"

    def diagnostics_settle_ms(self) -> int:
        """Return the milliseconds to wait for diagnostics to settle.

        Pylsp is typically fast, no extra wait needed.
        """
        return 0

    def get_language_settings(self) -> LspLanguageSettings | None:
        """Get language-specific settings from project config."""
        from ...config.settings import get_lsp_settings

        settings = get_lsp_settings()
        if settings.languages is None:
            return None
        return settings.languages.get(self.language)

    def detect_preflight_issue(
        self, *, file_path: Path, workspace_root: Path
    ) -> LspPreflightIssue | None:
        """Detect preflight issues before starting LSP.

        Python doesn't require complex preflight checks like Maven.
        Just verify pylsp is available.
        """
        if not self._command:
            return LspPreflightIssue(
                message="Python LSP command not configured",
                issue_code="command_not_configured",
                project_state="lsp_not_configured",
                details={"suggestion": "Install python-lsp-server: pip install python-lsp-server"},
            )

        executable = self._command[0]
        if not shutil.which(executable):
            return LspPreflightIssue(
                message=f"Python LSP executable not found: {executable}",
                issue_code="executable_not_found",
                project_state="lsp_not_installed",
                details={"suggestion": "Install python-lsp-server: pip install python-lsp-server"},
            )

        return None


def build_default_python_adapter() -> PyLspServerAdapter:
    """Build the default Python LSP adapter with settings from config."""
    from ...config.settings import get_lsp_settings

    settings = get_lsp_settings()
    if settings.languages and "python" in settings.languages:
        lang_settings = settings.languages["python"]
        return PyLspServerAdapter(
            command=lang_settings.command,
            file_extensions=lang_settings.file_extensions,
            workspace_markers=lang_settings.workspace_markers,
            init_options=lang_settings.init_options,
        )
    return PyLspServerAdapter()
