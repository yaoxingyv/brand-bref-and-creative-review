# -*- coding: utf-8 -*-
"""
FAISS 向量库实现（带落盘持久化）
================================================================
  · 用 IndexFlatIP（内积）。向量入库前做 L2 归一化，因此内积 == 余弦相似度。
  · 索引与 payload（原文/文档信息）一起落盘到 data/vectordb/，
    重启后自动加载，避免每次启动重算嵌入（Stage1 原型的遗留问题）。
  · 规模变大后可换 IndexIVFFlat 做近似检索，本类接口保持不变。
"""

import json
import os
from typing import List, Tuple

import faiss
import numpy as np

from app.core.config import settings


class FAISSStore:
    """内存 FAISS 索引 + JSON payload，支持 save/load。"""

    def __init__(self, dim: int, persist_dir: str = None):
        self.dim = dim
        self.persist_dir = persist_dir or settings.VECTOR_DIR
        self.index_path = os.path.join(self.persist_dir, "index.faiss")
        self.meta_path = os.path.join(self.persist_dir, "payloads.json")
        self.index = faiss.IndexFlatIP(dim)
        self.payloads: List[dict] = []  # 与 index 行号一一对应

    # ---------------- 写入 ----------------
    def add(self, vectors: List[List[float]], payloads: List[dict]) -> None:
        if not vectors:
            return
        arr = np.asarray(vectors, dtype="float32")
        # 归一化：使内积等价于余弦相似度
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        self.index.add(arr)
        self.payloads.extend(payloads)

    def clear(self) -> None:
        """清空索引（重建知识库时调用）。"""
        self.index = faiss.IndexFlatIP(self.dim)
        self.payloads = []

    # ---------------- 检索 ----------------
    def search(self, query_vec: List[float], topk: int = 3) -> List[Tuple[int, float]]:
        if self.index.ntotal == 0:
            return []
        q = np.asarray(query_vec, dtype="float32").reshape(1, -1)
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm
        topk = min(topk, self.index.ntotal)
        sims, idxs = self.index.search(q, topk)
        out = []
        for j in range(len(idxs[0])):
            i = int(idxs[0][j])
            if i < 0:            # FAISS 候选不足时补 -1
                continue
            out.append((i, float(sims[0][j])))
        return out

    def payload(self, idx: int) -> dict:
        if 0 <= idx < len(self.payloads):
            return self.payloads[idx]
        return {}

    def size(self) -> int:
        return self.index.ntotal

    # ---------------- 持久化 ----------------
    # 注意：不用 faiss.write_index/read_index。
    # 原因：FAISS 底层用 C 的 fopen 打开文件，在 Windows 上对**中文路径**会失败
    # （报 "could not open ... for writing"）。这里改为序列化成字节后由 Python 落盘，
    # 路径编码交给 Python 处理，彻底规避该问题。
    def save(self) -> None:
        os.makedirs(self.persist_dir, exist_ok=True)
        buf = faiss.serialize_index(self.index)          # 返回 numpy uint8 数组
        with open(self.index_path, "wb") as f:
            f.write(np.asarray(buf, dtype=np.uint8).tobytes())
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump({"dim": self.dim, "payloads": self.payloads}, f, ensure_ascii=False)

    def load(self) -> bool:
        """加载磁盘索引。成功返回 True，不存在/损坏/维度不匹配返回 False。"""
        if not (os.path.exists(self.index_path) and os.path.exists(self.meta_path)):
            return False
        try:
            with open(self.index_path, "rb") as f:
                buf = np.frombuffer(f.read(), dtype=np.uint8)
            index = faiss.deserialize_index(buf)
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("dim") != index.d:
                return False       # 维度不匹配（换了嵌入模型），要求重建
            self.index = index
            self.dim = index.d
            self.payloads = meta.get("payloads", [])
            return len(self.payloads) == self.index.ntotal
        except Exception:
            return False
