"""Tests for TypeScript LSP Server adapter."""

from pathlib import Path

from agent.lsp.servers.base import LspPreflightIssue
from agent.lsp.servers.typescript import TypeScriptLspServerAdapter, build_default_typescript_adapter


class TestTypeScriptLspServerAdapter:
    """Tests for TypeScriptLspServerAdapter."""

    def test_supports_file_should_return_true_for_typescript_family_files(self):
        adapter = TypeScriptLspServerAdapter()

        assert adapter.supports_file(Path("/foo/bar.ts")) is True
        assert adapter.supports_file(Path("/foo/bar.tsx")) is True
        assert adapter.supports_file(Path("/foo/bar.js")) is True
        assert adapter.supports_file(Path("/foo/bar.jsx")) is True

    def test_supports_file_should_return_false_for_non_typescript_files(self):
        adapter = TypeScriptLspServerAdapter()

        assert adapter.supports_file(Path("/foo/bar.py")) is False
        assert adapter.supports_file(Path("/foo/bar.java")) is False
        assert adapter.supports_file(Path("/foo/bar")) is False

    def test_get_language_id_should_match_file_type(self):
        adapter = TypeScriptLspServerAdapter()

        assert adapter.get_language_id(Path("/foo/bar.ts")) == "typescript"
        assert adapter.get_language_id(Path("/foo/bar.tsx")) == "typescriptreact"
        assert adapter.get_language_id(Path("/foo/bar.js")) == "javascript"
        assert adapter.get_language_id(Path("/foo/bar.jsx")) == "javascriptreact"

    def test_build_command_should_return_default_command(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()

        command = adapter.build_command(tmp_path)

        assert command == ["typescript-language-server", "--stdio"]

    def test_build_command_should_return_custom_command(self, tmp_path):
        adapter = TypeScriptLspServerAdapter(command=("custom-ts-lsp", "--stdio"))

        command = adapter.build_command(tmp_path)

        assert command == ["custom-ts-lsp", "--stdio"]

    def test_select_workspace_root_should_find_tsconfig_first(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        file_path = tmp_path / "src" / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_tsconfig.json"

    def test_select_workspace_root_should_find_package_json(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        file_path = tmp_path / "src" / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_package.json"

    def test_select_workspace_root_should_find_git_root(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        (tmp_path / ".git").mkdir()
        file_path = tmp_path / "src" / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_git_root"

    def test_select_workspace_root_should_fallback_to_file_directory(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        file_path = src_dir / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == src_dir
        assert reason == "workspace_boundary_fallback"

    def test_select_workspace_root_should_not_cross_workspace_boundary(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        outer_root = tmp_path / "outer"
        workspace_root = outer_root / "workspace"
        src_dir = workspace_root / "src"
        src_dir.mkdir(parents=True)
        (outer_root / "tsconfig.json").write_text("{}", encoding="utf-8")
        file_path = src_dir / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, workspace_root)

        assert root == src_dir
        assert reason == "workspace_boundary_fallback"

    def test_select_workspace_root_should_fallback_to_boundary_when_file_outside_workspace(self, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        workspace_root = tmp_path / "workspace"
        external_root = tmp_path / "external"
        workspace_root.mkdir()
        external_root.mkdir()
        (external_root / "tsconfig.json").write_text("{}", encoding="utf-8")
        file_path = external_root / "main.ts"

        root, reason = adapter.select_workspace_root_with_reason(file_path, workspace_root)

        assert root == workspace_root
        assert reason == "workspace_boundary_fallback"

    def test_detect_preflight_issue_should_report_missing_command(self, tmp_path):
        adapter = TypeScriptLspServerAdapter(command=())

        issue = adapter.detect_preflight_issue(file_path=tmp_path / "main.ts", workspace_root=tmp_path)

        assert isinstance(issue, LspPreflightIssue)
        assert issue.issue_code == "command_not_configured"
        assert "typescript-language-server" in issue.details["suggestion"]

    def test_detect_preflight_issue_should_report_missing_executable(self, monkeypatch, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        monkeypatch.setattr("agent.lsp.servers.typescript.shutil.which", lambda executable: None)

        issue = adapter.detect_preflight_issue(file_path=tmp_path / "main.ts", workspace_root=tmp_path)

        assert isinstance(issue, LspPreflightIssue)
        assert issue.issue_code == "executable_not_found"
        assert "typescript-language-server" in issue.message

    def test_detect_preflight_issue_should_pass_when_executable_exists(self, monkeypatch, tmp_path):
        adapter = TypeScriptLspServerAdapter()
        monkeypatch.setattr(
            "agent.lsp.servers.typescript.shutil.which",
            lambda executable: f"/opt/homebrew/bin/{executable}",
        )

        issue = adapter.detect_preflight_issue(file_path=tmp_path / "main.ts", workspace_root=tmp_path)

        assert issue is None


def test_build_default_typescript_adapter_should_read_settings(monkeypatch):
    class _Settings:
        languages = {
            "typescript": type(
                "_LanguageSettings",
                (),
                {
                    "command": ("typescript-language-server", "--stdio"),
                    "file_extensions": (".ts", ".tsx"),
                    "workspace_markers": ("tsconfig.json", "package.json"),
                    "init_options": {"preferences": {"includeCompletionsForModuleExports": True}},
                },
            )()
        }

    monkeypatch.setattr("agent.config.settings.get_lsp_settings", lambda: _Settings())

    adapter = build_default_typescript_adapter()

    assert adapter.build_command(Path("/tmp")) == ["typescript-language-server", "--stdio"]
    assert adapter.supports_file(Path("/tmp/demo.tsx")) is True
