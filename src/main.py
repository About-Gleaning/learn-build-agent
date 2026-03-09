import json
import os
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

from .tool import TOOL_HANDLERS, TOOLS

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("MODEL", "qwen3-max")

if not API_KEY:
    raise ValueError("缺少 API_KEY，请在 .env 文件中配置 API_KEY。")

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

WORKDIR = Path.cwd()

SYSTEM = """
你是{WORKDIR}的一名编码代理。
使用待办事项工具来规划多步骤任务。开始前标记为“进行中”，完成后标记为“已完成”。
优先使用工具而非文字描述。
"""

def agent_loop(user_input: str):
    messages = [
        {
            "role": "system",
            "content": ""
        },
        {
            "role": "user",
            "content": user_input
        }
    ]
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        message = response.choices[0].message
        is_tool_calls = bool(getattr(message, "tool_calls", None))

        # 追加助手消息
        assistant_message = {
            "role": "assistant",
            "content": message.content
        }
        # 如果有工具调用，附加工具调用信息
        if is_tool_calls:
            assistant_message["tool_calls"] = []
            for tc in message.tool_calls or []:
                assistant_message["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        messages.append(assistant_message)


        # 如果不是工具调用，代表结束了
        if not is_tool_calls:
            return messages
        
        # 工具调用
        for tool_call in message.tool_calls or []:
            handler = TOOL_HANDLERS.get(tool_call.function.name)
            if handler:
                args = json.loads(tool_call.function.arguments)
                result = handler(**args)
            else:
                result = "Error: Unknown tool"
            # 工具调用结果封装为 message
            tool_message = {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id
            }
            messages.append(tool_message)



        
if __name__ == "__main__":
    result = agent_loop("""

""")
    print("最终结果：")
    print(result)
