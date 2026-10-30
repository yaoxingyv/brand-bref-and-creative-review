# -*- coding: utf-8 -*-
"""
入口层：授权确认、人工队列、只读导出
================================================================
对应 M1 / M7 / M5。

这里最能体现"红线是硬的"：
    · 非授权人确认 → 403（不是警告，是拒绝）
    · 无有效版本确认 → 409（不是默认通过）
    · 导出只有只读一条路，系统里不存在写回接口
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_backoffice, require_decision_maker
from app.core.config import HardRuleViolation
from app.core.database import get_session
from app.models.user import User
from app.services import approval_service, audit_service, brief_service

router = APIRouter(prefix="/approvals", tags=["授权确认与人工队列"])


# ---------------- Schemas ----------------
class ConfirmRequest(BaseModel):
    version_id: Optional[int] = None
    final_value: str = Field(..., min_length=1, description="确认后的最终值")
    conclusion: str = ""


class SuggestRequest(BaseModel):
    version_id: Optional[int] = None
    original_value: str = ""
    suggested_value: str = Field(..., min_length=1)
    suggested_by_model: str = "rule-engine"


class AssignRequest(BaseModel):
    assignee: str = Field(..., min_length=1, description="具名接收人，不允许留空")


class ResolveRequest(BaseModel):
    resolution: str = Field(..., min_length=1)


class PauseRequest(BaseModel):
    reason: str = Field(..., min_length=1)


# ---------------- 授权确认 ----------------
@router.post("/briefs/{brief_id}/confirm", summary="正式确认简报（仅授权人）")
async def confirm_brief(
    brief_id: int,
    payload: ConfirmRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_decision_maker),
):
    """正式确认。非授权人或版本不可确认时会被硬约束拦截。"""
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    try:
        approval = await approval_service.confirm_brief(
            db,
            brief=brief,
            version_id=payload.version_id,
            actor=current_user,
            final_value=payload.final_value,
            conclusion=payload.conclusion,
        )
    except HardRuleViolation as exc:
        # 硬约束拦截：返回 403 并把规则码带给前端，让人知道触了哪条线
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return {"id": approval.id, "action": approval.action, "is_authorized": approval.is_authorized}


@router.post("/briefs/{brief_id}/suggest", summary="提交系统建议（不覆盖原值）")
async def suggest(
    brief_id: int,
    payload: SuggestRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """模型/规则引擎产出建议。只写 suggested_value，永不碰 final_value（R1/R3）。"""
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    approval = await approval_service.submit_suggestion(
        db,
        brief_id=brief_id,
        version_id=payload.version_id,
        original_value=payload.original_value,
        suggested_value=payload.suggested_value,
        suggested_by_model=payload.suggested_by_model,
        actor_id=current_user.id,
        actor_name=current_user.full_name or current_user.username,
    )
    return {"id": approval.id, "action": "submit", "note": "建议已记录，需授权人确认后生效"}


# ---------------- 授权名单（M1）----------------
@router.get("/approvers", summary="授权确认人名单")
async def approvers(
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    return await approval_service.list_approvers(db)


@router.get("/hard-rules", summary="七条硬约束")
async def hard_rules(_user: User = Depends(get_current_user)):
    return approval_service.list_hard_rules()


# ---------------- 人工队列（M7）----------------
@router.get("/queue", summary="人工处理队列")
async def queue_list(
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    items = await approval_service.list_queue(db, status=status_filter)
    return [
        {
            "id": i.id,
            "brief_id": i.brief_id,
            "reason_code": i.reason_code,
            "reason_detail": i.reason_detail,
            "assignee": i.assignee,
            "status": i.status,
            "created_at": i.created_at.isoformat(),
        }
        for i in items
    ]


@router.post("/queue/{queue_id}/assign", summary="指定具名接收人")
async def assign(
    queue_id: int,
    payload: AssignRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_backoffice),
):
    try:
        item = await approval_service.assign_queue(
            db,
            queue_id=queue_id,
            assignee=payload.assignee,
            actor_id=current_user.id,
            actor_name=current_user.full_name or current_user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"id": item.id, "assignee": item.assignee}


@router.post("/queue/{queue_id}/resolve", summary="处理完成")
async def resolve(
    queue_id: int,
    payload: ResolveRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_backoffice),
):
    try:
        item = await approval_service.resolve_queue(
            db,
            queue_id=queue_id,
            resolution=payload.resolution,
            actor_id=current_user.id,
            actor_name=current_user.full_name or current_user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"id": item.id, "status": item.status}


@router.post("/pilot/pause", summary="暂停试点（审核负责人否决权）")
async def pause(
    payload: PauseRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_decision_maker),
):
    count = await approval_service.pause_pilot(
        db,
        actor_id=current_user.id,
        actor_name=current_user.full_name or current_user.username,
        reason=payload.reason,
    )
    return {"paused_count": count, "reason": payload.reason}


# ---------------- 只读导出（M5 / R4）----------------
@router.get("/briefs/{brief_id}/export", summary="脱敏只读导出")
async def export_brief(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """导出脱敏数据。系统内不存在任何写回接口（f32）。"""
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    data = await approval_service.export_brief_readonly(db, brief)
    await audit_service.record(
        db,
        action="export",
        actor_id=current_user.id,
        actor_name=current_user.full_name or current_user.username,
        target_type="brief",
        target_id=brief_id,
        brief_id=brief_id,
        detail={"mode": "readonly"},
    )
    await db.commit()
    return data
