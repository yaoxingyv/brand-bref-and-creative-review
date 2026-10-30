# -*- coding: utf-8 -*-
"""
入口层：认证路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", summary="登录")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_session)):
    """登录。账号密码错误统一返回 401，不区分用户是否存在（防账号枚举）。"""
    result = await auth_service.login(db, payload.username, payload.password)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    return result


@router.get("/me", summary="当前登录人")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
    }
