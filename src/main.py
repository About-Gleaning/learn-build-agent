from agent.core.message import get_message_text
from agent.runtime.session import clear_session_memory, run_session


def main() -> None:
    session_id = "cli-session-002"
    mode = "build"

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
        answer = get_message_text(result)
        print(f"\n助手：{answer}")


if __name__ == "__main__":
    main()
