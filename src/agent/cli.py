from __future__ import annotations

import argparse
from pathlib import Path

from .config.logging_setup import init_logging
from .core.message import get_message_text
from .runtime.session import clear_session_memory, generate_session_id, run_session
from .runtime.web_dev_server import (
    WebStackError,
    format_web_stack_prune_report,
    format_web_stack_status,
    get_web_stack_status,
    prune_web_dev_stacks,
    start_web_dev_stack,
    stop_web_dev_stack,
)
from .runtime.workspace import configure_workspace, get_workspace


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-agent", description="在当前目录启动编码代理。")
    parser.add_argument("--workdir", default=".", help="工作区目录，默认使用当前目录。")
    parser.add_argument("--session", help="会话 ID；未传时自动生成随机会话号。")
    parser.add_argument("--mode", choices=("build", "plan"), default="build", help="启动模式，默认 build。")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("help", help="查看常用命令与示例。")
    web_parser = subparsers.add_parser("web", help="启动绑定当前工作区的 Web 服务。")
    web_parser.add_argument(
        "web_action",
        nargs="?",
        choices=("start", "status", "stop", "prune"),
        default="start",
        help="Web 管理动作，默认 start。",
    )
    web_parser.add_argument("--host", default="0.0.0.0", help="监听地址。")
    web_parser.add_argument("--port", type=int, default=8000, help="监听端口。")
    web_parser.add_argument(
        "--share-frontend",
        action="store_true",
        help="仅将前端页面开放给局域网访问；后端继续只监听本机，并由前端开发代理转发。",
    )
    web_parser.add_argument("--verbose", action="store_true", help="输出 Web 启动过程与状态提示，不输出业务日志。")
    return parser


def _format_help_text() -> str:
    return "\n".join(
        [
            "my-agent 命令总览",
            "",
            "基础用法：",
            "  my-agent",
            "    在当前目录进入持续对话式 CLI。",
            "  my-agent help",
            "    查看这份命令总览。",
            "",
            "常用参数：",
            "  --workdir PATH",
            "    指定工作区目录，默认使用当前目录。",
            "  --session SESSION_ID",
            "    指定会话 ID；未传时自动生成。",
            "  --mode {build,plan}",
            "    指定启动模式，默认 build。",
            "",
            "Web 命令：",
            "  my-agent web",
            "    启动当前工作区的 Web 开发栈。",
            "  my-agent web status",
            "    查看当前工作区 Web 实例状态。",
            "  my-agent web stop",
            "    停止当前工作区 Web 实例。",
            "  my-agent web prune",
            "    清理 ~/.my-agent/workspaces/web-dev/ 下 degraded/stale 的异常残留实例。",
            "",
            "典型示例：",
            "  my-agent --workdir /path/to/project",
            "  my-agent --session demo_001",
            "  my-agent --mode plan",
            "  my-agent web --host 127.0.0.1 --port 8000",
            "",
            "说明：",
            "  不带子命令时，my-agent 会直接进入持续对话模式。",
        ]
    )


def run_help() -> None:
    print(_format_help_text())


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


def run_web_start(*, host: str, port: int, share_frontend: bool = False, verbose: bool = False) -> None:
    init_logging(get_workspace().logs_dir, console_enabled=False)
    try:
        state = start_web_dev_stack(
            workspace_root=get_workspace().root,
            host=host,
            port=port,
            share_frontend=share_frontend,
            verbose=verbose,
        )
    except WebStackError as exc:
        raise SystemExit(str(exc)) from exc
    print("Web 开发栈启动成功。")
    print(format_web_stack_status("running", state))


def run_web_status() -> None:
    init_logging(get_workspace().logs_dir, console_enabled=False)
    status, state = get_web_stack_status()
    print(format_web_stack_status(status, state))


def run_web_stop() -> None:
    init_logging(get_workspace().logs_dir, console_enabled=False)
    status, state = stop_web_dev_stack()
    if state is None:
        print("当前工作区 Web 开发栈未运行。")
        return
    print("Web 开发栈已停止。")
    print(format_web_stack_status(status, state))


def run_web_prune() -> None:
    init_logging(get_workspace().logs_dir, console_enabled=False)
    results = prune_web_dev_stacks()
    print(format_web_stack_prune_report(results))


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "help":
        run_help()
        return
    configure_workspace(Path(args.workdir), launch_mode="web" if args.command == "web" else "cli")
    if args.command == "web":
        web_action = getattr(args, "web_action", "start") or "start"
        if web_action == "status":
            run_web_status()
            return
        if web_action == "stop":
            run_web_stop()
            return
        if web_action == "prune":
            run_web_prune()
            return
        run_web_start(
            host=args.host,
            port=args.port,
            share_frontend=bool(getattr(args, "share_frontend", False)),
            verbose=bool(getattr(args, "verbose", False)),
        )
        return
    session_id = (args.session or "").strip() or generate_session_id("cli")
    run_cli_session(session_id=session_id, mode=args.mode)
