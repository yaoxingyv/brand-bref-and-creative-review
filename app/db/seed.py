# -*- coding: utf-8 -*-
"""
数据初始化：种子数据
================================================================
创建六位负责人账号（对应访谈报告里的真实角色）、授权确认人名单、
以及一份**刻意残缺**的示例简报。

为什么示例简报要残缺：
    这个系统的价值就是"把缺的东西列出来"。如果种子数据是一份完美的
    简报，打开页面什么都看不到，等于没演示。所以这里故意
    · 不填 single_message（行业调研指出的头号痛点：单一诉求模糊）
    · 30 条约束只确认前 12 条
    这样一进系统就能看到缺项清单和 12/30 的进度条。

所有数据均为**虚构演练样本**（访谈 f21 明确说明是合成样本），
不代表任何真实企业数据。
"""

from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.approval import ManualQueue
from app.models.audit import Asset
from app.models.brief import Brief, BriefConstraint, BriefVersion
from app.models.user import AuthorizedApprover, User

# 六位负责人：登录名 / 姓名 / 角色
# 角色直接取自访谈报告，不是抽象权限组
SEED_USERS: List[tuple] = [
    ("admin", "系统管理员", "admin"),
    ("guminglan", "顾明岚", "business_owner"),
    ("tangyingqiu", "唐映秋", "coordinator"),
    ("linyizhou", "林亦舟", "designer"),
    ("fujing", "傅景", "auditor"),
    ("qinrui", "秦芮", "engineer"),
    ("yeshu", "叶书", "client_rep"),
]
SEED_PASSWORD = "pilot2026"


