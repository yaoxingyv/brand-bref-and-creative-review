# -*- coding: utf-8 -*-
"""
入口层：品牌创意简报路由
================================================================
对应 M2 / M3 / M4 / M6。

这一层只做三件事：收参数、调服务、把规则引擎的结论翻译成 HTTP 语义。
**所有判断都在服务层和规则引擎里**，路由不自己拍板。

特别约定：`resolve_effective_version` 判定失败时返回 409 而不是 200。
因为"没有可确认的版本"是一种必须被前端看见的状态，
返回 200 带个 ok=false 很容易被前端"先记下来"地吞掉 ——
那正好是 f44 禁止的"默认通过"。
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.services import brief_service

router = APIRouter(prefix="/briefs", tags=["品牌创意简报"])


# ---------------- Schemas ----------------
class BriefCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, description="简报标题")
    audience: Optional[str] = None
    channel: Optional[str] = None
    single_message: Optional[str] = None
    deliverables: List[str] = Field(default_factory=list)
    budget: Optional[str] = None
    deadline: Optional[datetime] = None


class VersionCreateRequest(BaseModel):
    # 业务发生时间：f13 强调回填时间不能当作发生时间，所以这个字段
    # 必须由调用方显式登记，不默认用 now()
    biz_occurred_at: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    confirm_status: str = "unconfirmed"
    raw_snapshot: str = ""


class HandoverCreateRequest(BaseModel):
    latest_version: int
    unconfirmed_items: List[str] = Field(default_factory=list)
    deadline: Optional[datetime] = None
    receiver: str = Field(..., min_length=1, description="接收人，必填")


# ---------------- 路由 ----------------
@router.post("", summary="新建简报（草稿）")
async def create_brief(
    payload: BriefCreateRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    brief = await brief_service.create_brief(
        db,
        title=payload.title,
        created_by=current_user.id,
        audience=payload.audience,
        channel=payload.channel,
        single_message=payload.single_message,
        deliverables=payload.deliverables,
        budget=payload.budget,
        deadline=payload.deadline,
        actor_name=current_user.full_name or current_user.username,
    )
    return {"id": brief.id, "brief_no": brief.brief_no, "status": brief.status}


@router.get("", summary="简报列表")
async def list_briefs(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    briefs = await brief_service.list_briefs(db, limit=limit)
    return [
        {
            "id": b.id,
            "brief_no": b.brief_no,
            "title": b.title,
            "status": b.status,
            "current_version": b.current_version,
            "created_at": b.created_at.isoformat(),
        }
        for b in briefs
    ]


@router.get("/{brief_id}", summary="简报详情")
async def get_brief(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")
    return {
        "id": brief.id,
        "brief_no": brief.brief_no,
        "title": brief.title,
        "audience": brief.audience,
        "channel": brief.channel,
        "single_message": brief.single_message,
        "deliverables": brief.get_deliverables(),
        "budget": brief.budget,
        "deadline": brief.deadline.isoformat() if brief.deadline else None,
        "status": brief.status,
        "current_version": brief.current_version,
    }


@router.post("/{brief_id}/check", summary="校验并生成缺项清单（M2+M3）")
async def check_brief(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """执行一次完整校验，返回一页式缺项清单与 30 项约束进度。"""
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    return await brief_service.check_and_build_gap_list(
        db, brief, actor_id=current_user.id,
        actor_name=current_user.full_name or current_user.username,
    )


@router.get("/{brief_id}/gaps", summary="最新一版缺项清单")
async def latest_gaps(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    items = await brief_service.get_latest_gap_list(db, brief_id)
    return [
        {
            "gap": i.gap_desc,
            "source": i.source_location,
            "severity": i.severity,
            "list_version": i.list_version,
        }
        for i in items
    ]


@router.get("/{brief_id}/constraints", summary="30 项品牌约束")
async def list_constraints(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    """列出 30 项约束及其确认状态（验收看板的数据源）。"""
    from sqlalchemy import select

    from app.models.brief import BriefConstraint

    result = await db.execute(
        select(BriefConstraint)
        .where(BriefConstraint.brief_id == brief_id)
        .order_by(BriefConstraint.seq)
    )
    rows = list(result.scalars().all())
    return {
        "total": len(rows),
        "confirmed": sum(1 for r in rows if r.status == "confirmed"),
        "unconfirmed": sum(1 for r in rows if r.status == "unconfirmed"),
        "missing": sum(1 for r in rows if r.status == "missing"),
        "items": [
            {
                "seq": r.seq,
                "content": r.content,
                "source_location": r.source_location,
                "status": r.status,
                "confirmed_by": r.confirmed_by,
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
            }
            for r in rows
        ],
    }


@router.post("/{brief_id}/versions", summary="新增版本（含过期来源拦截）")
async def add_version(
    brief_id: int,
    payload: VersionCreateRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """新增版本。若来源过期或缺少业务发生时间，返回 409 且不写入。"""
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    result = await brief_service.add_version(
        db,
        brief,
        biz_occurred_at=payload.biz_occurred_at,
        valid_until=payload.valid_until,
        confirm_status=payload.confirm_status,
        raw_snapshot=payload.raw_snapshot,
        created_by=current_user.id,
        actor_name=current_user.full_name or current_user.username,
    )
    if result.get("blocked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": result["reason_code"],
                "message": result["message"],
                "queue_id": result.get("queue_id"),
            },
        )
    return {"version_no": result["version_no"], "brief_id": brief_id}


@router.get("/{brief_id}/effective-version", summary="当前有效版本（双条件判定）")
async def effective_version(
    brief_id: int,
    db: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
):
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    result = await brief_service.resolve_effective_version(db, brief)
    if not result.get("ok"):
        # 409：必须让调用方看见"确认不了"这件事，不能当正常结果处理
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result)
    return result


@router.post("/{brief_id}/handovers", summary="登记交接（f23）")
async def create_handover(
    brief_id: int,
    payload: HandoverCreateRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    brief = await brief_service.get_brief(db, brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简报不存在")

    handover = await brief_service.create_handover(
        db,
        brief,
        latest_version=payload.latest_version,
        unconfirmed_items=payload.unconfirmed_items,
        deadline=payload.deadline,
        receiver=payload.receiver,
        handed_by=current_user.id,
        actor_name=current_user.full_name or current_user.username,
    )
    return {
        "id": handover.id,
        "latest_version": handover.latest_version,
        "unconfirmed_items": handover.get_unconfirmed_items(),
        "receiver": handover.receiver,
    }
