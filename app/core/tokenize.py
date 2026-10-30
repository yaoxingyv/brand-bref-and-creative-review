# -*- coding: utf-8 -*-
"""
核心层：中文分词（BM25 与本地嵌入共用的基础组件）
================================================================
为什么自己写而不引 jieba：
  · 零依赖、结果确定（不受词典版本影响），便于单元测试；
  · 制度文档术语集中（"报销""审批""住宿费"），单字 + 二元组的粗切分
    在 BM25 上表现已足够好。
  · 后续替换为 jieba / 专业分词，只需改本文件，上层无感。
"""

import re
from typing import List

# 汉字 / 英文字母数字 的 token 模式
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")

# 需要忽略的高频虚词（单字切分后噪声较大，BM25 中单独过滤）
_STOP_CHARS = set("的了在是和与及对为等一二三四五六七八九十")


def tokenize(text: str, use_bigram: bool = True) -> List[str]:
    """把文本切成 token 列表。

    策略：
      1. 英文/数字连续串作为一个整体 token（保留 "2000" "500" 这类关键数字）；
      2. 汉字按单字切；
      3. 可选追加相邻汉字二元组（"住宿" "审批"），显著提升中文 BM25 的召回质量。
    """
    raw = _TOKEN_PATTERN.findall(text.lower())

    tokens: List[str] = []
    for t in raw:
        if len(t) == 1 and "\u4e00" <= t <= "\u9fff":
            if t in _STOP_CHARS:
                continue
        tokens.append(t)

    if use_bigram:
        # 仅对相邻两个汉字生成二元组，不跨英文/数字
        for a, b in zip(raw, raw[1:]):
            if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
                tokens.append(a + b)

    return tokens