async def seed_all(db: AsyncSession) -> dict:
    """写入种子数据。已存在则跳过，可重复执行。"""
    result = await db.execute(select(User.id).limit(1))
    if result.scalar_one_or_none() is not None:
        return {"seeded": False, "reason": "已有数据，跳过"}

    # ---- 1. 用户 ----
    users = {}
    for username, full_name, role in SEED_USERS:
        u = User(
            username=username,
            password_hash=hash_password(SEED_PASSWORD),
            full_name=full_name,
            role=role,
        )
        db.add(u)
        users[username] = u
    await db.flush()

    # ---- 2. 授权确认人名单（M1）----
    # f22：正式确认由业务负责人完成；f41：正式结论须由授权人员确认
    # 这里把顾明岚（业务决策）与傅景（审核质量）纳入，
    # 注意：**这仍是待客户书面确认的假设**，访谈明确说名单未核实。
    for name, scope in [("guminglan", "brief_confirm"), ("fujing", "final_approval")]:
        db.add(
            AuthorizedApprover(
                user_id=users[name].id,
                scope=scope,
                granted_by=users["admin"].id,
                note="试点初始名单（待业务负责人与审核负责人书面确认）",
            )
        )

    # ---- 3. 示例简报（刻意残缺）----
    import json

    brief = Brief(
        brief_no="BR-PILOT01-0001",
        title="虚构品牌 · 秋季新品上市传播创意简报",
        audience="25-35 岁都市女性",           # 已填
        channel="社交媒体 + 线下快闪店",        # 已填
        single_message=None,                   # 故意缺失：头号痛点
        deliverables=json.dumps(["主视觉 KV", "15 秒短视频"], ensure_ascii=False),
        budget="待定",
        deadline=datetime.utcnow() + timedelta(days=14),
        status="draft",
        created_by=users["guminglan"].id,
    )
    db.add(brief)
    await db.flush()

    # 30 条约束：前 12 条已确认（其中含一条未确认），其余缺失
    # 前 12 条内容取自访谈里提到的典型品牌约束
    confirmed_texts = [
        "主视觉须使用品牌标准色（潘通 2736C）",
        "不得使用未经授权的明星肖像",
        "所有文案须经品牌部书面确认后方可使用",
        "Logo 最小使用尺寸不小于 24px",
        "不得出现竞品名称或暗示性比较",
        "宣传语须与已注册商标一致",
        "视频时长严格控制在 15 秒以内",
        "字体仅可使用思源黑体系列",
        "价格信息须标注有效期",
        "不得使用绝对化用语（最、第一、国家级等）",
        "素材须保留可追溯来源记录",
        "快闪店物料须与设计稿一致",
    ]
    for seq in range(1, 31):
        if seq <= len(confirmed_texts):
            # 第 7 条留作"已录入但未确认"，用来演示两种状态的区别（f31）
            if seq == 7:
                status = "unconfirmed"
            else:
                status = "confirmed"
            db.add(
                BriefConstraint(
                    brief_id=brief.id,
                    seq=seq,
                    content=confirmed_texts[seq - 1],
                    source_location=f"客户访谈记录 2026-10-18 · 第{seq}条",
                    status=status,
                    confirmed_by=users["yeshu"].id if status == "confirmed" else None,
                    confirmed_at=datetime.utcnow() if status == "confirmed" else None,
                )
            )
        else:
            db.add(
                BriefConstraint(
                    brief_id=brief.id, seq=seq, content="", source_location="", status="missing"
                )
            )

    # ---- 3.5 示例版本 ----
    # 必须造一个"已确认且有效"的当前版本，否则过期来源拦截（M6）
    # 没有对照物，系统行为看起来像"不拦截"——那会误导验收。
    # v1：三天前发生、一个月内有效、已确认 → 这就是"当前有效版本"
    # v2：昨天发生、未确认 → 用来演示"有更新的版本但不能上位"
    db.add(
        BriefVersion(
            brief_id=brief.id,
            version_no=1,
            biz_occurred_at=datetime.utcnow() - timedelta(days=3),
            sys_received_at=datetime.utcnow() - timedelta(days=3),
            valid_until=datetime.utcnow() + timedelta(days=30),
            confirm_status="confirmed",
            raw_snapshot="客户访谈后整理的第一版品牌约束（演练样本）",
            created_by=users["guminglan"].id,
        )
    )
    db.add(
        BriefVersion(
            brief_id=brief.id,
            version_no=2,
            biz_occurred_at=datetime.utcnow() - timedelta(days=1),
            sys_received_at=datetime.utcnow() - timedelta(hours=6),
            valid_until=datetime.utcnow() + timedelta(days=30),
            confirm_status="unconfirmed",
            raw_snapshot="客户群消息补充的约束，尚未确认（f13 场景）",
            created_by=users["tangyingqiu"].id,
        )
    )
    brief.current_version = 2
    await db.flush()

    # ---- 4. 一条人工队列样例（演示 M7）----
    db.add(
        ManualQueue(
            brief_id=brief.id,
            reason_code="BIZ_TIME_MISSING",
            reason_detail="客户口头补充的约束通过群消息传来，缺少业务发生时间，无法判断是否过期",
            assignee="唐映秋",
            status="pending",
        )
    )

    # ---- 5. 素材样例（演示 M9 授权标记）----
    db.add(
        Asset(
            brief_id=brief.id,
            name="秋季新品宣传语 · 旧版「焕新一季」",
            source="客户 2026-09 提供",
            authorized=True,
            deprecated=True,     # f11：客户已停用，但演示稿仍引用
            used_in=json.dumps(["主视觉 KV 第 2 版", "短视频脚本 v3"], ensure_ascii=False),
            note="客户 2026-10-15 停用，引用处需同步替换",
        )
    )
    db.add(
        Asset(
            brief_id=brief.id,
            name="品牌标准色卡 2736C",
            source="品牌部官方素材库",
            authorized=True,
            deprecated=False,
            used_in=json.dumps(["主视觉 KV 第 3 版"], ensure_ascii=False),
        )
    )
    db.add(
        Asset(
            brief_id=brief.id,
            name="明星肖像照片（未授权）",
            source="网络下载（待核实）",
            authorized=False,    # f02：未授权素材明确标记
            deprecated=False,
            used_in=json.dumps([]),
            note="未获授权，禁止用于任何对外物料",
        )
    )

    await db.commit()
    return {
        "seeded": True,
        "users": len(SEED_USERS),
        "approvers": 2,
        "brief_no": brief.brief_no,
        "constraints": 30,
    }
