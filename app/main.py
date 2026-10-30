# -*- coding: utf-8 -*-
"""
应用入口：品牌创意简报与评审系统
================================================================
四层架构在这里装配：
    入口层 app/api      —— 收参数、验身份、把结论翻译成 HTTP 语义
    服务层 app/services —— 业务编排（不碰 LLM 细节）
    编排层 app/agents   —— 规则引擎（确定判断）+ 大模型（软活）
    数据层 app/models   —— 11 张表，字段对应访谈事实编号

启动：uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import admin, approval, auth, brief
from app.core.config import HardRuleViolation, settings
from app.core.database import init_db
from app.db.seed import seed_all
from app.core.database import get_session

import os

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表并灌种子数据。"""
    await init_db()
    async for session in get_session():
        result = await seed_all(session)
        if result.get("seeded"):
            print(f"[seed] 已写入种子数据：{result}")
        break
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "品牌需求简报与创意评审系统 · 试点版\n\n"
        "链路：客户访谈 → 需求确认 → 创意提案 → 品牌审核 → 交付\n"
        "红线：七条硬约束全程拦截，模型无审批权限"
    ),
    version="0.1.0-pilot",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=settings.UPLOAD_DIR), name="static")


# ---------------- 统一异常处理：硬约束违反 → 403 + 规则码 ----------------
@app.exception_handler(HardRuleViolation)
async def hard_rule_handler(request: Request, exc: HardRuleViolation):
    return JSONResponse(
        status_code=403,
        content={"code": exc.code, "message": exc.message},
    )


# ---------------- 业务路由 ----------------
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(brief.router, prefix=settings.API_PREFIX)
app.include_router(approval.router, prefix=settings.API_PREFIX)
app.include_router(admin.router, prefix=settings.API_PREFIX)


# ---------------- 页面 ----------------
@app.get("/health", tags=["系统"])
async def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "pilot_weeks": settings.PILOT_WEEKS,
        "constraint_target": settings.CONSTRAINT_TARGET_COUNT,
        "hard_rules": 7,
    }


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_name": settings.APP_NAME, "api_prefix": settings.API_PREFIX},
    )


@app.get("/")
async def workspace_page(request: Request):
    """业务工作台：简报 + 缺项清单 + 约束进度 + 交接。"""
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={"app_name": settings.APP_NAME, "api_prefix": settings.API_PREFIX},
    )


@app.get("/admin")
async def admin_page(request: Request):
    """管理后台：概览 / 审计 / 违规 / 素材 / 用户 / 队列 / 数据模型。"""
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"app_name": settings.APP_NAME, "api_prefix": settings.API_PREFIX},
    )
