# -*- coding: utf-8 -*-
"""
数据模型层：审计留痕 与 素材授权标记
================================================================
对应模块：
    M1 审计留痕（REQ-5 / f45）
    M9 素材授权标记（f02 / f11）

f45 原文："审计要看到谁查过、使用哪个版本、提出什么建议及谁确认，
访问记录不能只留下最终摘要。"

这句话拆成四个必须记录的字段：actor（谁查过）+ version_no（哪个版本）
+ detail（提了什么建议）+ 关联确认记录（谁确认）。
只存"最终摘要"的日志在审计面前等于没存。
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    """审计日志：只追加，不修改，不删除。

    为什么不用通用的 logging：
        审计要能按"人 / 简报 / 版本"三个维度反查，且必须和业务数据
        在同一个事务里落库。文件日志做不到关联查询，也会在事务回滚时
        与数据库状态不一致——那正好是 f45 最担心的"证据链断裂"。
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    actor_name: Mapped[str] = mapped_column(String(50), default="", comment="冗余存名字，人员离职也可读")

    # query|create|update|confirm|export|reject|violation
    action: Mapped[str] = mapped_column(String(30), index=True)
    target_type: Mapped[str] = mapped_column(String(30), default="", comment="brief|version|constraint|asset")
    target_id: Mapped[Optional[int]] = mapped_column(Integer)

    brief_id: Mapped[Optional[int]] = mapped_column(ForeignKey("briefs.id"), index=True)
    # f45 要求"使用哪个版本"，所以版本号在日志里冗余一份
    version_no: Mapped[Optional[int]] = mapped_column(Integer)

    detail: Mapped[str] = mapped_column(Text, default="{}", comment="细节 JSON，含建议内容")
    # 违反硬约束时记录规则码，这类日志是审计重点
    rule_code: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def set_detail(self, data: Dict[str, Any]) -> None:
        self.detail = json.dumps(data, ensure_ascii=False)

    def get_detail(self) -> Dict[str, Any]:
        try:
            v = json.loads(self.detail or "{}")
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}


class Asset(Base):
    """素材授权标记（f02 / f11）。

    f02 验收口径："未授权素材明确标记"
    f11 真实案例："客户停用一条宣传语，但演示稿仍引用旧版本，需查原始版本后判断责任"

    所以这张表要能回答两件事：
      1. 这个素材有没有授权？（authorized）
      2. 它被停用了，但还有哪些地方在引用？（deprecated + used_in）

    第二点是责任判定的依据。没有 used_in，出现 f11 那种事故时
    只能靠人翻文件，正是访谈里说的"追溯断点"。
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[Optional[int]] = mapped_column(ForeignKey("briefs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), comment="素材名称，如某条宣传语")
    source: Mapped[str] = mapped_column(String(300), default="", comment="来源")

    authorized: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已授权")
    # f11：停用的素材要在引用处告警，而不是悄悄消失
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已停用")
    used_in: Mapped[str] = mapped_column(Text, default="[]", comment="被引用位置 JSON 数组")

    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
