# -*- coding: utf-8 -*-
"""
离线假模型（Mock LLM）
================================================================
作用：在没有任何 API Key、没有网络的环境下把整条链路跑通。

行为：
  · RAG 模式：解析 prompt 中的【制度上下文】，回显命中片段，便于验证检索是否正确；
  · 通用模式：说明当前是离线假模型，并给出接入真实模型的方法。

生产环境请勿使用（把 LLM_PROVIDER 切到 openai_compat 即可）。
"""

import re

from app.core.config import settings
from app.llm.base import LLMProvider


class MockProvider:
    name = "mock"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """解析 user_prompt 并给出结构化回显，代替真实生成。"""
        # 从 prompt 里提取编号片段 [1] [2] ...
        blocks = re.findall(r"^\[(\d+)\]\s*(.+)$", user_prompt, flags=re.MULTILINE)
        question = ""
        m = re.search(r"【(?:用户问题|当前提问)】\s*(.+?)\s*\n", user_prompt, flags=re.DOTALL)
        if m:
            question = m.group(1).strip()

        # ---------- RAG 模式 ----------
        if settings.USE_RAG:
            if not blocks:
                return "[MOCK 模式 · 未调用真实大模型] 未检索到任何制度片段，真实模型应回答：未在制度中找到相关规定。"
            lines = [f"[MOCK 模式 · 未调用真实大模型] 问题：{question}", "", "命中的制度片段："]
            for idx, text in blocks[:3]:
                lines.append(f"  [{idx}] {text[:120]}{'…' if len(text) > 120 else ''}")
            lines.append("")
            lines.append(
                "真实模型接入后（LLM_PROVIDER=openai_compat + LLM_API_KEY）会依据以上片段"
                "生成自然语言答案，并在句末标注来源编号。"
            )
            return "\n".join(lines)

        # ---------- 通用问答模式 ----------
        return (
            f"[MOCK 模式 · 当前是离线假模型，未调用真实大模型]\n"
            f"收到问题：{question}\n\n"
            f"接上真实模型后，这里会由大模型直接生成回答。配置方式（.env）：\n"
            f"  LLM_PROVIDER=openai_compat\n"
            f"  LLM_BASE_URL=https://api.deepseek.com/v1\n"
            f"  LLM_API_KEY=sk-xxx\n"
            f"  LLM_MODEL=deepseek-chat\n\n"
            f"若改用 RAG（先检索制度再作答），再加一行 USE_RAG=true。"
        )


def build() -> LLMProvider:
    return MockProvider()  # type: ignore[return-value]
