# -*- coding: utf-8 -*-
"""
OpenAI 兼容协议供应商
================================================================
覆盖：DeepSeek / 通义千问 / 智谱 GLM / OpenAI 官方，以及任何兼容
      /v1/chat/completions 的网关（如 One-API、本地 vLLM）。
切换只需改 .env：
    LLM_BASE_URL=https://api.deepseek.com/v1
    LLM_MODEL=deepseek-chat
    LLM_API_KEY=sk-xxx

依赖取舍：用标准库 urllib 而非 openai SDK —— 少一个重型依赖，
         且各家的细微差异（如 response_format）自己可控。
"""

import json
import urllib.error
import urllib.request

from app.core.config import settings
from app.llm.base import LLMProvider


class OpenAICompatProvider:
    name = "openai_compat"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not settings.LLM_API_KEY:
            raise RuntimeError("未配置 LLM_API_KEY，无法调用真实大模型")

        url = settings.LLM_BASE_URL.rstrip("/") + "/chat/completions"
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.LLM_TEMPERATURE,
            "stream": False,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"LLM 接口返回 {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"无法连接 LLM 服务（{settings.LLM_BASE_URL}）: {e.reason}") from e

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"LLM 返回结构异常: {str(data)[:300]}") from e


def build() -> LLMProvider:
    return OpenAICompatProvider()  # type: ignore[return-value]
