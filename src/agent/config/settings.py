import os

from dotenv import load_dotenv

# 在模块加载时统一读取环境变量，避免各处重复调用。
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("MODEL", "qwen3-max")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
