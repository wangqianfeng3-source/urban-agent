"""LLM 接入层 —— OpenAI 兼容接口，一套代码通吃通义千问 / DeepSeek 等。

环境变量：
  LLM_API_KEY   必填。通义在 dashscope 控制台申请，DeepSeek 在 platform.deepseek.com。
  LLM_BASE_URL  选填。默认通义千问兼容端点；DeepSeek 填 https://api.deepseek.com
  LLM_MODEL     选填。默认 qwen-plus；DeepSeek 填 deepseek-chat

把 key 写在 shell 环境或 .env 里，严禁提交进 git。
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


def _load_dotenv() -> None:
    """Load simple KEY=VALUE pairs from project .env without extra dependencies."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_model() -> str:
    _load_dotenv()
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def get_client():
    _load_dotenv()
    key = os.getenv("LLM_API_KEY")
    if not key:
        raise RuntimeError(
            "未设置 LLM_API_KEY。请先申请 API key 并 export LLM_API_KEY=...；"
            "跑通闭环不需要大模型，可先用 --editor mock（默认）。"
        )
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL))

