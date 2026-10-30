# -*- coding: utf-8 -*-
"""
BM25 关键词检索
================================================================
为什么 RAG 里还要 BM25：
    制度文档里大量是精确术语（"住宿费标准""审批权限""2000元"），
    纯向量检索对这类词敏感度过低，容易召回语义相近但数字/对象错误的片段。
    BM25 负责"命中准确"，向量负责"语义扩展"，两者融合最稳。

标准 Okapi BM25 实现，纯标准库，无额外依赖。
"""

import math
from typing import List, Tuple

from app.core.tokenize import tokenize


class BM25Retriever:
    """基于内存语料的 BM25。语料变化时重新 fit 即可。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: List[List[str]] = []
        self.doc_len: List[int] = []
        self.avgdl: float = 0.0
        self.idf: dict = {}

    def fit(self, corpus: List[str]) -> None:
        """用给定语料训练（计算 IDF 与文档长度统计）。"""
        self.docs = [tokenize(d) for d in corpus]
        n = len(self.docs)
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = (sum(self.doc_len) / n) if n else 0.0

        df: dict = {}
        for d in self.docs:
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        # IDF 平滑公式，避免 c=0 或 c=n 时除以零
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, topk: int = 3) -> List[Tuple[int, float]]:
        """返回 [(语料下标, 分数)]，按分数降序。"""
        if not self.docs:
            return []
        q_tokens = tokenize(query)
        scores: List[float] = []

        for i, doc in enumerate(self.docs):
            tf: dict = {}
            for w in doc:
                tf[w] = tf.get(w, 0) + 1

            dl = self.doc_len[i] or 1
            denom_base = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            score = 0.0
            for w in q_tokens:
                f = tf.get(w, 0)
                if f == 0 or w not in self.idf:
                    continue
                score += self.idf[w] * (f * (self.k1 + 1)) / (f + denom_base)
            scores.append(score)

        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:topk]
        return [(i, scores[i]) for i in ranked if scores[i] > 0]
