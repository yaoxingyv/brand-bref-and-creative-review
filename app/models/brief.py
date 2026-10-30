# -*- coding: utf-8 -*-
"""
数据模型层：品牌创意简报本体、约束条目、版本、交接、缺项清单
================================================================
对应模块：
    M2 简报结构化（REQ-3 / f31）  关键字段 + 缺失值与未确认值区分
    M3 缺项清单 （REQ-1 / f10 f15）一页式，每条附来源位置
    M4 版本交接 （REQ-2 / f23 f24）双条件判定 + 交接登记卡
    M6 过期覆盖 （REQ-4 / f34 f35）双时间戳 + 快照保留

三个容易做错、这里刻意做对的地方：

1) **缺失值与未确认值必须区分**（f31 原文要求）
   用 None 表示"这个字段根本没填"，用 status 表示"填了但没确认"。
   两者在缺项清单里是两种不同的处理动作：前者要补录，后者要催确认。
   如果只用空字符串或默认值，这个区别就丢了，秦芮那层的数据质量检查（f33）会失效。

2) **版本判定不靠文件名**（f24 原文："不靠文件名猜测最新版本"）
   所以版本表存两个时间戳：业务发生时间（biz_occurred_at）与
   系统接收时间（sys_received_at）。现场习惯先用群消息处理再补表（f13），
   回填时间不能当作发生时间——这就是双时间戳存在的唯一理由。

3) **确认状态与版本有效期是两个独立条件**（f24）
   只有"在有效期内 **且** 已确认"才算当前有效版本。
   任一不满足都不算，且不能自动取最新，要进人工队列（f44）。
"""

import json
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Brief(Base):
    """品牌创意简报主表。

    brief_no 是"统一关联编号"的落地（f30/f31）。访谈里秦芮说三类工具
    并存、简报无统一关联编号，跨工具只能人工比对——这个编号就是
    把三类工具缝起来的那根线，是只读集成（M5）的前置条件。
    """

    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_no: Mapped[str] = mapped_column(String(50), unique=True, index=True, comment="统一关联编号")
    title: Mapped[str] = mapped_column(String(200), default="")

    # ---- REQ-3 / f31 点名的五个关键字段 ----
    # 受众、传播渠道、约束条款与版本都在本表或约束表；这里放前两个
    audience: Mapped[Optional[str]] = mapped_column(String(200), comment="受众")
    channel: Mapped[Optional[str]] = mapped_column(String(200), comment="传播渠道")

    # ---- 行业调研指出的首要痛点：单一诉求模糊 ----
    # 对应"作品总差一口气，反复微调"这个失败模式
    single_message: Mapped[Optional[str]] = mapped_column(Text, comment="单一诉求")
    deliverables: Mapped[str] = mapped_column(Text, default="[]", comment="交付物清单 JSON 数组")
    budget: Mapped[Optional[str]] = mapped_column(String(100))

    # f23：交接必须列明截止时间
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # draft | submitted | confirmed | rejected | archived
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    # 试点范围：f05 只覆盖一个批次的简报
    batch_no: Mapped[str] = mapped_column(String(50), default="PILOT-01", index=True)

    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def get_deliverables(self) -> List[str]:
        """解析交付物清单。存 JSON 字符串是为了兼容 SQLite 与 Postgres。"""
        try:
            v = json.loads(self.deliverables or "[]")
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []


