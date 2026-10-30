# -*- coding: utf-8 -*-
"""
入口层：依赖注入（鉴权与角色拦截）
================================================================
两道拦截，职责分明：
    1. get_current_user —— 你是不是本系统的合法用户（401）
    2. require_roles    —— 你的角色能不能碰这个接口（403）

**注意**：角色拦截只做粗粒度分流。真正的"能不能确认简报"
不在这一层判断，而在 approval_service.is_authorized 里查授权名单。
因为角色写死在代码里，授权名单是客户还没确认的业务决定（f41）。
"""

from typing import Callable, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import decode_token
from app.models.user import User

# auto_error=False：未带令牌时也交给我们自己处理，好返回中文错误码
bearer_scheme = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = "未登录或登录已过期，请重新登录"
PERMISSION_ERROR = "当前角色无权访问该接口"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """解析 JWT 并取出用户。任何失败统一 401 + 中文提示。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=CREDENTIALS_ERROR)

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=CREDENTIALS_ERROR)

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=CREDENTIALS_ERROR)

    user = await db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=CREDENTIALS_ERROR)

    return user


def require_roles(*allowed: str) -> Callable:
    """按角色拦截。管理员（admin）默认放行所有接口。"""

    async def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == "admin" or current_user.role in allowed:
            return current_user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PERMISSION_ERROR)

    return _dep


# 常用组合：只有业务负责人与审核负责人能进的"决策类"接口
require_decision_maker = require_roles("business_owner", "auditor")
# 管理后台：管理员、流程协调员、审核负责人可看
require_backoffice = require_roles("coordinator", "auditor", "engineer")
