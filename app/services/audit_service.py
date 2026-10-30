# -*- coding: utf-8 -*-
"""
服务层：审计留痕
================================================================
对应 M1 审计留痕（REQ-5 / f45）

f45 原文："审计要看到谁查过、使用哪个版本、提出什么建议及谁确认，
访问记录不能只留下最终摘要。"

所以每一次写操作和**关键查询**都要落一条日志。特别地：
    · 违反硬约束的尝试也必须记录（rule_code 字段）——
      "谁试图越界"本身就是审计要的证据，拦下来不记录等于没拦。
"""

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record(
    db: AsyncSession,
    *,
    action: str,
    actor_id: Optional[int] = None,
    actor_name: str = "",
    target_type: str = "",
    target_id: Optional[int] = None,
    brief_id: Optional[int] = None,
    version_no: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    rule_code: str = "",
) -> AuditLog:
    """落一条审计日志。

    注意：本函数只 add 不 commit，由调用方在业务事务里一起提交。
    这样能保证"业务发生但日志没记"的情况不存在 —— 两者同生同灭。
    """
    log = AuditLog(
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        brief_id=brief_id,
        version_no=version_no,
        rule_code=rule_code,
    )
    log.set_detail(detail or {})
    db.add(log)
    return log


async def list_logs(
    db: AsyncSession,
    *,
    brief_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 100,
) -> list[AuditLog]:
    """按条件查询审计日志（管理端审计视图用）。"""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if brief_id is not None:
        stmt = stmt.where(AuditLog.brief_id == brief_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_violations(db: AsyncSession, limit: int = 50) -> list[AuditLog]:
    """只查违反硬约束的记录。审核负责人最关心这一类。"""
    stmt = (
        select(AuditLog)
        .where(AuditLog.rule_code != "")
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