class BriefConstraint(Base):
    """品牌约束条目（验收口径：30 项全部追溯到客户确认，f02 / f42）。

    为什么约束要单独建表而不是塞进 brief 的一个 JSON 字段：
        验收要"30 项逐条可追溯"，每一项都要有自己的来源位置、确认人、
        确认时间。塞进 JSON 就没法按条查询、统计和审计，
        傅景要的"谁查过、用哪个版本、谁确认"（f45）也就无从回答。

    status 三态（f31 缺失值与未确认值必须区分）：
        confirmed   已由确认
        unconfirmed 内容已录入，但尚未确认
        missing     这一项根本没有内容，需要补录
    """

    __tablename__ = "brief_constraints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, comment="第几项，1..30")

    content: Mapped[str] = mapped_column(Text, default="", comment="约束内容")
    # f15：设计师要"一页缺项清单及对应来源位置"，来源位置是硬要求
    source_location: Mapped[str] = mapped_column(
        String(300), default="", comment="来源位置，如：客户访谈记录 2026-10-20 第3段"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="missing", index=True, comment="confirmed|unconfirmed|missing"
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")


class BriefVersion(Base):
    """简报版本 —— 双时间戳 + 双条件判定的载体（REQ-2 / REQ-4）。

    这是整个系统最"值钱"的一张表，因为四个需求（REQ-2/3/4）都压在它身上：
      · f34 更新时同时记录业务发生时间与系统接收时间
      · f24 用版本有效期 + 确认状态两个条件判定，作废记录保留原因
      · f35 读取失败保留上次快照并标记时间，提示人工核查
    """

    __tablename__ = "brief_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, comment="版本号，从 1 递增")

    # ---------------- 双时间戳（f34）----------------
    biz_occurred_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="业务发生时间：事情真正发生的时间，来自人工登记"
    )
    sys_received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, comment="系统接收时间：写入系统的时间，自动生成"
    )

    # ---------------- 双条件（f24）----------------
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="版本有效期")
    confirm_status: Mapped[str] = mapped_column(
        String(20), default="unconfirmed", comment="confirmed|unconfirmed|void"
    )
    # f24：作废记录保留原因，不靠删除
    void_reason: Mapped[str] = mapped_column(Text, default="")

    # ---------------- 过期来源与快照（f35）----------------
    is_expired_source: Mapped[bool] = mapped_column(
        default=False, comment="True=这是一次来自过期来源的更新，不得覆盖已确认新版本"
    )
    raw_snapshot: Mapped[str] = mapped_column(Text, default="", comment="原文快照")
    snapshot_marked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def is_effective(self, now: Optional[datetime] = None) -> bool:
        """双条件判定：在有效期内 **且** 已确认，才算当前有效版本。

        注意这里是 AND 不是 OR —— 只满足一个条件的版本不能自动上位，
        这正是 f44"不能默认通过"的落地。调用方拿到 False 后应当
        把该简报送进人工处理队列，而不是退回上一个版本了事。
        """
        now = now or datetime.utcnow()
        in_validity = (self.valid_until is None) or (self.valid_until >= now)
        return in_validity and self.confirm_status == "confirmed"


class Handover(Base):
    """交接登记卡（f23 / f20）。

    唐映秋的原话是"交接时必须列明最新版本、尚未确认事项和截止时间，
    不能只发一条'已处理'的消息"。所以这三个字段是必填校验项，
    缺任何一个都不允许登记——这是规则引擎的活，不是提示词的活。
    """

    __tablename__ = "handovers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("brief_versions.id"))

    latest_version: Mapped[int] = mapped_column(Integer, default=1, comment="最新版本")
    unconfirmed_items: Mapped[str] = mapped_column(
        Text, default="[]", comment="未确认事项 JSON 数组"
    )
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="截止时间")

    # f20：每次交接记录接收人；f25：跨团队变更需登记责任人
    receiver: Mapped[str] = mapped_column(String(50), default="", comment="接收人")
    handed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    handed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[str] = mapped_column(Text, default="")

    def set_unconfirmed_items(self, items: List[Any]) -> None:
        self.unconfirmed_items = json.dumps(items, ensure_ascii=False)

    def get_unconfirmed_items(self) -> List[str]:
        try:
            v = json.loads(self.unconfirmed_items or "[]")
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []


class GapItem(Base):
    """缺项清单条目（REQ-1 / f10 / f15）。

    林亦舟要的是"一页缺项清单和对应来源位置，不需要很长的泛化摘要"。
    所以这个模型刻意做得极简：一条缺项 = 缺什么 + 去哪儿找。
    没有 summary 字段——加摘要就是违背需求原话。

    list_version 用于清单版本化：简报改版后重新生成的清单是新版本，
    旧清单保留，这样能回答"上一版就说缺这个，现在还缺吗"。
    """

    __tablename__ = "gap_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    list_version: Mapped[int] = mapped_column(Integer, default=1)

    gap_desc: Mapped[str] = mapped_column(Text, comment="缺什么")
    source_location: Mapped[str] = mapped_column(
        String(300), default="", comment="去哪儿找：来源位置"
    )
    # high|medium|low，用于排序，让一页纸先显示最要命的
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    # 关联到具体约束条目，便于从清单反查
    constraint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("brief_constraints.id"))
    is_resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
