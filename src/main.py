import json
import os

from openai import OpenAI
from dotenv import load_dotenv

from tool import TOOL_HANDLERS, TOOLS

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


def agent_loop(user_input: str):
    messages = [
        {
            "role": "system",
            "content": "你叫爪爪，是一个有帮助的助手。必要时调用工具。"
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
在src目录下创建一个todo_manager.py文件。
内容是一个TodoManager类，包含update, render方法。
update接收一个list，元素包含三个属性：id, text, status, text去除收尾空格后不能为空。
list长度不能超过20个，in_progress中的任务最多只能有一个。
处理status的时候先统一转小写。
stuatus必须是pending, in_progress, completed其中之一, id可以为空，为空的话就根据顺序自动设置，从1开始计数。
render方法返回一个字符串，包含所有待办事项的id、status和text，如果没有待办事项，返回"No todos."。
格式为参考：
```
[x] #1: 写需求文档
[>] #2: 实现接口

[ ] #3: 补单元测试

(1/3 completed)
```

update方法返回值为调用render方法的结果。
把todo_manager.py中的TodoManager的update方法，增加到tool.py文件中的TOOLS和TOOL_HANDLERS中。
""")
    print("最终结果：")
    print(result)
