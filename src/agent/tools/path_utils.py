from pathlib import Path

from ..runtime.workspace import get_workspace


def resolve_workspace_or_skills_path(path_str: str, *, allow_missing: bool = True) -> Path:
    """解析工作区或 skills 目录内路径，统一处理越界校验。"""
    workspace = get_workspace()
    raw_path = Path(path_str).expanduser()
    target = raw_path.resolve() if raw_path.is_absolute() else (workspace.root / raw_path).resolve()
    if not (target.is_relative_to(workspace.root) or target.is_relative_to(workspace.skills_dir)):
        raise ValueError(f"路径超出允许范围: {path_str}")
    if not allow_missing and not target.exists():
        raise FileNotFoundError(f"路径不存在: {path_str}")
    return target


def resolve_workspace_path(path_str: str, *, allow_missing: bool = True) -> Path:
    """把用户路径解析为工作区内绝对路径，统一处理相对路径与越界校验。"""
    workspace_root = get_workspace().root
    raw_path = Path(path_str).expanduser()
    target = raw_path.resolve() if raw_path.is_absolute() else (workspace_root / raw_path).resolve()
    if not target.is_relative_to(workspace_root):
        raise ValueError(f"路径超出工作区范围: {path_str}")
    if not allow_missing and not target.exists():
        raise FileNotFoundError(f"路径不存在: {path_str}")
    return target


def resolve_workspace_directory(path_str: str | None = None) -> Path:
    """解析并校验工作目录；未传时默认返回工作区根目录。"""
    workspace_root = get_workspace().root
    if path_str is None:
        return workspace_root

    target = resolve_workspace_path(path_str, allow_missing=False)
    if not target.is_dir():
        raise NotADirectoryError(f"目录路径不是目录: {path_str}")
    return target
