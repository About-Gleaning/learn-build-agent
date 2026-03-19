from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RUNTIME_HOME = Path(os.getenv("MY_AGENT_HOME", str(Path.home() / ".my-agent"))).expanduser().resolve()


@dataclass(frozen=True)
class WorkspaceRuntime:
    root: Path
    launch_mode: str
    runtime_home: Path
    workspaces_root: Path
    workspace_id: str
    workspace_home: Path
    sessions_dir: Path
    todo_root: Path
    plan_root: Path
    tool_output_root: Path
    logs_dir: Path

    @property
    def workspace_name(self) -> str:
        return self.root.name or str(self.root)

    @property
    def agents_md_path(self) -> Path:
        return self.root / "AGENTS.md"

    @property
    def has_agents_md(self) -> bool:
        return self.agents_md_path.exists()


_WORKSPACE_RUNTIME: WorkspaceRuntime | None = None
_WORKSPACE_EXPLICIT = False


def _normalize_root(path: str | Path | None) -> Path:
    candidate = Path.cwd() if path is None else Path(path)
    return candidate.expanduser().resolve()


def _build_workspace_id(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return digest[:16]


def build_session_storage_name(session_id: str, *, suffix: str = "") -> str:
    normalized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session_id).strip("._")
    safe_session_id = normalized or "default_session"
    return f"{safe_session_id}{suffix}"


def build_todo_storage_path(session_id: str) -> Path:
    workspace = get_workspace()
    return (workspace.todo_root / build_session_storage_name(session_id, suffix=".json")).resolve()


def build_plan_storage_path(session_id: str) -> Path:
    workspace = get_workspace()
    return (workspace.plan_root / build_session_storage_name(session_id, suffix=".md")).resolve()


def _build_workspace_runtime(root: Path, *, launch_mode: str) -> WorkspaceRuntime:
    workspace_id = _build_workspace_id(root)
    runtime_home = DEFAULT_RUNTIME_HOME.resolve()
    workspaces_root = (runtime_home / "workspaces").resolve()
    workspace_home = (workspaces_root / workspace_id).resolve()
    return WorkspaceRuntime(
        root=root,
        launch_mode=launch_mode,
        runtime_home=runtime_home,
        workspaces_root=workspaces_root,
        workspace_id=workspace_id,
        workspace_home=workspace_home,
        sessions_dir=(workspaces_root / "sessions").resolve(),
        todo_root=(workspaces_root / "todo").resolve(),
        plan_root=(workspaces_root / "plan").resolve(),
        tool_output_root=(workspaces_root / "tool-output").resolve(),
        logs_dir=(runtime_home / "logs").resolve(),
    )


def configure_workspace(root: str | Path | None = None, *, launch_mode: str = "cli", explicit: bool = True) -> WorkspaceRuntime:
    global _WORKSPACE_RUNTIME, _WORKSPACE_EXPLICIT
    normalized_root = _normalize_root(root)
    _WORKSPACE_RUNTIME = _build_workspace_runtime(normalized_root, launch_mode=launch_mode)
    _WORKSPACE_EXPLICIT = explicit
    return _WORKSPACE_RUNTIME


def get_workspace() -> WorkspaceRuntime:
    global _WORKSPACE_RUNTIME
    current_cwd = _normalize_root(None)
    if _WORKSPACE_RUNTIME is None:
        return configure_workspace(current_cwd, launch_mode="cli", explicit=False)
    if not _WORKSPACE_EXPLICIT and _WORKSPACE_RUNTIME.root != current_cwd:
        return configure_workspace(current_cwd, launch_mode=_WORKSPACE_RUNTIME.launch_mode, explicit=False)
    return _WORKSPACE_RUNTIME


def reset_workspace() -> None:
    global _WORKSPACE_RUNTIME, _WORKSPACE_EXPLICIT
    _WORKSPACE_RUNTIME = None
    _WORKSPACE_EXPLICIT = False
