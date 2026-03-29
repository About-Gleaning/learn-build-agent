from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
STARTUP_TIMEOUT_SECONDS = 15.0
PORT_CHECK_INTERVAL_SECONDS = 0.2
SHUTDOWN_TIMEOUT_SECONDS = 5.0


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


def _build_subprocess_stdio(*, verbose: bool) -> dict[str, object]:
    if verbose:
        return {}
    return {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def _emit_console(message: str, *, verbose: bool) -> None:
    if verbose:
        print(message)


def start_backend_dev_server(*, workspace_root: Path, host: str, port: int, verbose: bool) -> subprocess.Popen[bytes]:
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
    return subprocess.Popen(command, cwd=str(workspace_root), **_build_subprocess_stdio(verbose=verbose))


def start_frontend_dev_server(*, frontend_dir: Path, pnpm_binary: str, verbose: bool) -> subprocess.Popen[bytes]:
    command = [
        pnpm_binary,
        "dev",
        "--host",
        FRONTEND_HOST,
        "--port",
        str(FRONTEND_PORT),
    ]
    return subprocess.Popen(command, cwd=str(frontend_dir), **_build_subprocess_stdio(verbose=verbose))


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


def wait_for_web_stack_forever(
    backend_process: subprocess.Popen[bytes],
    frontend_process: subprocess.Popen[bytes],
) -> None:
    while True:
        backend_return_code = backend_process.poll()
        if backend_return_code is not None:
            raise WebStackError(f"后端进程已退出，退出码={backend_return_code}。")
        frontend_return_code = frontend_process.poll()
        if frontend_return_code is not None:
            raise WebStackError(f"前端进程已退出，退出码={frontend_return_code}。")
        time.sleep(PORT_CHECK_INTERVAL_SECONDS)


def run_web_dev_stack(*, workspace_root: Path, host: str, port: int, verbose: bool = False) -> None:
    frontend_dir = resolve_frontend_dir()
    pnpm_binary = ensure_frontend_dev_prerequisites(frontend_dir)
    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    backend_endpoint = create_service_endpoint("后端服务", host, port)
    frontend_endpoint = create_service_endpoint("前端服务", FRONTEND_HOST, FRONTEND_PORT)

    try:
        _emit_console(f"工作区: {workspace_root}", verbose=verbose)
        _emit_console(f"后端服务启动中: {backend_endpoint.url}", verbose=verbose)
        backend_process = start_backend_dev_server(workspace_root=workspace_root, host=host, port=port, verbose=verbose)
        wait_for_process_port(backend_process, backend_endpoint)
        _emit_console(f"后端服务已就绪: {backend_endpoint.url}", verbose=verbose)

        _emit_console(f"前端服务启动中: {frontend_endpoint.url}", verbose=verbose)
        frontend_process = start_frontend_dev_server(frontend_dir=frontend_dir, pnpm_binary=pnpm_binary, verbose=verbose)
        wait_for_process_port(frontend_process, frontend_endpoint)
        _emit_console(f"前端服务已就绪: {frontend_endpoint.url}", verbose=verbose)
        _emit_console("按 Ctrl+C 可同时停止前后端服务。", verbose=verbose)

        wait_for_web_stack_forever(backend_process, frontend_process)
    except KeyboardInterrupt:
        _emit_console("\n正在停止前后端服务...", verbose=verbose)
    finally:
        stop_process(frontend_process, name="前端服务")
        stop_process(backend_process, name="后端服务")
