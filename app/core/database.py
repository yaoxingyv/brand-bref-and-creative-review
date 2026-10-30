# -*- coding: utf-8 -*-
"""
核心层：异步数据库引擎与会话工厂（SQLAlchemy 2.0 async）
================================================================
  · 开发用 SQLite + aiosqlite，生产改 DATABASE_URL 成 postgresql+asyncpg
    即可，**业务代码零改动**。
  · get_session() 作为 FastAPI 依赖注入，每个请求一个会话，自动关闭。
  · init_db() 在应用启动时建表（Week 1 用；第 2 周起换成 Alembic 迁移）。
"""

from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite 路径模式下确保父目录存在，否则建库会失败。"""
    if url.startswith("sqlite"):
        # sqlite+aiosqlite:///./data/app.db → ./data/app.db
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    # SQLite 需要关闭同线程检查；Postgres 无副作用
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # commit 后仍可访问已加载属性，避免 async 下懒加载报错
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：为每个请求提供一个数据库会话。"""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """建表（首次启动）。生产环境请替换为 Alembic migration。"""
    # 导入所有模型，确保它们注册到 Base.metadata
    from app.models import (  # noqa: F401
        Approval,
        Asset,
        AuditLog,
        AuthorizedApprover,
        Brief,
        BriefConstraint,
        BriefVersion,
        GapItem,
        Handover,
        ManualQueue,
        User,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
