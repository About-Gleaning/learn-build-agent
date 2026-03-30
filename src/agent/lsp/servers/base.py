from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ...config.settings import LspLanguageSettings, get_lsp_settings
from ...runtime.workspace import get_workspace


@dataclass(frozen=True)
class LspPreflightIssue:
    message: str
    issue_code: str
    project_state: str
    details: dict[str, str] = field(default_factory=dict)


class LspServerAdapter(ABC):
    language: str = ""
    adapter_mode: str = "direct_lsp"
    language_id: str = ""
    server_name: str = ""

    def get_language_settings(self) -> LspLanguageSettings:
        settings = get_lsp_settings()
        config = settings.languages.get(self.language)
        if config is None:
            raise ValueError(f"未配置语言: {self.language}")
        return config

    def supports_file(self, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        return suffix in self.get_language_settings().file_extensions

    def build_server_key(self, workspace_root: Path) -> str:
        return f"{self.language}:{workspace_root.resolve()}:{self.adapter_mode}"

    def detect_preflight_issue(self, *, file_path: Path, workspace_root: Path) -> LspPreflightIssue | None:
        return None

    def build_data_dir(self, workspace_root: Path) -> Path:
        workspace = get_workspace()
        # 按 server_key 隔离运行态目录，避免同一 workspace_root 下不同 profile/模式复用旧缓存。
        digest = hashlib.sha256(self.build_server_key(workspace_root).encode("utf-8")).hexdigest()[:16]
        return (workspace.workspace_home / "lsp" / self.language / digest).resolve()

    @abstractmethod
    def select_workspace_root(self, file_path: Path, workspace_root: Path) -> Path:
        raise NotImplementedError

    def select_workspace_root_with_reason(self, file_path: Path, workspace_root: Path) -> tuple[Path, str]:
        return self.select_workspace_root(file_path, workspace_root), "workspace_marker_root"

    @abstractmethod
    def build_command(self, workspace_root: Path) -> list[str]:
        raise NotImplementedError

    def build_initialize_params(self, workspace_root: Path) -> dict[str, Any]:
        return {
            "processId": os.getpid(),
            "rootUri": workspace_root.resolve().as_uri(),
            "workspaceFolders": [
                {
                    "uri": workspace_root.resolve().as_uri(),
                    "name": workspace_root.name,
                }
            ],
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {},
                    "synchronization": {
                        "didSave": True,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                    },
                }
            },
            "initializationOptions": dict(self.get_language_settings().init_options),
        }

    def diagnostics_settle_ms(self) -> int:
        return 150
