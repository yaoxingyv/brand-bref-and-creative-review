# -*- coding: utf-8 -*-
"""
核心层：全局配置 + 试点口径常量
================================================================
设计要点：
  · 所有"可替换"的外部依赖（LLM 供应商 / 嵌入模型 / 数据库）集中在此，
    业务代码只依赖 Settings，不直接读环境变量。
  · 默认 LLM_PROVIDER=mock、EMBEDDING_PROVIDER=local，零外部依赖即可跑通。
  · **业务红线不进配置**：七条硬约束写在 HARD_RULES 常量里，
    不提供任何环境变量开关 —— 它们来自访谈 f05/f14/f22/f32/f41/f44/f52，
    一旦可配置就可能被"顺手关掉"，那就失去了拦截意义。
"""

from functools import lru_cache
from typing import Dict, List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。环境变量优先级高于 .env 文件。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------- 应用基础 ----------------
    APP_NAME: str = "品牌创意简报与评审系统"
    APP_ENV: str = "dev"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # ---------------- 安全 ----------------
    # 生产环境必须通过环境变量覆盖，禁止使用默认密钥（长度需 ≥32 字节）
    SECRET_KEY: str = "dev-only-secret-change-me-to-32bytes-min"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12  # 12 小时

    # ---------------- 数据库 ----------------
    # 开发：sqlite+aiosqlite；生产：postgresql+asyncpg
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    # ---------------- LLM 供应商（可替换）----------------
    # mock         : 离线假模型，仅用于跑通链路与测试
    # openai_compat: OpenAI 兼容协议（DeepSeek / 通义 / GLM 仅 base_url 不同）
    LLM_PROVIDER: Literal["mock", "openai_compat"] = "mock"
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.3
    LLM_TIMEOUT: int = 60

    # ---------------- 嵌入模型（可替换）----------------
    # local: 本地哈希向量（离线占位）  api: 调用远程嵌入接口
    EMBEDDING_PROVIDER: Literal["local", "api"] = "local"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 256  # 仅 local 模式生效

    # ---------------- 检索参数（知识库 / 品牌规范检索）----------------
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    TOP_K: int = 3
    W_BM25: float = 0.4
    W_VEC: float = 0.6

    # ---------------- 试点口径（来自访谈报告的书面约定）----------------
    # f02/f42 验收口径：30 项品牌约束全部追溯到客户确认
    CONSTRAINT_TARGET_COUNT: int = 30
    # f03 试点边界：四周试点、两名业务骨干、每周不超过四小时
    PILOT_WEEKS: int = 4

    # ---------------- 问答 / 预检模式 ----------------
    # False = 纯规则引擎预检（离线可跑，结论可解释）
    # True  = 规则引擎 + 大模型补位（需配真实 API Key）
    USE_LLM_ASSIST: bool = False
    ASSISTANT_NAME: str = "简报预检助手"

    # ---------------- 文件与向量索引落盘 ----------------
    UPLOAD_DIR: str = "./data/uploads"
    VECTOR_DIR: str = "./data/vectordb"

    # ---------------- 种子账号（仅 dev 用，对应访谈六位负责人）----------------
    SEED_ADMIN: str = "admin"
    SEED_ADMIN_PASSWORD: str = "admin123"


@lru_cache
def get_settings() -> Settings:
    """配置单例。FastAPI 依赖注入处统一调用它。"""
    return Settings()


settings = get_settings()


# =================================================================
# 七条硬约束（业务红线 · 不可配置 · 违反即拒绝而非提示）
# -----------------------------------------------------------------
# 来源：与各个负责人对话形成的报告
#   f05 不自动发布广告，不推断或承诺广告法律审查结果
#   f14 系统建议不能覆盖原值或省略确认
#   f22 一线专员可提交变更，正式确认由业务负责人完成，模型没有审批权限
#   f32 试点只开放脱敏只读导出；写回、外部消息和执行接口均不开放
#   f41 所有正式结论须由授权人员确认
#   f44 无法确认版本或关键字段时进入人工队列，必要时暂停试点，不能默认通过
#   f52 不推断或承诺广告法律审查结果；对外承诺仍由业务人员确认
# =================================================================
class HardRules:
    """七条硬约束的声明式定义。

    每一条都绑定了：事实依据、实现方式、越界时的拦截动作。
    业务代码在写入结论字段、确认动作、导出动作前必须调用对应校验，
    校验失败抛 HardRuleViolation，由入口层转成 403/422 并**留痕**。
    """

    RULES: List[Dict[str, str]] = [
        {
            "code": "R1_MODEL_NO_APPROVAL",
            "name": "模型没有审批权限",
            "basis": "f22",
            "impl": "提交与确认分离，模型只能产出建议",
            "block": "任何结论字段的写入必须带授权人 ID",
        },
        {
            "code": "R2_CONFIRM_BY_AUTHORIZED",
            "name": "正式结论须由授权人员确认",
            "basis": "f41",
            "impl": "确认动作绑定授权确认人名单",
            "block": "名单外账号的确认请求直接拒绝",
        },
        {
            "code": "R3_SUGGESTION_NOT_OVERWRITE",
            "name": "建议不能覆盖原值或省略确认",
            "basis": "f14",
            "impl": "建议与原值双写，各自留痕",
            "block": "覆盖类操作阻断并留痕",
        },
        {
            "code": "R4_READONLY_EXPORT",
            "name": "只开放脱敏只读导出",
            "basis": "f32",
            "impl": "连接器只实现读与脱敏",
            "block": "写回 / 外部消息 / 执行接口不实现",
        },
        {
            "code": "R5_NO_AUTO_PUBLISH",
            "name": "不自动发布广告",
            "basis": "f05",
            "impl": "不提供任何发布通路",
            "block": "发布类调用不存在，无法触发",
        },
        {
            "code": "R6_NO_LEGAL_INFERENCE",
            "name": "不推断或承诺法律审查结果",
            "basis": "f05 / f52",
            "impl": "法律相关字段标注为人工判定",
            "block": "模型不得输出确定性法律结论",
        },
        {
            "code": "R7_NO_SILENT_PASS",
            "name": "无法确认版本或关键字段时不得默认通过",
            "basis": "f44",
            "impl": "置信不足即入人工处理队列",
            "block": "队列非空时试点可暂停",
        },
    ]

    # 法律 / 合规相关的敏感词：命中即标记为"人工判定"，模型不得给确定结论
    LEGAL_KEYWORDS: List[str] = [
        "广告法",
        "法律审查",
        "合规审查",
        "侵权",
        "违法",
        "监管",
        "处罚",
        "商标",
        "专利",
        "肖像权",
    ]

    # 发布相关的动作词：任何形式都不提供通路
    PUBLISH_KEYWORDS: List[str] = [
        "发布",
        "投放",
        "上线广告",
        "推送给媒体",
        "自动投放",
    ]


class HardRuleViolation(Exception):
    """违反硬约束时抛出。

    与业务异常区分开：它必须在审计日志里留下一条 VIOLATION 记录，
    因为"谁试图越界"本身就是审计需要的证据（f45）。
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
