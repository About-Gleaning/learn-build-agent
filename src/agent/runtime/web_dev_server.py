from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .workspace import get_workspace


FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
LOOPBACK_HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 15.0
PORT_CHECK_INTERVAL_SECONDS = 0.2
SHUTDOWN_TIMEOUT_SECONDS = 5.0
PORT_DISCOVERY_MAX_ATTEMPTS = 50
STATE_FILENAME = "state.json"
BACKEND_LOG_FILENAME = "backend.log"
FRONTEND_LOG_FILENAME = "frontend.log"
LOG_TAIL_LINE_LIMIT = 20
LOG_TAIL_BYTE_LIMIT = 4000


class WebStackError(RuntimeError):
    """Web 开发栈启动或运行失败。"""


@dataclass(frozen=True)
class ServiceEndpoint:
    name: str
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class WebStackState:
    workspace_root: str
    host: str
    port: int
    backend_pid: int
    frontend_pid: int
    backend_url: str
    frontend_url: str
    backend_log_path: str
    frontend_log_path: str
    started_at: float
    status: str
    frontend_bind_host: str = FRONTEND_HOST
    frontend_port: int = FRONTEND_PORT
    frontend_local_url: str = ""
    frontend_network_url: str = ""
    share_frontend: bool = False


@dataclass(frozen=True)
class WebStackInspection:
    state_path: Path
    state: WebStackState
    status: str
    backend_alive: bool
    frontend_alive: bool
    backend_ready: bool
    frontend_ready: bool


@dataclass(frozen=True)
class WebStackPruneResult:
    inspection: WebStackInspection
    action: str
    error: str = ""


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_frontend_dir() -> Path:
    frontend_dir = resolve_project_root() / "frontend"
    if not frontend_dir.is_dir():
        raise WebStackError(
            f"未找到前端目录：{frontend_dir}。当前版本的 `my-agent web` 需要仓库内置 `frontend/` 才能一键启动。"
        )
    if not (frontend_dir / "package.json").is_file():
        raise WebStackError(f"前端目录缺少 `package.json`：{frontend_dir}")
    return frontend_dir


def ensure_frontend_dev_prerequisites(frontend_dir: Path) -> str:
    pnpm_binary = shutil.which("pnpm")
    if not pnpm_binary:
        raise WebStackError("未检测到 `pnpm`。请先安装 `pnpm`，再重新执行 `my-agent web`。")
    if not (frontend_dir / "node_modules").is_dir():
        raise WebStackError(
            f"前端依赖未安装：{frontend_dir / 'node_modules'}。请先执行 `cd {frontend_dir} && pnpm install`。"
        )
    return pnpm_binary


def create_service_endpoint(name: str, host: str, port: int) -> ServiceEndpoint:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return ServiceEndpoint(name=name, host=connect_host, port=port)


def find_available_port(host: str, preferred_port: int, *, attempts: int = PORT_DISCOVERY_MAX_ATTEMPTS) -> int:
    normalized_port = int(preferred_port)
    if normalized_port <= 0:
        raise WebStackError(f"端口必须是正整数：{preferred_port}")

    bind_host = "0.0.0.0" if host in {"", "::"} else host
    for offset in range(attempts):
        candidate = normalized_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, candidate))
            except OSError:
                continue
        return candidate

    raise WebStackError(
        f"未能从端口 {normalized_port} 开始找到可用端口，已尝试 {attempts} 个候选端口。"
    )


def get_web_dev_runtime_dir() -> Path:
    workspace = get_workspace()
    return (workspace.web_dev_root / workspace.workspace_id).resolve()


def get_web_dev_state_path() -> Path:
    return (get_web_dev_runtime_dir() / STATE_FILENAME).resolve()


def iter_web_dev_state_paths() -> list[Path]:
    web_dev_root = get_workspace().web_dev_root
    if not web_dev_root.is_dir():
        return []
    return sorted(path.resolve() for path in web_dev_root.glob(f"*/{STATE_FILENAME}") if path.is_file())


def _build_runtime_file_path(file_name: str) -> Path:
    return (get_web_dev_runtime_dir() / file_name).resolve()


def _emit_console(message: str, *, verbose: bool) -> None:
    if verbose:
        print(message)


