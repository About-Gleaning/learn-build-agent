from agent.core.message import get_message_text
from agent.runtime.session import run_session

if __name__ == "__main__":
    result = run_session(
        """
查看当前文件夹下是否有heelo.py文件，如果没有就创建一个，打印hello word。必须使用todo工具完成
""",
        "test-session-123",
    )
    print("最终结果：")
    print(get_message_text(result))
