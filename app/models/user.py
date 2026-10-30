# -*- coding: utf-8 -*-
"""
数据模型层：用户与授权确认人名单
================================================================
对应模块：M1 授权与审计（REQ-5，P0 前置）

角色直接映射访谈报告里的六位负责人，不是抽象的权限组：
    business_owner 顾明岚  业务决策，定范围预算，**唯一有正式确认权**
    coordinator    唐映秋  流程协调，唯一横跨全链路，负责交接登记
    designer       林亦舟  设计执行，缺项清单的主要使用者
    auditor        傅景    审核与质量，授权确认人之一，保留停止试点权
    engineer       秦芮    工具与数据，只读导出
    client_rep     叶书    客户协同，确认结果用途

为什么角色要具体到人名对应的职能：
    访谈 f22 说"正式确认由对应业务负责人完成，模型没有审批权限"——
    这句话的主语是岗位不是权限位。如果只做 admin/employee 两档，
    就无法表达"谁能确认"这个验收核心（f41），傅景不会签字。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """系统用户。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, comment="登录名")
    password_hash: Mapped[str] = mapped_column(String(255), comment="pbkdf2 哈希，不存明文")
    full_name: Mapped[str] = mapped_column(String(50), default="", comment="姓名，页面上展示用")
    role: Mapped[str] = mapped_column(
        String(20),
        default="designer",
        index=True,
        comment="business_owner|coordinator|designer|auditor|engineer|client_rep|admin",
    )
    # 审计需要"谁做的"，所以禁用账号不用删除，只置为不活跃
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User {self.username} ({self.role})>"


class AuthorizedApprover(Base):
    """授权确认人名单（M1 的核心交付物之一）。

    为什么单独建表而不是写死在配置里：
        f41 要求"正式结论须由授权人员确认"，而访谈明确写了
        "试点阶段哪些角色算授权确认人、异常拦截标准由谁定尚未核实"。
        既然名单未定，就必须可维护——试点方案里要书面列出并让
        业务负责人与审核负责人共同确认（报告"下一步验证"原文）。
        写死等于替客户做了这个决定，验收时会被打回。

    有效性用 valid_until 而非布尔：人员调岗后历史确认记录仍需可追溯，
    删除名单项会破坏旧记录的证据链（f45 审计要求）。
    """

    __tablename__ = "authorized_approvers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 确认范围：brief_confirm=简报确认 / final_approval=终审签字
    scope: Mapped[str] = mapped_column(String(30), default="brief_confirm", index=True)
    granted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), comment="由谁授予")
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="为空=长期有效")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="", comment="授权依据，审计时看")