def _prepare_runtime_dir() -> Path:
    runtime_dir = get_web_dev_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def _reset_log_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _spawn_logged_process(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    with log_path.open("ab") as log_file:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.Popen(
            command,
            cwd=str(cwd),
            env=process_env,
            # Web 开发服务需要彻底脱离当前终端，避免继承 stdin 后让 shell 输入变卡。
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def start_backend_dev_server(*, workspace_root: Path, host: str, port: int, log_path: Path) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "agent.web.app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    return _spawn_logged_process(command, cwd=workspace_root, log_path=log_path)


def start_frontend_dev_server(
    *,
    frontend_dir: Path,
    pnpm_binary: str,
    host: str,
    port: int,
    backend_url: str,
    workspace_root: Path,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    command = [
        pnpm_binary,
        "dev",
        "--host",
        host,
        "--port",
        str(port),
    ]
    return _spawn_logged_process(
        command,
        cwd=frontend_dir,
        log_path=log_path,
        env={
            "MY_AGENT_VITE_BACKEND_URL": backend_url,
            "VITE_EXPECTED_WORKSPACE_ROOT": str(workspace_root.resolve()),
        },
    )


def resolve_network_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            host = sock.getsockname()[0]
    except OSError:
        return LOOPBACK_HOST
    return host or LOOPBACK_HOST


def build_frontend_urls(*, frontend_bind_host: str, frontend_port: int, share_frontend: bool) -> tuple[str, str]:
    local_url = f"http://{LOOPBACK_HOST}:{frontend_port}"
    if not share_frontend:
        return local_url, ""
    network_host = resolve_network_host()
    if network_host == LOOPBACK_HOST:
        return local_url, ""
    return local_url, f"http://{network_host}:{frontend_port}"


def is_tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def wait_for_process_port(
    process: subprocess.Popen[bytes],
    endpoint: ServiceEndpoint,
    *,
    timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise WebStackError(f"{endpoint.name}启动失败，进程已退出，退出码={return_code}。")
        if is_tcp_port_open(endpoint.host, endpoint.port):
            return
        time.sleep(PORT_CHECK_INTERVAL_SECONDS)
    raise WebStackError(f"{endpoint.name}在 {timeout_seconds:.0f} 秒内未监听 {endpoint.url}。")


def stop_process(process: subprocess.Popen[bytes] | None, *, name: str) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # 避免子进程僵死导致端口残留，超时后强制 kill。
        process.kill()
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)


def _read_log_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    content = path.read_bytes()[-LOG_TAIL_BYTE_LIMIT :].decode("utf-8", errors="replace")
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-LOG_TAIL_LINE_LIMIT:])


def _format_log_excerpt(log_path: Path, label: str) -> str:
    tail = _read_log_tail(log_path)
    if not tail:
        return ""
    return f"{label}日志摘要（{log_path}）:\n{tail}"


def _build_start_failure_message(error_message: str, *, backend_log_path: Path, frontend_log_path: Path) -> str:
    parts = [error_message]
    backend_excerpt = _format_log_excerpt(backend_log_path, "后端")
    frontend_excerpt = _format_log_excerpt(frontend_log_path, "前端")
    if backend_excerpt:
        parts.append(backend_excerpt)
    if frontend_excerpt:
        parts.append(frontend_excerpt)
    return "\n\n".join(parts)


def _write_state(state: WebStackState) -> None:
    state_path = get_web_dev_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _remove_state_file(state_path: Path | None = None) -> None:
    state_path = get_web_dev_state_path() if state_path is None else state_path.resolve()
    if state_path.exists():
        state_path.unlink()


def _load_state_from_path(state_path: Path) -> WebStackState | None:
    state_path = state_path.resolve()
    if not state_path.is_file():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if "frontend_port" not in payload:
        payload["frontend_port"] = FRONTEND_PORT
    if "frontend_bind_host" not in payload:
        payload["frontend_bind_host"] = FRONTEND_HOST
    if "frontend_local_url" not in payload:
        payload["frontend_local_url"] = payload.get("frontend_url", "")
    if "frontend_network_url" not in payload:
        payload["frontend_network_url"] = ""
    if "share_frontend" not in payload:
        payload["share_frontend"] = False
    return WebStackState(**payload)


def _load_state() -> WebStackState | None:
    return _load_state_from_path(get_web_dev_state_path())


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_pid_group(pid: int, sig: int) -> None:
    os.killpg(pid, sig)


def _stop_pid(pid: int) -> None:
    if not _is_process_alive(pid):
        return
    try:
        _signal_pid_group(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _is_process_alive(pid):
            return
        time.sleep(PORT_CHECK_INTERVAL_SECONDS)

    try:
        _signal_pid_group(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        os.kill(pid, signal.SIGKILL)


def inspect_web_stack_state(state: WebStackState, *, state_path: Path | None = None) -> WebStackInspection:
    backend_endpoint = create_service_endpoint("后端服务", state.host, state.port)
    frontend_endpoint = create_service_endpoint("前端服务", state.frontend_bind_host, state.frontend_port)
    backend_alive = _is_process_alive(state.backend_pid)
    frontend_alive = _is_process_alive(state.frontend_pid)
    backend_ready = is_tcp_port_open(backend_endpoint.host, state.port)
    frontend_ready = is_tcp_port_open(frontend_endpoint.host, state.frontend_port)
    if backend_alive and frontend_alive and backend_ready and frontend_ready:
        status = "running"
    elif backend_alive or frontend_alive or backend_ready or frontend_ready:
        status = "degraded"
    else:
        status = "stale"
    resolved_state_path = get_web_dev_state_path() if state_path is None else state_path.resolve()
    return WebStackInspection(
        state_path=resolved_state_path,
        state=state,
        status=status,
        backend_alive=backend_alive,
        frontend_alive=frontend_alive,
        backend_ready=backend_ready,
        frontend_ready=frontend_ready,
    )


def inspect_all_web_dev_stacks() -> list[WebStackInspection]:
    inspections: list[WebStackInspection] = []
    for state_path in iter_web_dev_state_paths():
        state = _load_state_from_path(state_path)
        if state is None:
            continue
        inspections.append(inspect_web_stack_state(state, state_path=state_path))
    return inspections


def _format_stack_health(inspection: WebStackInspection) -> str:
    flags = [
        f"backend_pid={'up' if inspection.backend_alive else 'down'}",
        f"backend_port={'ready' if inspection.backend_ready else 'closed'}",
        f"frontend_pid={'up' if inspection.frontend_alive else 'down'}",
        f"frontend_port={'ready' if inspection.frontend_ready else 'closed'}",
    ]
    return ", ".join(flags)


def format_web_stack_prune_report(results: list[WebStackPruneResult]) -> str:
    if not results:
        return "未发现任何 Web 开发栈状态文件。"

    action_label = {
        "removed": "已清理",
        "kept": "保留",
        "failed": "清理失败",
    }
    lines: list[str] = []
    removed_count = sum(1 for item in results if item.action == "removed")
    kept_count = sum(1 for item in results if item.action == "kept")
    failed_count = sum(1 for item in results if item.action == "failed")
    lines.append(
        f"扫描完成：共 {len(results)} 个实例，已清理 {removed_count} 个，保留 {kept_count} 个，失败 {failed_count} 个。"
    )
    for item in results:
        inspection = item.inspection
        lines.extend(
            [
                f"- {action_label.get(item.action, item.action)} | {inspection.status} | 工作区: {inspection.state.workspace_root}",
                f"  状态文件: {inspection.state_path}",
                f"  后端: {inspection.state.backend_url} (PID {inspection.state.backend_pid})",
                f"  前端: {(inspection.state.frontend_local_url or inspection.state.frontend_url)} (PID {inspection.state.frontend_pid})",
                f"  健康检查: {_format_stack_health(inspection)}",
            ]
        )
        if item.error:
            lines.append(f"  错误: {item.error}")
    return "\n".join(lines)


def prune_web_dev_stacks() -> list[WebStackPruneResult]:
    results: list[WebStackPruneResult] = []
    for inspection in inspect_all_web_dev_stacks():
        if inspection.status == "running":
            results.append(WebStackPruneResult(inspection=inspection, action="kept"))
            continue
        try:
            _stop_pid(inspection.state.frontend_pid)
            _stop_pid(inspection.state.backend_pid)
            _remove_state_file(inspection.state_path)
        except OSError as exc:
            results.append(WebStackPruneResult(inspection=inspection, action="failed", error=str(exc)))
            continue
        results.append(WebStackPruneResult(inspection=inspection, action="removed"))
    return results


def get_web_stack_status() -> tuple[str, WebStackState | None]:
    state = _load_state()
    if state is None:
        return "stopped", None
    inspection = inspect_web_stack_state(state)
    return inspection.status, state


def _ensure_not_running() -> None:
    status, state = get_web_stack_status()
    if status in {"running", "degraded"} and state is not None:
        raise WebStackError(
            "当前工作区已有 Web 开发栈实例正在运行或处于异常残留状态。"
            "请先执行 `my-agent web status` 查看详情；若怀疑存在跨工作区残留，执行 `my-agent web prune` 统一清理异常实例。"
        )
    if status in {"stopped", "stale"}:
        _remove_state_file()


def start_web_dev_stack(
    *,
    workspace_root: Path,
    host: str,
    port: int,
    share_frontend: bool = False,
    verbose: bool = False,
) -> WebStackState:
    frontend_dir = resolve_frontend_dir()
    pnpm_binary = ensure_frontend_dev_prerequisites(frontend_dir)
    _ensure_not_running()
    _prepare_runtime_dir()

    backend_log_path = _build_runtime_file_path(BACKEND_LOG_FILENAME)
    frontend_log_path = _build_runtime_file_path(FRONTEND_LOG_FILENAME)
    _reset_log_file(backend_log_path)
    _reset_log_file(frontend_log_path)

    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    backend_bind_host = LOOPBACK_HOST if share_frontend else host
    frontend_bind_host = host if share_frontend else FRONTEND_HOST
    backend_port = find_available_port(backend_bind_host, port)
    frontend_port = find_available_port(frontend_bind_host, FRONTEND_PORT)
    backend_endpoint = create_service_endpoint("后端服务", backend_bind_host, backend_port)
    frontend_endpoint = create_service_endpoint("前端服务", frontend_bind_host, frontend_port)
    frontend_local_url, frontend_network_url = build_frontend_urls(
        frontend_bind_host=frontend_bind_host,
        frontend_port=frontend_port,
        share_frontend=share_frontend,
    )

    try:
        _emit_console(f"工作区: {workspace_root}", verbose=verbose)
        _emit_console(f"后端服务启动中: {backend_endpoint.url}", verbose=verbose)
        backend_process = start_backend_dev_server(
            workspace_root=workspace_root,
            host=backend_bind_host,
            port=backend_port,
            log_path=backend_log_path,
        )
        wait_for_process_port(backend_process, backend_endpoint)
        _emit_console(f"后端服务已就绪: {backend_endpoint.url}", verbose=verbose)

        _emit_console(f"前端服务启动中: {frontend_endpoint.url}", verbose=verbose)
        frontend_process = start_frontend_dev_server(
            frontend_dir=frontend_dir,
            pnpm_binary=pnpm_binary,
            host=frontend_bind_host,
            port=frontend_port,
            backend_url=backend_endpoint.url,
            workspace_root=workspace_root,
            log_path=frontend_log_path,
        )
        wait_for_process_port(frontend_process, frontend_endpoint)
        _emit_console(f"前端服务已就绪: {frontend_endpoint.url}", verbose=verbose)

        state = WebStackState(
            workspace_root=str(workspace_root),
            host=backend_bind_host,
            port=backend_port,
            backend_pid=backend_process.pid,
            frontend_pid=frontend_process.pid,
            backend_url=backend_endpoint.url,
            frontend_url=frontend_local_url,
            backend_log_path=str(backend_log_path),
            frontend_log_path=str(frontend_log_path),
            started_at=time.time(),
            status="running",
            frontend_bind_host=frontend_bind_host,
            frontend_port=frontend_port,
            frontend_local_url=frontend_local_url,
            frontend_network_url=frontend_network_url,
            share_frontend=share_frontend,
        )
        _write_state(state)
        return state
    except Exception as exc:
        stop_process(frontend_process, name="前端服务")
        stop_process(backend_process, name="后端服务")
        _remove_state_file()
        error_message = str(exc) if isinstance(exc, WebStackError) else f"Web 开发栈启动失败：{exc}"
        raise WebStackError(
            _build_start_failure_message(
                error_message,
                backend_log_path=backend_log_path,
                frontend_log_path=frontend_log_path,
            )
        ) from exc


def format_web_stack_status(status: str, state: WebStackState | None) -> str:
    if state is None:
        return "当前工作区 Web 开发栈未运行。"

    status_label = {
        "running": "运行中",
        "degraded": "异常残留",
        "stale": "失效残留",
        "stopped": "已停止",
    }.get(status, status)
    lines = [
        f"状态: {status_label}",
        f"工作区: {state.workspace_root}",
        f"后端监听地址: {state.host}:{state.port}",
        f"后端访问地址: {state.backend_url}",
        f"前端监听地址: {state.frontend_bind_host}:{state.frontend_port}",
        f"前端本机访问地址: {state.frontend_local_url or state.frontend_url}",
    ]
    if state.frontend_network_url:
        lines.append(f"前端局域网访问地址: {state.frontend_network_url}")
    if state.share_frontend:
        lines.append("开放模式: 仅前端页面对局域网开放，后端继续仅本机可见")
    lines.extend(
        [
            f"后端 PID: {state.backend_pid}",
            f"前端 PID: {state.frontend_pid}",
            f"状态文件: {get_web_dev_state_path()}",
            f"后端日志: {state.backend_log_path}",
            f"前端日志: {state.frontend_log_path}",
        ]
    )
    return "\n".join(lines)


def stop_web_dev_stack() -> tuple[str, WebStackState | None]:
    status, state = get_web_stack_status()
    if state is None:
        return "stopped", None

    _stop_pid(state.frontend_pid)
    _stop_pid(state.backend_pid)
    _remove_state_file()
    return "stopped", state
