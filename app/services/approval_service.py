# -*- coding: utf-8 -*-
"""
服务层：授权确认、硬约束拦截、人工处理队列
================================================================
对应模块：M1 授权与审计 / M7 人工处理队列（REQ-5，P0 前置）

这是七条硬约束真正被执行的地方。原则只有一条：

    **越界要"拦下来并记录"，不能"提示一下就放行"。**

每一处拦截都会往 audit_logs 写一条带 rule_code 的记录。
因为 f45 要的审计里，"谁试图越界"和"谁确认了"同样重要。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import HardRuleViolation, HardRules
from app.models.approval import Approval, ManualQueue
from app.models.brief import Brief, BriefVersion
from app.models.user import AuthorizedApprover, User
from app.services import audit_service


# =================================================================
# M1：授权确认人名单
# =================================================================
async def is_authorized(
    db: AsyncSession, user_id: int, scope: str = "brief_confirm"
) -> bool:
    """判断某人是否在授权确认人名单内且在有效期内（R2 / f41）。

    为什么名单查库而不是查角色：
        f22 只说"正式确认由对应业务负责人完成"，但访谈明确写了
        "试点阶段哪些角色算授权确认人尚未核实"。
        名单是可配置项，角色是代码常量——把未定的东西写进代码，
        等于替客户做了这个决定，验收时必被打回。
    """
    now = datetime.utcnow()
    result = await db.execute(
        select(AuthorizedApprover).where(
            AuthorizedApprover.user_id == user_id,
            AuthorizedApprover.scope == scope,
            AuthorizedApprover.is_active.is_(True),
        )
    )
    for row in result.scalars().all():
        if row.valid_until is None or row.valid_until >= now:
            return True
    return False


async def grant_approver(
    db: AsyncSession,
    *,
    user_id: int,
    scope: str = "brief_confirm",
    granted_by: Optional[int] = None,
    note: str = "",
    actor_name: str = "",
) -> AuthorizedApprover:
    """把某人加入授权确认人名单。"""
    row = AuthorizedApprover(
        user_id=user_id, scope=scope, granted_by=granted_by, note=note
    )
    db.add(row)
    await audit_service.record(
        db,
        action="grant_approver",
        actor_id=granted_by,
        actor_name=actor_name,
        target_type="user",
        target_id=user_id,
        detail={"scope": scope, "note": note},
    )
    await db.commit()
    await db.refresh(row)
    return row


async def list_approvers(db: AsyncSession) -> List[Dict[str, Any]]:
    """列出授权确认人名单（M1 的展示与核对视图）。"""
    result = await db.execute(
        select(AuthorizedApprover, User)
        .join(User, User.id == AuthorizedApprover.user_id)
        .order_by(AuthorizedApprover.id)
    )
    rows = []
    for appr, user in result.all():
        rows.append(
            {
                "id": appr.id,
                "user_id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "role": user.role,
                "scope": appr.scope,
                "valid_until": appr.valid_until.isoformat() if appr.valid_until else None,
                "is_active": appr.is_active,
                "note": appr.note,
            }
        )
    return rows


# =================================================================
# R1 / R3：提交建议（模型侧唯一的写入口）
# =================================================================
async def submit_suggestion(
    db: AsyncSession,
    *,
    brief_id: int,
    version_id: Optional[int],
    original_value: str,
    suggested_value: str,
    suggested_by_model: str = "rule-engine",
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> Approval:
    """记录一条"系统建议"（R1 模型没有审批权限 / R3 建议不覆盖原值）。

    这个接口是模型能走的**唯一**写入路径：
        · 只能写 suggested_value，永远不碰 final_value
        · action 固定是 submit，不可能是 confirm
    模型想确认？没有这条路。f22 就是这么落的。
    """
    approval = Approval(
        brief_id=brief_id,
        version_id=version_id,
        action="submit",
        actor_id=actor_id,
        is_authorized=False,
        original_value=original_value,
        suggested_value=suggested_value,
        suggested_by_model=suggested_by_model,
    )
    db.add(approval)
    await audit_service.record(
        db,
        action="suggest",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="brief",
        target_id=brief_id,
        brief_id=brief_id,
        detail={"suggested": suggested_value[:300], "by": suggested_by_model},
    )
    await db.commit()
    await db.refresh(approval)
    return approval


# =================================================================
# R2 / R7：正式确认
# =================================================================
async def confirm_brief(
    db: AsyncSession,
    *,
    brief: Brief,
    version_id: Optional[int],
    actor: User,
    final_value: str,
    conclusion: str = "",
) -> Approval:
    """正式确认一份简报（R2 / f41）。

    两道闸门：
      1. 确认人必须在授权名单内，否则抛 HardRuleViolation 并留痕
      2. 无法确定版本时（version_id 为空）不得默认通过（R7 / f44），
         同样抛异常，由调用方转人工队列

    为什么用异常而不是返回 False：
        返回 False 容易被上层"先记下来再说"地吞掉。抛异常会强制
        调用方处理，处理不了就 500 而不是悄悄通过。
        红线就该是硬的。
    """
    # ── 闸门一：授权人校验 ──
    if not await is_authorized(db, actor.id, "brief_confirm"):
        await audit_service.record(
            db,
            action="reject",
            actor_id=actor.id,
            actor_name=actor.full_name or actor.username,
            target_type="brief",
            target_id=brief.id,
            brief_id=brief.id,
            detail={"attempt": "confirm", "reason": "名单外账号尝试确认"},
            rule_code="R2_CONFIRM_BY_AUTHORIZED",
        )
        await db.commit()
        raise HardRuleViolation(
            "R2_CONFIRM_BY_AUTHORIZED",
            f"账号 {actor.username} 不在授权确认人名单内，确认请求已被拒绝",
        )

    # ── 闸门二：版本必须可确认 ──
    if version_id is None:
        await audit_service.record(
            db,
            action="reject",
            actor_id=actor.id,
            actor_name=actor.full_name or actor.username,
            brief_id=brief.id,
            detail={"attempt": "confirm", "reason": "无有效版本"},
            rule_code="R7_NO_SILENT_PASS",
        )
        await db.commit()
        raise HardRuleViolation(
            "R7_NO_SILENT_PASS", "当前没有可确认的有效版本，不得默认通过"
        )

    version = await db.get(BriefVersion, version_id)
    if version is None:
        raise HardRuleViolation("R7_NO_SILENT_PASS", f"版本 {version_id} 不存在")

    version.confirm_status = "confirmed"
    brief.status = "confirmed"

    approval = Approval(
        brief_id=brief.id,
        version_id=version_id,
        action="confirm",
        actor_id=actor.id,
        is_authorized=True,
        original_value="",
        final_value=final_value,
        conclusion=conclusion,
    )
    db.add(approval)

    await audit_service.record(
        db,
        action="confirm",
        actor_id=actor.id,
        actor_name=actor.full_name or actor.username,
        target_type="brief",
        target_id=brief.id,
        brief_id=brief.id,
        version_no=version.version_no,
        detail={"final_value": final_value[:300], "conclusion": conclusion},
    )
    await db.commit()
    await db.refresh(approval)
    return approval


# =================================================================
# M7：人工处理队列
# =================================================================
async def list_queue(
    db: AsyncSession, status: Optional[str] = None, limit: int = 100
) -> List[ManualQueue]:
    stmt = select(ManualQueue).order_by(ManualQueue.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(ManualQueue.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def assign_queue(
    db: AsyncSession,
    *,
    queue_id: int,
    assignee: str,
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> ManualQueue:
    """给队列项指定**具名**接收人。

    访谈要求"为低置信、缺资料、冲突和越权安排具名人工接收人"。
    所以 assignee 是必填的普通字符串（人名/岗位），不允许留空——
    留空等于"大家都负责"="没人负责"，正是现状的毛病。
    """
    item = await db.get(ManualQueue, queue_id)
    if item is None:
        raise ValueError(f"队列项 {queue_id} 不存在")
    item.assignee = assignee
    await audit_service.record(
        db,
        action="update",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="queue",
        target_id=queue_id,
        brief_id=item.brief_id,
        detail={"assignee": assignee},
    )
    await db.commit()
    await db.refresh(item)
    return item


async def resolve_queue(
    db: AsyncSession,
    *,
    queue_id: int,
    resolution: str,
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> ManualQueue:
    """处理完一条队列项。"""
    item = await db.get(ManualQueue, queue_id)
    if item is None:
        raise ValueError(f"队列项 {queue_id} 不存在")
    item.status = "resolved"
    item.resolution = resolution
    item.resolved_by = actor_id
    item.resolved_at = datetime.utcnow()
    await audit_service.record(
        db,
        action="update",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="queue",
        target_id=queue_id,
        brief_id=item.brief_id,
        detail={"resolution": resolution[:300]},
    )
    await db.commit()
    await db.refresh(item)
    return item


async def pause_pilot(
    db: AsyncSession, *, actor_id: Optional[int], actor_name: str, reason: str
) -> int:
    """暂停试点：把所有 pending 队列项置为 paused（f04 / f44 傅景的否决权）。"""
    result = await db.execute(
        select(ManualQueue).where(ManualQueue.status == "pending")
    )
    items = list(result.scalars().all())
    for item in items:
        item.status = "paused"
    await audit_service.record(
        db,
        action="pause_pilot",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="pilot",
        detail={"reason": reason, "paused_count": len(items)},
    )
    await db.commit()
    return len(items)


# =================================================================
# R4：脱敏只读导出
# =================================================================
def mask_text(text: str) -> str:
    """脱敏：手机号、邮箱、身份证。

    f32 原文："试点只开放脱敏只读导出；写回、外部消息和执行接口均不开放。"
    所以导出路径**只有**这一个函数，且系统里不存在任何写回接口。
    """
    import re

    if not text:
        return ""
    text = re.sub(r"1[3-9]\d{9}", "1**********", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "***@***", text)
    text = re.sub(r"\d{17}[\dXx]", "******************", text)
    return text


async def export_brief_readonly(db: AsyncSession, brief: Brief) -> Dict[str, Any]:
    """生成脱敏只读导出（M5 的数据面）。字段级脱敏，无写回能力。"""
    c_result = await db.execute(
        select(Brief.audience, Brief.channel, Brief.single_message)
    )
    del c_result  # 占位：实际字段直接从 brief 取，保持显式

    return {
        "brief_no": brief.brief_no,
        "title": mask_text(brief.title),
        "audience": mask_text(brief.audience or ""),
        "channel": mask_text(brief.channel or ""),
        "single_message": mask_text(brief.single_message or ""),
        "status": brief.status,
        "current_version": brief.current_version,
        "exported_at": datetime.utcnow().isoformat(),
        "mode": "readonly",   # 显式声明：此数据结构不可用于写回
    }


def list_hard_rules() -> List[Dict[str, str]]:
    """把七条硬约束暴露给前端展示（让人看见红线在哪）。"""
    return HardRules.RULES
