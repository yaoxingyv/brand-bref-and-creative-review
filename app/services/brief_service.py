# -*- coding: utf-8 -*-
"""
服务层：品牌创意简报主流程
================================================================
对应模块：M2 简报结构化 / M3 缺项清单 / M4 版本交接 / M6 过期覆盖拦截

这里体现项目方案里最重要的一条设计决定：

    **凡是"必须判对"的判断，一律不经过大模型。**

版本是不是有效、来源是不是过期、字段缺不缺——这些全部由
app/agents/rules.py 的纯函数给出确定答案。大模型只在
`USE_LLM_ASSIST=true` 时参与"把口语整理成结构化建议"这类软活，
且产出的东西只能进 suggested_* 字段（f14）。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.rules import (
    GapFinding,
    build_gap_list,
    check_expired_overwrite,
    judge_current_version,
    validate_brief_fields,
)
from app.models.approval import ManualQueue
from app.models.brief import Brief, BriefConstraint, BriefVersion, GapItem, Handover
from app.models.user import User
from app.services import audit_service


# =================================================================
# 创建与查询
# =================================================================
async def _next_brief_no(db: AsyncSession) -> str:
    """生成统一关联编号（f30 / f31）。

    编号规则：BR-批次-四位序号。
    为什么简报要有自己的编号：访谈里三类工具（内部服务台/业务台账/文件库）
    各存各的，跨工具只能人工比对（f30）。一个系统内统一的编号是
    把它们缝起来的唯一钥匙，也是 M5 只读集成的前置条件。
    """
    result = await db.execute(select(Brief.id).order_by(Brief.id.desc()).limit(1))
    last = result.scalar_one_or_none() or 0
    return f"BR-PILOT01-{last + 1:04d}"


async def create_brief(
    db: AsyncSession,
    *,
    title: str,
    created_by: Optional[int] = None,
    audience: Optional[str] = None,
    channel: Optional[str] = None,
    single_message: Optional[str] = None,
    deliverables: Optional[List[str]] = None,
    budget: Optional[str] = None,
    deadline: Optional[datetime] = None,
    actor_name: str = "",
) -> Brief:
    """新建简报（草稿态），并预生成 30 条约束条目占位。

    为什么一创建就铺 30 条空约束：
        验收口径是"30 项品牌约束全部追溯到客户确认"（f02 / f42）。
        如果等人逐条添加，最后一定凑不齐，也无法统计"还差几项"。
        先铺好 30 个位子，状态全部是 missing，
        界面上就能一直显示"已确认 12 / 30"，这是验收要的那块看板。
    """
    import json

    brief = Brief(
        brief_no=await _next_brief_no(db),
        title=title,
        audience=audience,
        channel=channel,
        single_message=single_message,
        deliverables=json.dumps(deliverables or [], ensure_ascii=False),
        budget=budget,
        deadline=deadline,
        status="draft",
        created_by=created_by,
    )
    db.add(brief)
    await db.flush()  # 先拿到 brief.id 再建约束

    from app.core.config import settings

    for seq in range(1, settings.CONSTRAINT_TARGET_COUNT + 1):
        db.add(
            BriefConstraint(
                brief_id=brief.id,
                seq=seq,
                content="",
                source_location="",
                status="missing",
            )
        )

    await audit_service.record(
        db,
        action="create",
        actor_id=created_by,
        actor_name=actor_name,
        target_type="brief",
        target_id=brief.id,
        brief_id=brief.id,
        version_no=1,
        detail={"title": title, "brief_no": brief.brief_no},
    )
    await db.commit()
    await db.refresh(brief)
    return brief


async def get_brief(db: AsyncSession, brief_id: int) -> Optional[Brief]:
    return await db.get(Brief, brief_id)


async def list_briefs(db: AsyncSession, limit: int = 50) -> List[Brief]:
    result = await db.execute(select(Brief).order_by(Brief.id.desc()).limit(limit))
    return list(result.scalars().all())


# =================================================================
# M2 + M3：结构化校验 与 缺项清单（REQ-1 / REQ-3）
# =================================================================
async def check_and_build_gap_list(
    db: AsyncSession,
    brief: Brief,
    *,
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> Dict[str, Any]:
    """执行一次完整校验，并把结果落成缺项清单（M3）。

    返回结构里同时给出：
      · field_gaps    关键字段缺项（M2）
      · constraint_gaps 约束条目缺项（M3）
      · progress      30 项约束的确认进度（验收看板）
      · can_submit    是否允许提交（缺 high 级字段则 False）

    can_submit=False 时**不允许**继续走确认流程 —— 行业调研把
    "缺关键字段不让提交"列为第一个产品缺口，这是硬闸门不是提醒。
    """
    field_result = validate_brief_fields(brief)

    c_result = await db.execute(
        select(BriefConstraint).where(BriefConstraint.brief_id == brief.id).order_by(BriefConstraint.seq)
    )
    constraints = list(c_result.scalars().all())
    constraint_result = build_gap_list(constraints)

    # 把本次发现写进缺项清单表（版本化：同一简报多次生成会累加版本号）
    v_result = await db.execute(
        select(GapItem.list_version)
        .where(GapItem.brief_id == brief.id)
        .order_by(GapItem.list_version.desc())
        .limit(1)
    )
    next_version = (v_result.scalar_one_or_none() or 0) + 1

    for f in field_result.findings + constraint_result.findings:
        db.add(
            GapItem(
                brief_id=brief.id,
                list_version=next_version,
                gap_desc=f.gap_desc,
                source_location=f.source_location,
                severity=f.severity,
            )
        )

    await audit_service.record(
        db,
        action="query",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="brief",
        target_id=brief.id,
        brief_id=brief.id,
        detail={
            "check": "gap_list",
            "field_gaps": len(field_result.findings),
            "constraint_gaps": len(constraint_result.findings),
            "list_version": next_version,
        },
    )
    await db.commit()

    return {
        "brief_id": brief.id,
        "brief_no": brief.brief_no,
        "can_submit": field_result.passed,
        "field_gaps": [
            {"field": f.field_key, "gap": f.gap_desc, "source": f.source_location, "severity": f.severity}
            for f in field_result.findings
        ],
        "constraint_gaps": [
            {"field": f.field_key, "gap": f.gap_desc, "source": f.source_location, "severity": f.severity}
            for f in constraint_result.findings
        ],
        "progress": constraint_result.data,
        "list_version": next_version,
    }


async def get_latest_gap_list(db: AsyncSession, brief_id: int) -> List[GapItem]:
    """取最新一版缺项清单（一页纸用）。"""
    v_result = await db.execute(
        select(GapItem.list_version)
        .where(GapItem.brief_id == brief_id)
        .order_by(GapItem.list_version.desc())
        .limit(1)
    )
    version = v_result.scalar_one_or_none()
    if not version:
        return []
    result = await db.execute(
        select(GapItem)
        .where(GapItem.brief_id == brief_id, GapItem.list_version == version)
        .order_by(GapItem.id)
    )
    return list(result.scalars().all())


# =================================================================
# M4：版本与交接（REQ-2 / f23 f24 f34）
# =================================================================
async def add_version(
    db: AsyncSession,
    brief: Brief,
    *,
    biz_occurred_at: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    confirm_status: str = "unconfirmed",
    raw_snapshot: str = "",
    created_by: Optional[int] = None,
    actor_name: str = "",
) -> Dict[str, Any]:
    """新增一个版本，并做过期来源拦截（M6）。

    流程：
      1. 先算当前有效版本（双条件）
      2. 用双时间戳比对新来源是不是过期
      3. 过期 → 不写入正式版本，转人工队列，返回 blocked
      4. 不过期 → 写入新版本

    注意第 3 步：**拦截时不静默丢弃**。过期来源的内容会被记进
    人工队列的 detail 里，人可以去看。f35 要求"读取失败保留快照并标记时间，
    提示人工核查，不编造最新状态"——静默丢弃就是编造状态。
    """
    v_result = await db.execute(
        select(BriefVersion).where(BriefVersion.brief_id == brief.id)
    )
    versions = list(v_result.scalars().all())

    judge = judge_current_version(versions)
    current_effective = None
    if judge.passed:
        for v in versions:
            if v.id == judge.data.get("version_id"):
                current_effective = v
                break

    check = check_expired_overwrite(
        {"biz_occurred_at": biz_occurred_at}, current_effective
    )

    if not check.passed:
        # 过期或时间缺失：不写版本，进人工队列
        queue_item = ManualQueue(
            brief_id=brief.id,
            reason_code=check.reason_code,
            reason_detail=check.message,
            assignee="",  # 由流程协调员认领，见 approval_service.assign_queue
            status="pending",
        )
        db.add(queue_item)
        await audit_service.record(
            db,
            action="reject",
            actor_id=created_by,
            actor_name=actor_name,
            target_type="version",
            brief_id=brief.id,
            detail={"reason": check.reason_code, "message": check.message, "snapshot": raw_snapshot[:500]},
            rule_code="R6" if check.reason_code == "EXPIRED_SOURCE_BLOCKED" else "R7",
        )
        await db.commit()
        return {
            "blocked": True,
            "reason_code": check.reason_code,
            "message": check.message,
            "queue_id": queue_item.id,
        }

    next_no = (max((v.version_no for v in versions), default=0)) + 1
    version = BriefVersion(
        brief_id=brief.id,
        version_no=next_no,
        biz_occurred_at=biz_occurred_at,
        sys_received_at=datetime.utcnow(),
        valid_until=valid_until,
        confirm_status=confirm_status,
        raw_snapshot=raw_snapshot,
        created_by=created_by,
    )
    db.add(version)
    brief.current_version = next_no

    await audit_service.record(
        db,
        action="create",
        actor_id=created_by,
        actor_name=actor_name,
        target_type="version",
        brief_id=brief.id,
        version_no=next_no,
        detail={"biz_occurred_at": str(biz_occurred_at), "confirm_status": confirm_status},
    )
    await db.commit()
    await db.refresh(version)
    return {"blocked": False, "version": version, "version_no": next_no}


async def create_handover(
    db: AsyncSession,
    brief: Brief,
    *,
    latest_version: int,
    unconfirmed_items: List[str],
    deadline: Optional[datetime],
    receiver: str,
    handed_by: Optional[int] = None,
    actor_name: str = "",
) -> Handover:
    """登记一次交接（f20 / f23）。

    f23 要求交接必须列明：最新版本、未确认事项、截止时间。
    这三项在 API 层做了必填校验（见 schemas），这里不再重复校验，
    只负责落库与留痕。

    为什么未确认事项要单独存成数组：
        唐映秋说"不能只发一条'已处理'的消息"。交接卡的价值就在于
        "还有什么没定"，空数组意味着"全部已确认"，这本身就是信息。
    """
    handover = Handover(
        brief_id=brief.id,
        latest_version=latest_version,
        deadline=deadline,
        receiver=receiver,
        handed_by=handed_by,
    )
    handover.set_unconfirmed_items(unconfirmed_items)
    db.add(handover)

    await audit_service.record(
        db,
        action="create",
        actor_id=handed_by,
        actor_name=actor_name,
        target_type="handover",
        brief_id=brief.id,
        version_no=latest_version,
        detail={"receiver": receiver, "unconfirmed_count": len(unconfirmed_items)},
    )
    await db.commit()
    await db.refresh(handover)
    return handover


async def resolve_effective_version(
    db: AsyncSession, brief: Brief
) -> Dict[str, Any]:
    """对外暴露的"当前有效版本是谁"。

    若判定失败（没有同时满足双条件的版本），**不返回兜底版本**，
    而是把 f44 的后果显式带回：需要人工确认 + 队列是否非空。
    调用方（API）据此返回 409 而不是 200，前端就不能假装没事继续走。
    """
    result = await db.execute(
        select(BriefVersion).where(BriefVersion.brief_id == brief.id)
    )
    versions = list(result.scalars().all())
    judge = judge_current_version(versions)

    if judge.passed:
        return {"ok": True, **judge.data}

    q_result = await db.execute(
        select(ManualQueue).where(
            ManualQueue.brief_id == brief.id, ManualQueue.status == "pending"
        )
    )
    pending = list(q_result.scalars().all())

    return {
        "ok": False,
        "reason_code": judge.reason_code,
        "message": judge.message,
        "pending_queue_count": len(pending),
        "can_pause_pilot": len(pending) > 0,   # f44 傅景的停止试点权
    }
