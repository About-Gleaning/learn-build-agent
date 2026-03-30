from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from pathlib import Path
from xml.sax.saxutils import escape

from .base import LspPreflightIssue, LspServerAdapter


_JAVA_VERSION_PATTERN = re.compile(r'version "(?P<version>[^"]+)"')


class JdtlsServerAdapter(LspServerAdapter):
    language = "java"
    language_id = "java"
    server_name = "jdtls"

    def select_workspace_root(self, file_path: Path, workspace_root: Path) -> Path:
        selected_root, _ = self.select_workspace_root_with_reason(file_path, workspace_root)
        return selected_root

    def select_workspace_root_with_reason(self, file_path: Path, workspace_root: Path) -> tuple[Path, str]:
        boundary = workspace_root.resolve()
        nearest_marker_root = self._find_nearest_marker_root(file_path.resolve(), boundary)
        if nearest_marker_root is None:
            return boundary, "workspace_boundary_fallback"

        if (nearest_marker_root / "pom.xml").exists():
            aggregator_root = self._find_topmost_maven_aggregator_root(nearest_marker_root, boundary)
            if aggregator_root is not None:
                return aggregator_root, "maven_aggregator_root"
            return nearest_marker_root, "maven_nearest_module"

        if (nearest_marker_root / "build.gradle").exists() or (nearest_marker_root / "settings.gradle").exists():
            return nearest_marker_root, "gradle_marker_root"
        return nearest_marker_root, "workspace_marker_root"

    def build_server_key(self, workspace_root: Path) -> str:
        base_key = super().build_server_key(workspace_root)
        profiles = self.get_language_settings().maven_profiles
        local_repository = self.get_language_settings().maven_local_repository.strip()
        suffix_parts: list[str] = []
        if profiles:
            suffix_parts.append(f"maven_profiles={','.join(profiles)}")
        if local_repository:
            suffix_parts.append(f"maven_local_repository={local_repository}")
        if not suffix_parts:
            return base_key
        return f"{base_key}:{':'.join(suffix_parts)}"

    def build_initialize_params(self, workspace_root: Path) -> dict[str, object]:
        params = super().build_initialize_params(workspace_root)
        profiles = self.get_language_settings().maven_profiles
        local_repository = self.get_language_settings().maven_local_repository.strip()
        if not profiles and not local_repository:
            return params

        initialization_options = dict(params.get("initializationOptions", {}))
        settings_payload = initialization_options.get("settings", {})
        if not isinstance(settings_payload, dict):
            settings_payload = {}
        java_settings = settings_payload.get("java", {})
        if not isinstance(java_settings, dict):
            java_settings = {}
        configuration_settings = java_settings.get("configuration", {})
        if not isinstance(configuration_settings, dict):
            configuration_settings = {}
        maven_settings = configuration_settings.get("maven", {})
        if not isinstance(maven_settings, dict):
            maven_settings = {}

        maven_settings["userSettings"] = str(
            self._write_maven_user_settings(
                workspace_root,
                profiles,
                local_repository=local_repository,
            )
        )
        configuration_settings["maven"] = maven_settings
        java_settings["configuration"] = configuration_settings
        settings_payload["java"] = java_settings
        initialization_options["settings"] = settings_payload
        params["initializationOptions"] = initialization_options
        return params

    def detect_preflight_issue(self, *, file_path: Path, workspace_root: Path) -> LspPreflightIssue | None:
        profiles = self.get_language_settings().maven_profiles
        if profiles:
            return None

        module_root = self._find_nearest_maven_module_root(file_path.resolve(), workspace_root.resolve())
        if module_root is None:
            return None
        pom_info = _parse_maven_pom_info(module_root / "pom.xml")
        if pom_info is None:
            return None
        relative_path = file_path.resolve().relative_to(module_root).as_posix()
        conflicting_profiles = [
            profile["id"]
            for profile in pom_info["profiles"]
            if profile["active_by_default"] and any(fnmatch(relative_path, pattern) for pattern in profile["excludes"])
        ]
        if not conflicting_profiles:
            return None

        suggested_profile = _extract_channel_profile(file_path.resolve())
        suggestion_text = (
            f"；建议在 project_runtime.json 中配置 lsp.languages.java.maven_profiles=[\"{suggested_profile}\"]"
            if suggested_profile
            else ""
        )
        return LspPreflightIssue(
            message=(
                "Java 工程导入存在 Maven profile 冲突："
                f"当前文件 {relative_path} 会被默认激活的 profile {', '.join(conflicting_profiles)} 排除"
                f"{suggestion_text}"
            ),
            issue_code="maven_profile_conflict",
            project_state="profile_conflict",
            details={
                "maven_module_path": module_root.relative_to(workspace_root.resolve()).as_posix() or ".",
                "conflicting_profiles": ",".join(conflicting_profiles),
                "suggested_profile": suggested_profile or "",
            },
        )

    def _find_nearest_marker_root(self, file_path: Path, boundary: Path) -> Path | None:
        markers = self.get_language_settings().workspace_markers
        current = file_path.resolve().parent
        while True:
            if any((current / marker).exists() for marker in markers):
                return current
            if current == boundary or current.parent == current:
                return boundary if any((boundary / marker).exists() for marker in markers) else None
            current = current.parent

    def _find_nearest_maven_module_root(self, file_path: Path, boundary: Path) -> Path | None:
        current = file_path.resolve().parent
        while True:
            if (current / "pom.xml").exists():
                return current
            if current == boundary or current.parent == current:
                return boundary if (boundary / "pom.xml").exists() else None
            current = current.parent

    def _find_topmost_maven_aggregator_root(self, module_root: Path, boundary: Path) -> Path | None:
        current = module_root.resolve()
        selected = None
        while True:
            pom_path = current / "pom.xml"
            if pom_path.exists():
                pom_info = _parse_maven_pom_info(pom_path)
                if pom_info is not None and pom_info["is_aggregator"]:
                    relative_parts = module_root.relative_to(current).parts if current != module_root else ()
                    if _aggregator_contains_module_path(pom_info["modules"], relative_parts):
                        selected = current
            if current == boundary or current.parent == current:
                return selected
            current = current.parent

    def build_command(self, workspace_root: Path) -> list[str]:
        command = list(self.get_language_settings().command)
        if not command:
            raise ValueError("Java LSP command 不能为空。")
        self._validate_launch_command(command)
        data_dir = self.build_data_dir(workspace_root)
        if "-configuration" not in command:
            # Homebrew 版 jdtls 默认会落到 ~/.eclipse/.../configuration，当前运行环境下未必可写。
            # 显式改到 runtime_home 下，避免因为用户目录权限或沙箱限制导致通道启动即关闭。
            configuration_dir = data_dir.parent / f"{data_dir.name}-configuration"
            configuration_dir.mkdir(parents=True, exist_ok=True)
            command.extend(["-configuration", str(configuration_dir)])
        if "-data" not in command:
            data_dir.mkdir(parents=True, exist_ok=True)
            command.extend(["-data", str(data_dir)])
        return command

    def _validate_launch_command(self, command: list[str]) -> None:
        launch_env, executable_tokens = self._extract_launch_context(command)
        if not executable_tokens:
            raise ValueError("Java LSP command 缺少可执行目标。")

        executable = executable_tokens[0]
        if self._resolve_executable(executable, launch_env) is None:
            raise ValueError(
                f"Java LSP 命令不可执行：{executable}。"
                "请确认 jdtls 已安装，或在 project_runtime.json 中配置可执行的绝对路径。"
            )

        java_command = self._resolve_java_command(launch_env)
        if java_command is None:
            raise ValueError(
                "Java LSP 未找到可用的 Java 运行时。"
                "请在 lsp.languages.java.command 中显式绑定 JDK 21。"
            )

        major_version = self._detect_java_major_version(java_command, launch_env)
        if major_version < 21:
            raise ValueError(
                f"Java LSP 需要 JDK 21+，当前检测到 JDK {major_version}。"
                "请在 lsp.languages.java.command 中显式绑定 JDK 21。"
            )

    def _extract_launch_context(self, command: list[str]) -> tuple[dict[str, str], list[str]]:
        launch_env = dict(os.environ)
        if not command:
            return launch_env, []
        if Path(command[0]).name != "env":
            return launch_env, list(command)

        index = 1
        while index < len(command):
            token = command[index]
            if "=" not in token or token.startswith("-"):
                break
            key, value = token.split("=", 1)
            if key:
                launch_env[key] = value
            index += 1
        return launch_env, command[index:]

    def _resolve_executable(self, executable: str, launch_env: dict[str, str]) -> str | None:
        candidate = Path(executable).expanduser()
        if candidate.is_absolute() or "/" in executable:
            resolved = candidate.resolve(strict=False)
            if resolved.exists() and os.access(resolved, os.X_OK):
                return str(resolved)
            return None
        return shutil.which(executable, path=launch_env.get("PATH"))

    def _resolve_java_command(self, launch_env: dict[str, str]) -> str | None:
        java_home = launch_env.get("JAVA_HOME", "").strip()
        if java_home:
            java_candidate = Path(java_home).expanduser() / "bin" / "java"
            if java_candidate.exists() and os.access(java_candidate, os.X_OK):
                return str(java_candidate)
            raise ValueError(
                f"Java LSP 配置的 JAVA_HOME 不可用：{java_home}。"
                "请检查 JDK 21 路径是否正确。"
            )
        return shutil.which("java", path=launch_env.get("PATH"))

    def _detect_java_major_version(self, java_command: str, launch_env: dict[str, str]) -> int:
        try:
            completed = subprocess.run(
                [java_command, "-version"],
                check=True,
                capture_output=True,
                text=True,
                env=launch_env,
                timeout=5,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("检测 Java 版本超时，请检查 JDK 21 是否可正常执行。") from exc
        except OSError as exc:
            raise ValueError(f"检测 Java 版本失败：{exc}") from exc
        except subprocess.CalledProcessError as exc:
            output = (exc.stderr or exc.stdout or "").strip()
            detail = f"：{output}" if output else ""
            raise ValueError(f"检测 Java 版本失败{detail}") from exc

        version_output = f"{completed.stderr}\n{completed.stdout}".strip()
        match = _JAVA_VERSION_PATTERN.search(version_output)
        if match is None:
            raise ValueError(f"无法识别 Java 版本输出：{version_output[:120]}")

        raw_version = match.group("version")
        if raw_version.startswith("1."):
            major_token = raw_version.split(".")[1]
        else:
            major_token = raw_version.split(".", 1)[0]
        try:
            return int(major_token)
        except ValueError as exc:
            raise ValueError(f"无法解析 Java 主版本：{raw_version}") from exc

    def _write_maven_user_settings(
        self,
        workspace_root: Path,
        profiles: tuple[str, ...],
        *,
        local_repository: str = "",
    ) -> Path:
        settings_path = self.build_data_dir(workspace_root) / "maven-user-settings.xml"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        active_profiles = "\n".join(
            f"    <activeProfile>{escape(profile)}</activeProfile>" for profile in profiles
        )
        local_repository_block = (
            f"  <localRepository>{escape(local_repository)}</localRepository>\n"
            if local_repository
            else ""
        )
        settings_path.write_text(
            (
                "<settings xmlns=\"http://maven.apache.org/SETTINGS/1.0.0\"\n"
                "          xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"\n"
                "          xsi:schemaLocation=\"http://maven.apache.org/SETTINGS/1.0.0 "
                "https://maven.apache.org/xsd/settings-1.0.0.xsd\">\n"
                f"{local_repository_block}"
                "  <activeProfiles>\n"
                f"{active_profiles}\n"
                "  </activeProfiles>\n"
                "</settings>\n"
            ),
            encoding="utf-8",
        )
        return settings_path


def build_default_java_adapter() -> JdtlsServerAdapter:
    return JdtlsServerAdapter()


def _parse_maven_pom_info(pom_path: Path) -> dict[str, object] | None:
    try:
        root = ET.fromstring(pom_path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError):
        return None

    namespace_prefix = ""
    if root.tag.startswith("{"):
        namespace_prefix = root.tag.split("}", 1)[0] + "}"

    packaging_text = root.findtext(f"{namespace_prefix}packaging", default="jar").strip().lower()
    modules_parent = root.find(f"{namespace_prefix}modules")
    modules: list[str] = []
    if modules_parent is not None:
        for module in modules_parent.findall(f"{namespace_prefix}module"):
            if module.text and module.text.strip():
                modules.append(module.text.strip().strip("/"))
    profiles = _parse_maven_profiles(root, namespace_prefix)
    return {
        "packaging": packaging_text,
        "modules": tuple(modules),
        "is_aggregator": packaging_text == "pom" and bool(modules),
        "profiles": tuple(profiles),
    }


def _parse_maven_profiles(root: ET.Element, namespace_prefix: str) -> list[dict[str, object]]:
    profiles: list[dict[str, object]] = []
    profiles_parent = root.find(f"{namespace_prefix}profiles")
    if profiles_parent is None:
        return profiles
    for profile in profiles_parent.findall(f"{namespace_prefix}profile"):
        profile_id = profile.findtext(f"{namespace_prefix}id", default="").strip()
        if not profile_id:
            continue
        active_by_default = (
            profile.findtext(f"{namespace_prefix}activation/{namespace_prefix}activeByDefault", default="")
            .strip()
            .lower()
            == "true"
        )
        excludes: list[str] = []
        for plugin in profile.findall(f"{namespace_prefix}build/{namespace_prefix}plugins/{namespace_prefix}plugin"):
            artifact_id = plugin.findtext(f"{namespace_prefix}artifactId", default="").strip()
            if artifact_id != "maven-compiler-plugin":
                continue
            excludes_parent = plugin.find(f"{namespace_prefix}configuration/{namespace_prefix}excludes")
            if excludes_parent is None:
                continue
            for exclude in excludes_parent.findall(f"{namespace_prefix}exclude"):
                if exclude.text and exclude.text.strip():
                    excludes.append(exclude.text.strip())
        profiles.append(
            {
                "id": profile_id,
                "active_by_default": active_by_default,
                "excludes": tuple(excludes),
            }
        )
    return profiles


def _aggregator_contains_module_path(modules: tuple[str, ...], relative_parts: tuple[str, ...]) -> bool:
    if not modules:
        return False
    if not relative_parts:
        return True
    relative_path = "/".join(relative_parts)
    for module in modules:
        normalized = module.replace("\\", "/").strip("/")
        if not normalized:
            continue
        if relative_path == normalized or relative_path.startswith(f"{normalized}/"):
            return True
    return False


def _extract_channel_profile(file_path: Path) -> str | None:
    parts = file_path.parts
    try:
        channel_index = parts.index("channel")
    except ValueError:
        return None
    if channel_index + 1 >= len(parts):
        return None
    return parts[channel_index + 1].strip() or None
