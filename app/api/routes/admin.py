# -*- coding: utf-8 -*-
"""
入口层：管理后台数据接口
================================================================
管理端六个模块的数据源：概览 / 审计 / 违规 / 素材 / 用户 / 数据模型。
统一要求：只有管理后台类角色可访问（require_backoffice 或 admin）。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_backoffice
from app.core.database import get_session
from app.models.approval import ManualQueue
from app.models.audit import Asset, AuditLog
from app.models.brief import Brief, BriefConstraint
from app.models.user import User
from app.services import approval_service, audit_service

router = APIRouter(prefix="/admin", tags=["管理后台"])


@router.get("/dashboard", summary="概览")
async def dashboard(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(require_backoffice),
):
    """管理端首页数据：简报数、约束确认进度、待处理队列、违规次数。"""
    total_briefs = (await db.execute(select(func.count(Brief.id)))).scalar() or 0

    c = await db.execute(select(BriefConstraint.status, func.count(BriefConstraint.id)).group_by(BriefConstraint.status))
    status_count = {row[0]: row[1] for row in c.all()}

    pending = (
        await db.execute(
            select(func.count(ManualQueue.id)).where(ManualQueue.status == "pending")
        )
    ).scalar() or 0

    violations = (
        await db.execute(select(func.count(AuditLog.id)).where(AuditLog.rule_code != ""))
    ).scalar() or 0

    return {
        "briefs": total_briefs,
        "constraints": {
            "confirmed": status_count.get("confirmed", 0),
            "unconfirmed": status_count.get("unconfirmed", 0),
            "missing": status_count.get("missing", 0),
            "target": 30,
        },
        "pending_queue": pending,
        "violations": violations,
    }


@router.get("/audit-logs", summary="审计日志")
async def audit_logs(
    brief_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(require_backoffice),
):
    logs = await audit_service.list_logs(db, brief_id=brief_id, action=action, limit=limit)
    return [
        {
            "id": log.id,
            "actor": log.actor_name,
            "action": log.action,
            "target_type": log.target_type,
            "brief_id": log.brief_id,
            "version_no": log.version_no,
            "detail": log.get_detail(),
            "rule_code": log.rule_code,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@router.get("/violations", summary="硬约束拦截记录")
async def violations(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(require_backoffice),
):
    """审核负责人最关心的一类：谁试图越界。"""
    logs = await audit_service.list_violations(db)
    return [
        {
            "id": log.id,
            "rule_code": log.rule_code,
            "actor": log.actor_name,
            "brief_id": log.brief_id,
            "detail": log.get_detail(),
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@router.get("/assets", summary="素材授权标记")
async def assets(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    import json

    result = await db.execute(select(Asset).order_by(Asset.id))
    rows = list(result.scalars().all())
    out = []
    for a in rows:
        try:
            used_in = json.loads(a.used_in or "[]")
        except (ValueError, TypeError):
            used_in = []
        out.append(
            {
                "id": a.id,
                "brief_id": a.brief_id,
                "name": a.name,
                "authorized": a.authorized,
                "deprecated": a.deprecated,
                "used_in": used_in,
                "note": a.note,
                # f11：停用但仍有引用 = 必须告警的责任点
                "warning": a.deprecated and len(used_in) > 0,
            }
        )
    return out


@router.get("/users", summary="用户列表")
async def users(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(require_backoffice),
):
    result = await db.execute(select(User).order_by(User.id))
    return [
        {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
        }
        for u in result.scalars().all()
    ]


@router.get("/hard-rules", summary="七条硬约束")
async def hard_rules(_user: User = Depends(get_current_user)):
    return approval_service.list_hard_rules()


@router.get("/tables", summary="数据模型自检")
async def tables(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(require_backoffice),
):
    """列出各表行数，用于交付时核对数据是否落库。"""
    checks = [
        ("users", User),
        ("briefs", Brief),
        ("brief_constraints", BriefConstraint),
        ("assets", Asset),
        ("audit_logs", AuditLog),
        ("manual_queue", ManualQueue),
    ]
    out = []
    for name, model in checks:
        cnt = (await db.execute(select(func.count(model.id)))).scalar() or 0
        out.append({"table": name, "rows": cnt})
    return out
