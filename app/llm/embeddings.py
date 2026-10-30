# -*- coding: utf-8 -*-
"""
嵌入模型抽象层（可替换）
================================================================
对外只暴露：
    embed_texts(texts: list[str]) -> list[list[float]]
    get_dim() -> int

两种后端：
    local —— 本地哈希词袋（离线、零依赖、确定性）。语义能力弱，
             仅用于"链路能跑通"，BM25 在此模式下承担主要召回职责。
    api   —— 远程嵌入接口（OpenAI 兼容 /embeddings）。真正的语义检索。

注意：切换后端后**必须重建向量索引**（维度不同），
     知识库摄取服务会在切换时自动重建（见 knowledge_service.reindex_all）。
"""

import hashlib
import json
import urllib.error
import urllib.request
from typing import List

from app.core.config import settings
from app.core.tokenize import tokenize


def get_dim() -> int:
    """当前嵌入维度。"""
    return settings.EMBEDDING_DIM if settings.EMBEDDING_PROVIDER == "local" else _api_dim_cache


# api 模式下维度在首次调用后确定（不同模型维度不同）
_api_dim_cache: int = 1536


def embed_local(text: str) -> List[float]:
    """本地哈希词袋向量：token 哈希到固定维度后累加，再 L2 归一化。

    用 hashlib 而非内置 hash()，因为 Python 的 hash() 对字符串
    每次进程启动都会变（PYTHONHASHSEED 随机化），会导致索引无法复用。
    """
    vec = [0.0] * settings.EMBEDDING_DIM
    for tok in tokenize(text):
        digest = hashlib.md5(tok.encode("utf-8")).digest()
        h = int.from_bytes(digest[:4], "big") % settings.EMBEDDING_DIM
        vec[h] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed_api(texts: List[str]) -> List[List[float]]:
    """调用 OpenAI 兼容的 /embeddings 接口。"""
    global _api_dim_cache
    if not settings.LLM_API_KEY:
        raise RuntimeError("使用 API 嵌入需配置 LLM_API_KEY")

    url = settings.LLM_BASE_URL.rstrip("/") + "/embeddings"
    payload = {"model": settings.EMBEDDING_MODEL, "input": texts}
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
        raise RuntimeError(f"嵌入接口返回 {e.code}: {body}") from e

    vectors = [d["embedding"] for d in data["data"]]
    if vectors:
        _api_dim_cache = len(vectors[0])
    return vectors


def embed_texts(texts: List[str]) -> List[List[float]]:
    """统一入口：按配置选择嵌入后端。上层检索代码只认这个函数。"""
    if settings.EMBEDDING_PROVIDER == "api":
        return embed_api(texts)
    return [embed_local(t) for t in texts]


def embed_one(text: str) -> List[float]:
    """单条文本的便捷入口（检索 query 用）。"""
    return embed_texts([text])[0]
