# -*- coding: utf-8 -*-
"""
LLM 供应商抽象层（可替换）
================================================================
对外只暴露两个函数：
    chat(system_prompt, user_prompt) -> str
    get_provider_name() -> str

内部按 settings.LLM_PROVIDER 路由：
    mock          —— 离线假模型，零依赖零费用，跑通链路与测试用
    openai_compat —— OpenAI 兼容协议；DeepSeek / 通义 / GLM 只改 base_url

新增供应商（如 Anthropic / 本地 Ollama）只需：
    1. 在 app/llm/ 下加一个实现文件；
    2. 在 factory.py 的 _PROVIDERS 里注册。
业务层完全无感。
"""

from typing import Protocol


class LLMProvider(Protocol):
    """所有 LLM 供应商需实现的接口。"""

    name: str

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...


__all__ = ["LLMProvider"]
