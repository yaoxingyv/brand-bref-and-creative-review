# -*- coding: utf-8 -*-
"""
数据模型层：授权确认记录 与 人工处理队列
================================================================
对应模块：
    M1 授权确认（REQ-5 / f41 f22 f14）
    M7 人工处理队列（REQ-5 / f44）

这两张表是"模型不能拍板"这件事在技术上的落点。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Approval(Base):
    """确认动作记录。

    关键设计：**建议值与实际值分列存放**（f14）。

    f14 原文："正式闭环前由当班人员核对关键字段；系统建议不能覆盖原值或省略确认。"
    所以系统产出的任何东西都写进 suggested_value / suggested_by_model，
    人确认的结果写进 final_value。两列永不互相覆盖。
    这样审计时能回答"系统当时建议了什么、人最后定了什么、是不是照单全收"——
    照单全收本身就是需要被看见的风险信号。

    is_authorized 记录这次确认是否由授权人做出。非授权人只能提交（submit），
    不能确认（confirm），这条边界在 service 层强制，不依赖调用方自觉。
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("brief_versions.id"))

    # submit=提交（一线可做） / confirm=正式确认（仅授权人） / reject=驳回
    action: Mapped[str] = mapped_column(String(20), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False, comment="确认人是否在授权名单内")

    # ---- 建议与原值双写，互不覆盖（f14）----
    original_value: Mapped[str] = mapped_column(Text, default="")
    suggested_value: Mapped[str] = mapped_column(Text, default="", comment="系统建议，仅供参照")
    suggested_by_model: Mapped[str] = mapped_column(String(50), default="", comment="产出建议的模型名")
    final_value: Mapped[str] = mapped_column(Text, default="", comment="人确认后的最终值")

    conclusion: Mapped[str] = mapped_column(Text, default="", comment="确认意见")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ManualQueue(Base):
    """人工处理队列（f44）。

    f44 原文："无法确认版本或关键字段时进入人工处理队列，必要时暂停试点，不能默认通过。"

    三个必须落地的点：
      1. 入队而不是放行 —— 置信不足时系统的正确动作是交给具名的人
      2. assignee 是**具名接收人** —— 访谈明确要求"为低置信、缺资料、
         冲突和越权安排具名人工接收人"。写"待分配"等于没处理。
      3. 队列非空时可暂停试点 —— 这是傅景的否决权，做成状态位而非口头约定
    """

    __tablename__ = "manual_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("brief_versions.id"))

    # version_unconfirmed | field_missing | conflict | expired_source | unauthorized
    reason_code: Mapped[str] = mapped_column(String(40), index=True)
    reason_detail: Mapped[str] = mapped_column(Text, default="")

    assignee: Mapped[str] = mapped_column(String(50), default="", comment="具名接收人")
    # pending | resolved | paused
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    resolved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
