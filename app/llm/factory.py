# -*- coding: utf-8 -*-
"""LLM 供应商工厂：按配置返回具体实现（新增供应商在此注册即可）。"""

from functools import lru_cache

from app.core.config import settings
from app.llm.base import LLMProvider


@lru_cache
def get_llm() -> LLMProvider:
    """按 settings.LLM_PROVIDER 构建供应商实例（进程内单例）。"""
    provider = settings.LLM_PROVIDER
    if provider == "mock":
        from app.llm import mock
        return mock.build()
    if provider == "openai_compat":
        from app.llm import openai_compat
        return openai_compat.build()
    raise ValueError(f"未知 LLM_PROVIDER: {provider}")


def get_provider_name() -> str:
    return get_llm().name
