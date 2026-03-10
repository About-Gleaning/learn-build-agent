import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# 环境加载
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("MODEL", "qwen3-max")

if not API_KEY:
    raise ValueError("缺少 API_KEY，请在 .env 文件中配置 API_KEY。")

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)


def create_chat_completion(messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int = 4096):
    """统一封装大模型调用入口，避免在业务流程中散落底层 SDK 调用。"""
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
    )
