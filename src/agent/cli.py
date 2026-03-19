from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .config.logging_setup import init_logging
from .core.message import get_message_text
from .runtime.session import clear_session_memory, run_session
from .runtime.workspace import configure_workspace, get_workspace


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent", description="在当前目录启动编码代理。")
    parser.add_argument("--workdir", default=".", help="工作区目录，默认使用当前目录。")
    parser.add_argument("--session", default="default", help="会话 ID，默认 default。")
    parser.add_argument("--mode", choices=("build", "plan"), default="build", help="启动模式，默认 build。")

    subparsers = parser.add_subparsers(dest="command")
    web_parser = subparsers.add_parser("web", help="启动绑定当前工作区的 Web 服务。")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    web_parser.add_argument("--port", type=int, default=8000, help="监听端口。")
    return parser


def _print_workspace_banner(session_id: str, mode: str) -> None:
    workspace = get_workspace()
    print(f"工作区: {workspace.root}")
    print(f"会话: {session_id}")
    print(f"模式: {mode}")
    print(f"AGENTS.md: {'已检测到' if workspace.has_agents_md else '未找到'}")


def run_cli_session(*, session_id: str, mode: str) -> None:
    init_logging(get_workspace().logs_dir)
    _print_workspace_banner(session_id, mode)
    print("已进入持续对话模式，输入 exit/quit/退出 可结束，输入 /clear 可清空历史。")
    while True:
        try:
            user_input = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n会话已结束。")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"} or user_input == "退出":
            print("会话已结束。")
            break
        if user_input.lower() == "/clear":
            clear_session_memory(session_id)
            print("历史上下文已清空。")
            continue

        result = run_session(user_input=user_input, session_id=session_id, mode=mode)
        print(f"\n助手：{get_message_text(result)}")


def run_web_server(*, host: str, port: int) -> None:
    init_logging(get_workspace().logs_dir)
    workspace = get_workspace()
    print(f"工作区: {workspace.root}")
    print(f"Web 服务: http://{host}:{port}")
    uvicorn.run("agent.web.app:app", host=host, port=port, reload=False)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_workspace(Path(args.workdir), launch_mode="web" if args.command == "web" else "cli")
    if args.command == "web":
        run_web_server(host=args.host, port=args.port)
        return
    run_cli_session(session_id=args.session, mode=args.mode)
