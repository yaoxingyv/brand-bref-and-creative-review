# -*- coding: utf-8 -*-
"""
智能体编排层：确定性规则引擎（系统的"硬骨头"）
================================================================
为什么必须有这一层，而不是全交给大模型：

    项目方案 4.3 节定了"规则引擎与大模型分层"，原因有三：
    1. 审核侧要的是**可解释**结论（f45 谁查过、用哪个版本、谁确认），
       大模型的黑箱结论傅景不会签字；
    2. 硬约束是"越界直接拒绝"，不是"提示一下"，
       靠提示词约束模型 = 把红线交给概率，这是 f44 明确禁止的；
    3. 字段校验、关键词匹配、版本比对这些事规则引擎零成本且 100% 稳定，
       用大模型是浪费钱还引入不确定性。

所以本模块承担所有"必须判对"的判断；大模型只做规则做不了的事
（比如把一段口语化的客户访谈整理成结构化约束建议）。

本模块全部是**纯函数**，不碰数据库、不发网络请求 —— 因此可以单测覆盖到 100%，
这也是它敢承担红线判断的前提。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.core.config import HardRules, settings


# =================================================================
# 数据结构
# =================================================================
@dataclass
class GapFinding:
    """一条缺项发现。

    刻意保持极简：缺什么 + 去哪儿找 + 多要紧。
    林亦舟 f15 原话要的就是"一页缺项清单和对应来源位置"，
    加摘要、加分析都是画蛇添足，反而违背需求。
    """

    field_key: str
    gap_desc: str
    source_location: str
    severity: str = "medium"          # high | medium | low


@dataclass
class RuleResult:
    """规则引擎的统一返回。

    passed=False 时**必须**有 reason_code，调用方据此决定是拒绝
    还是入人工队列 —— 不允许出现"没过但不知道为什么"。
    """

    passed: bool
    reason_code: str = ""
    message: str = ""
    findings: List[GapFinding] = field(default_factory=list)
    data: Dict = field(default_factory=dict)


# =================================================================
# M2 简报结构化校验 → 生成缺项清单（REQ-1 / REQ-3 / f15 f31）
# =================================================================
# 关键字段及其"去哪儿找"的指引。
# 来源位置写成可执行的指引而不是字段名，是因为缺项清单的使用者是设计师，
# 他要的是"我该去翻哪份材料"，不是"系统里哪个字段空了"。
KEY_FIELD_GUIDE: List[Tuple[str, str, str, str]] = [
    # (字段键, 中文名, 来源位置指引, 严重程度)
    ("audience", "受众", "客户访谈记录 · 需求确认会纪要", "high"),
    ("channel", "传播渠道", "客户访谈记录 · 媒介排期表", "high"),
    ("single_message", "单一诉求", "客户访谈记录 · 需向客户口头确认后补录", "high"),
    ("deliverables", "交付物清单", "需求确认会纪要 · 报价单附件", "high"),
    ("deadline", "截止时间", "项目排期表 · 需流程协调员登记", "medium"),
    ("budget", "预算", "需求确认会纪要 · 业务负责人确认", "medium"),
]


def validate_brief_fields(brief) -> RuleResult:
    """校验简报关键字段，产出缺项清单。

    设计要点（f31）：**缺失值与未确认值必须区分**
        · 字段为空        → missing，要补录
        · 字段有值未确认  → unconfirmed，要催确认
    两者在清单里是不同动作，混为一谈会让秦芮的数据质量检查（f33）失效。

    返回 passed 表示"是否可以进入下一环节"。缺了 high 级字段一律不放行——
    行业调研把"简报结构化校验，缺字段不让提交"列为第一个产品缺口。
    """
    findings: List[GapFinding] = []

    for key, cn_name, location, severity in KEY_FIELD_GUIDE:
        value = getattr(brief, key, None)
        # 交付物是 JSON 数组字符串，空数组同样算缺
        if key == "deliverables":
            is_missing = not brief.get_deliverables()
        else:
            is_missing = value is None or (isinstance(value, str) and not value.strip())

        if is_missing:
            findings.append(
                GapFinding(
                    field_key=key,
                    gap_desc=f"缺少{cn_name}",
                    source_location=location,
                    severity=severity,
                )
            )

    # 单一诉求缺失要单独提示：行业调研指出这是"反复微调"的头号原因
    has_high = any(f.severity == "high" for f in findings)
    if not has_high:
        return RuleResult(passed=True, findings=findings, data={"gap_count": len(findings)})

    return RuleResult(
        passed=False,
        reason_code="KEY_FIELD_MISSING",
        message=f"存在 {len(findings)} 项关键字段缺失，不允许提交",
        findings=findings,
        data={"gap_count": len(findings)},
    )


def build_gap_list(constraints: List) -> RuleResult:
    """从约束条目生成缺项清单（REQ-1 的主要交付物）。

    只挑出 status != confirmed 的条目。已确认的不需要出现在清单上——
    清单是"待办"，不是"总览"。
    """
    findings: List[GapFinding] = []

    for c in constraints:
        if c.status == "confirmed":
            continue

        if c.status == "missing":
            desc = f"第{c.seq}项约束内容缺失"
            severity = "high"
        else:  # unconfirmed
            desc = f"第{c.seq}项约束尚未确认"
            severity = "medium"

        findings.append(
            GapFinding(
                field_key=f"constraint_{c.seq}",
                gap_desc=desc,
                source_location=c.source_location or "来源位置未登记，需补",
                severity=severity,
            )
        )

    # 按严重程度排序：一页纸上先看到最要命的
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 9))

    unconfirmed_total = sum(1 for c in constraints if c.status != "confirmed")
    return RuleResult(
        passed=unconfirmed_total == 0,
        reason_code="" if unconfirmed_total == 0 else "CONSTRAINT_UNCONFIRMED",
        message="" if unconfirmed_total == 0 else f"{unconfirmed_total} 项约束未确认",
        findings=findings,
        data={
            "total": len(constraints),
            "confirmed": sum(1 for c in constraints if c.status == "confirmed"),
            "unconfirmed": unconfirmed_total,
            "target": settings.CONSTRAINT_TARGET_COUNT,
        },
    )


# =================================================================
# M4 版本判定（REQ-2 / f24）：双条件，不看文件名
# =================================================================
def judge_current_version(versions: List, now: Optional[datetime] = None) -> RuleResult:
    """用"版本有效期 + 确认状态"两个条件判定当前有效版本。

    f24 原文："采用版本有效期和确认状态两个条件，作废记录保留原因，不靠文件名猜测最新版本。"

    判定逻辑（AND，不是 OR）：
        · 在有效期内
        · 且 已确认
    两个都满足才算当前版本。

    若找不到满足条件的版本：
        返回 passed=False，reason_code=VERSION_UNCONFIRMED，
        调用方应当把它送进人工队列（f44），**不能回退到"上一个版本"了事** ——
        因为上一个版本可能同样没有确认状态，自动回退就是"默认通过"。
    """
    now = now or datetime.utcnow()

    effective = [v for v in versions if v.is_effective(now)]
    if effective:
        # 多个有效时取版本号最大的（同批次里这是唯一合理的排序键）
        best = max(effective, key=lambda v: v.version_no)
        return RuleResult(
            passed=True,
            data={
                "version_id": best.id,
                "version_no": best.version_no,
                "confirm_status": best.confirm_status,
            },
        )

    latest = max(versions, key=lambda v: v.version_no) if versions else None
    return RuleResult(
        passed=False,
        reason_code="VERSION_UNCONFIRMED",
        message="没有同时满足「有效期内 + 已确认」的版本，需人工确认",
        data={
            "latest_version_no": latest.version_no if latest else 0,
            "latest_confirm_status": latest.confirm_status if latest else "none",
        },
    )


# =================================================================
# M6 过期来源覆盖拦截（REQ-4 / f34 f35）
# =================================================================
def check_expired_overwrite(incoming, current_effective) -> RuleResult:
    """判断一次"来自外部来源的更新"能不能覆盖当前已确认版本。

    f34 原文："更新时同时记录业务发生时间和系统接收时间；过期来源不能覆盖已确认的新版本。"

    核心规则：**新数据的业务发生时间若早于当前有效版本的业务发生时间，
    一律不覆盖**。这解决的就是 f13 那个真实问题——现场先用群消息处理、
    事后补表，补进来的数据"系统接收时间"很新但"业务发生时间"很旧，
    如果按接收时间排序，旧结论就会把新结论冲掉。
    """
    if current_effective is None:
        return RuleResult(passed=True, message="当前无有效版本，可直接写入")

    in_biz = incoming.get("biz_occurred_at")
    cur_biz = current_effective.biz_occurred_at

    # 业务发生时间缺失 → 无法比较 → 按 f44 不能默认通过，进人工队列
    if in_biz is None:
        return RuleResult(
            passed=False,
            reason_code="BIZ_TIME_MISSING",
            message="缺少业务发生时间，无法判断是否过期，转人工确认",
        )

    if cur_biz is not None and in_biz < cur_biz:
        return RuleResult(
            passed=False,
            reason_code="EXPIRED_SOURCE_BLOCKED",
            message=(
                f"该来源的业务发生时间（{in_biz:%Y-%m-%d %H:%M}）早于当前有效版本"
                f"（{cur_biz:%Y-%m-%d %H:%M}），属过期来源，已拦截不覆盖"
            ),
            data={
                "incoming_biz_time": in_biz.isoformat(),
                "current_biz_time": cur_biz.isoformat(),
            },
        )

    return RuleResult(passed=True, message="来源时间新于当前版本，允许写入")


# =================================================================
# 硬约束前置检查（R5 / R6）
# =================================================================
def scan_sensitive_intent(text: str) -> RuleResult:
    """扫描文本中是否触碰发布意图或法律结论（f05 / f52）。

    这一条特殊：**不阻断**，而是标记"必须人工判定"。
    因为用户确实可能只是问"这条文案能发布吗"，
    粗暴拒绝会让系统没用；但让模型给出"可以发布"的结论是绝对禁止的。

    所以正确动作是：标记 + 限定模型的输出口径（见 agents/prompts.py）。
    """
    hits: List[str] = []
    low = text or ""

    for kw in HardRules.PUBLISH_KEYWORDS:
        if kw in low:
            hits.append(f"发布意图：{kw}")

    for kw in HardRules.LEGAL_KEYWORDS:
        if kw in low:
            hits.append(f"法律相关：{kw}")

    if not hits:
        return RuleResult(passed=True)

    return RuleResult(
        passed=False,
        reason_code="NEEDS_HUMAN_JUDGEMENT",
        message="涉及发布或法律审查，须由授权人员判定，系统不得给出确定结论",
        data={"hits": hits},
    )
