# -*- coding: utf-8 -*-
"""
服务层：认证与登录
================================================================
六角色登录（对应访谈六位负责人）。登录后签发 JWT，payload 带 role，
入口层据此做粗粒度拦截；**细粒度的"能不能确认"不走角色，走授权名单**
（见 approval_service.is_authorized）——因为角色是代码常量，
授权名单是待客户确认的业务决定（f41）。
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.services import audit_service


async def authenticate(
    db: AsyncSession, username: str, password: str
) -> Optional[User]:
    """校验用户名密码。失败返回 None（不区分"用户不存在"与"密码错"，防枚举）。"""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def login(db: AsyncSession, username: str, password: str) -> Optional[dict]:
    """登录成功返回 token 与用户摘要，并记一条审计。"""
    user = await authenticate(db, username, password)
    if user is None:
        # 登录失败也要留痕：审计要能看出反复尝试的行为
        await audit_service.record(
            db,
            action="reject",
            actor_name=username,
            target_type="auth",
            detail={"attempt": "login", "result": "failed"},
        )
        await db.commit()
        return None

    await audit_service.record(
        db,
        action="query",
        actor_id=user.id,
        actor_name=user.full_name or user.username,
        target_type="auth",
        detail={"attempt": "login", "result": "success", "role": user.role},
    )
    await db.commit()

    token = create_access_token(str(user.id), user.role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
        },
    }


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    return await db.get(User, user_id)


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    full_name: str = "",
    role: str = "designer",
) -> User:
    """创建用户（管理端用户管理模块用）。"""
    user = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
