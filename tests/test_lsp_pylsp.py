"""Tests for Python LSP Server (pylsp) adapter."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.lsp.servers.base import LspPreflightIssue
from agent.lsp.servers.pylsp import PyLspServerAdapter, build_default_python_adapter


class TestPyLspServerAdapter:
    """Tests for PyLspServerAdapter."""

    def test_supports_file_should_return_true_for_py_files(self):
        adapter = PyLspServerAdapter()

        assert adapter.supports_file(Path("/foo/bar.py")) is True
        assert adapter.supports_file(Path("/foo/bar.PY")) is True  # case insensitive
        assert adapter.supports_file(Path("foo.py")) is True

    def test_supports_file_should_return_false_for_non_py_files(self):
        adapter = PyLspServerAdapter()

        assert adapter.supports_file(Path("/foo/bar.java")) is False
        assert adapter.supports_file(Path("/foo/bar.txt")) is False
        assert adapter.supports_file(Path("/foo/bar")) is False

    def test_build_command_should_return_default_command(self, tmp_path):
        adapter = PyLspServerAdapter()

        command = adapter.build_command(tmp_path)

        assert command == ["pylsp"]

    def test_build_command_should_return_custom_command(self, tmp_path):
        adapter = PyLspServerAdapter(command=("custom-pylsp", "--verbose"))

        command = adapter.build_command(tmp_path)

        assert command == ["custom-pylsp", "--verbose"]

    def test_build_server_key_should_be_consistent_for_same_workspace(self, tmp_path):
        adapter = PyLspServerAdapter()

        key1 = adapter.build_server_key(tmp_path)
        key2 = adapter.build_server_key(tmp_path)

        assert key1 == key2
        assert key1.startswith("pylsp_")

    def test_build_server_key_should_differ_for_different_workspaces(self):
        adapter = PyLspServerAdapter()

        key1 = adapter.build_server_key(Path("/workspace/project1"))
        key2 = adapter.build_server_key(Path("/workspace/project2"))

        assert key1 != key2

    def test_select_workspace_root_should_find_pyproject_toml(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
        file_path = tmp_path / "src" / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        assert root == tmp_path

    def test_select_workspace_root_should_find_setup_py(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        file_path = tmp_path / "src" / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        assert root == tmp_path

    def test_select_workspace_root_should_find_requirements_txt(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "requirements.txt").write_text("requests==2.28.0")
        file_path = tmp_path / "src" / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        assert root == tmp_path

    def test_select_workspace_root_should_find_git_root(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / ".git").mkdir()
        file_path = tmp_path / "src" / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        assert root == tmp_path

    def test_select_workspace_root_should_fallback_to_file_directory(self, tmp_path):
        adapter = PyLspServerAdapter()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        file_path = src_dir / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        # Since main.py doesn't exist, and it has .py suffix, use its parent
        assert root == src_dir

    def test_select_workspace_root_should_search_upwards(self, tmp_path):
        adapter = PyLspServerAdapter()
        # Create project structure: root/pyproject.toml, root/src/pkg/main.py
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
        pkg_dir = tmp_path / "src" / "pkg"
        pkg_dir.mkdir(parents=True)
        file_path = pkg_dir / "main.py"

        root = adapter.select_workspace_root(file_path, tmp_path)

        assert root == tmp_path

    def test_select_workspace_root_should_not_cross_workspace_boundary_for_marker(self, tmp_path):
        adapter = PyLspServerAdapter()
        outer_root = tmp_path / "outer"
        workspace_root = outer_root / "workspace"
        pkg_dir = workspace_root / "src" / "pkg"
        pkg_dir.mkdir(parents=True)
        (outer_root / "pyproject.toml").write_text("[project]\nname = 'outer'")
        file_path = pkg_dir / "main.py"

        root = adapter.select_workspace_root(file_path, workspace_root)

        assert root == pkg_dir

    def test_select_workspace_root_should_not_cross_workspace_boundary_for_git_root(self, tmp_path):
        adapter = PyLspServerAdapter()
        outer_root = tmp_path / "outer"
        workspace_root = outer_root / "workspace"
        pkg_dir = workspace_root / "src" / "pkg"
        pkg_dir.mkdir(parents=True)
        (outer_root / ".git").mkdir(parents=True)
        file_path = pkg_dir / "main.py"

        root = adapter.select_workspace_root(file_path, workspace_root)

        assert root == pkg_dir

    def test_select_workspace_root_with_reason_should_find_pyproject_toml(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
        file_path = tmp_path / "src" / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_pyproject.toml"

    def test_select_workspace_root_with_reason_should_find_setup_py(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        file_path = tmp_path / "src" / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_setup.py"

    def test_select_workspace_root_with_reason_should_find_requirements_txt(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / "requirements.txt").write_text("requests==2.28.0")
        file_path = tmp_path / "src" / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_requirements.txt"

    def test_select_workspace_root_with_reason_should_find_git_root(self, tmp_path):
        adapter = PyLspServerAdapter()
        (tmp_path / ".git").mkdir()
        file_path = tmp_path / "src" / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        assert root == tmp_path
        assert reason == "found_git_root"

    def test_select_workspace_root_with_reason_should_fallback_to_file_directory(self, tmp_path):
        adapter = PyLspServerAdapter()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        file_path = src_dir / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, tmp_path)

        # Since main.py doesn't exist, and it has .py suffix, use its parent
        assert root == src_dir
        assert reason == "workspace_boundary_fallback"

    def test_select_workspace_root_with_reason_should_return_boundary_fallback_when_outer_marker_exists(self, tmp_path):
        adapter = PyLspServerAdapter()
        outer_root = tmp_path / "outer"
        workspace_root = outer_root / "workspace"
        pkg_dir = workspace_root / "src" / "pkg"
        pkg_dir.mkdir(parents=True)
        (outer_root / "pyproject.toml").write_text("[project]\nname = 'outer'")
        file_path = pkg_dir / "main.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, workspace_root)

        assert root == pkg_dir
        assert reason == "workspace_boundary_fallback"

    def test_select_workspace_root_with_reason_should_fallback_to_boundary_when_file_outside_workspace(self, tmp_path):
        adapter = PyLspServerAdapter()
        workspace_root = tmp_path / "workspace"
        external_root = tmp_path / "external"
        external_root.mkdir()
        workspace_root.mkdir()
        (external_root / "pyproject.toml").write_text("[project]\nname='external'")
        file_path = external_root / "skills" / "tool.py"

        root, reason = adapter.select_workspace_root_with_reason(file_path, workspace_root)

        assert root == workspace_root
        assert reason == "workspace_boundary_fallback"

    def test_diagnostics_settle_ms_should_return_zero(self):
        adapter = PyLspServerAdapter()

        assert adapter.diagnostics_settle_ms() == 0

    def test_build_data_dir_should_follow_workspace_home(self, tmp_path, monkeypatch):
        adapter = PyLspServerAdapter()
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()

        @dataclass(frozen=True)
        class FakeWorkspace:
            workspace_home: Path

        fake_workspace = FakeWorkspace(workspace_home=tmp_path / "runtime-home" / "workspace")
        monkeypatch.setattr("agent.lsp.servers.base.get_workspace", lambda: fake_workspace)

        data_dir = adapter.build_data_dir(workspace_root)

        assert data_dir.is_relative_to(fake_workspace.workspace_home / "lsp" / "python")
        assert str(data_dir).startswith(str(fake_workspace.workspace_home))

    def test_build_initialize_params_should_contain_workspace_info(self, tmp_path):
        adapter = PyLspServerAdapter()

        params = adapter.build_initialize_params(tmp_path)

        assert params["rootUri"] == tmp_path.resolve().as_uri()
        assert params["workspaceFolders"] == [
            {"uri": tmp_path.resolve().as_uri(), "name": tmp_path.name}
        ]
        assert "capabilities" in params
        assert params["initializationOptions"] == {}

    def test_build_initialize_params_should_include_custom_init_options(self, tmp_path):
        adapter = PyLspServerAdapter(init_options={"pylsp": {"plugins": {"pycodestyle": {"enabled": False}}}})

        params = adapter.build_initialize_params(tmp_path)

        assert params["initializationOptions"] == {"pylsp": {"plugins": {"pycodestyle": {"enabled": False}}}}


class TestDetectPreflightIssue:
    """Tests for detect_preflight_issue method."""

    def test_should_return_none_when_pylsp_available(self, tmp_path, monkeypatch):
        adapter = PyLspServerAdapter()
        monkeypatch.setattr("agent.lsp.servers.pylsp.shutil.which", lambda x: "/usr/bin/pylsp")
        file_path = tmp_path / "test.py"

        result = adapter.detect_preflight_issue(file_path=file_path, workspace_root=tmp_path)

        assert result is None

    def test_should_return_error_when_pylsp_not_found(self, tmp_path, monkeypatch):
        adapter = PyLspServerAdapter()
        monkeypatch.setattr("agent.lsp.servers.pylsp.shutil.which", lambda x: None)
        file_path = tmp_path / "test.py"

        result = adapter.detect_preflight_issue(file_path=file_path, workspace_root=tmp_path)

        assert result is not None
        assert isinstance(result, LspPreflightIssue)
        assert "pylsp" in result.message
        assert "pip install python-lsp-server" in result.details.get("suggestion", "")

    def test_should_return_error_when_command_empty(self, tmp_path):
        adapter = PyLspServerAdapter(command=())
        file_path = tmp_path / "test.py"

        result = adapter.detect_preflight_issue(file_path=file_path, workspace_root=tmp_path)

        assert result is not None
        assert isinstance(result, LspPreflightIssue)
        assert result.issue_code == "command_not_configured"


class TestBuildDefaultPythonAdapter:
    """Tests for build_default_python_adapter function."""

    def test_should_use_config_settings_when_available(self, monkeypatch):
        from agent.config.settings import LspLanguageSettings, LspSettings

        def mock_get_settings():
            return LspSettings(
                enabled=True,
                ide_enabled=False,
                startup_mode="on_demand",
                server_idle_ttl_seconds=60,
                request_timeout_ms=1000,
                max_diagnostics=2,
                max_chars=40,
                include_severity=("error", "warning"),
                strict_unavailable=False,
                languages={
                    "python": LspLanguageSettings(
                        enabled=True,
                        command=("custom-pylsp",),
                        file_extensions=(".py", ".pyw"),
                        workspace_markers=("pyproject.toml",),
                        init_options={"pylsp": {"plugins": {"pylint": {"enabled": True}}}},
                        maven_profiles=(),
                        maven_local_repository="",
                    )
                },
                ide=None,
            )

        monkeypatch.setattr("agent.config.settings.get_lsp_settings", mock_get_settings)

        adapter = build_default_python_adapter()

        assert adapter._command == ("custom-pylsp",)
        assert adapter._file_extensions == (".py", ".pyw")
        assert adapter._workspace_markers == ("pyproject.toml",)
        assert adapter._init_options == {"pylsp": {"plugins": {"pylint": {"enabled": True}}}}

    def test_should_use_defaults_when_config_unavailable(self, monkeypatch):
        from agent.config.settings import LspSettings

        def mock_get_settings():
            return LspSettings(
                enabled=True,
                ide_enabled=False,
                startup_mode="on_demand",
                server_idle_ttl_seconds=60,
                request_timeout_ms=1000,
                max_diagnostics=2,
                max_chars=40,
                include_severity=("error", "warning"),
                strict_unavailable=False,
                languages={},
                ide=None,
            )

        monkeypatch.setattr("agent.config.settings.get_lsp_settings", mock_get_settings)

        adapter = build_default_python_adapter()

        assert adapter._command == ("pylsp",)
        assert adapter._file_extensions == (".py",)
        assert adapter._workspace_markers == ("pyproject.toml", "setup.py", "requirements.txt", "setup.cfg")
