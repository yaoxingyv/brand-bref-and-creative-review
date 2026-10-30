# -*- coding: utf-8 -*-
"""LLM 与嵌入抽象层：供应商可替换。"""

from app.llm.embeddings import embed_one, embed_texts, get_dim
from app.llm.factory import get_llm, get_provider_name

__all__ = ["get_llm", "get_provider_name", "embed_texts", "embed_one", "get_dim"]
