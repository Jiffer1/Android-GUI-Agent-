from dataclasses import replace
from typing import Any, Dict, List

from pydantic import BaseModel

from app.agent.schemas import (
    AgentOutput,
    RISK_LEVEL_SAFE,
    RISK_LEVEL_MEDIUM,
    RISK_LEVEL_HIGH,
    RISK_CATEGORY_NONE,
    ALL_RISK_LEVELS,
    ALL_RISK_CATEGORIES,
)


class SafetyResult(BaseModel):
    is_safe: bool
    risk_level: str = RISK_LEVEL_SAFE
    risk_category: str = RISK_CATEGORY_NONE
    current_state: str = ""
    consequence: str = ""
    rollback_hint: str = ""
    reason: str = ""


def assess_output(output: AgentOutput) -> SafetyResult:
    """组装风险评估结果。

    风险评估由模型在同一次调用中产出（写入 AgentOutput 的 risk_*
    字段，仅 product 模式的 prompt 要求）。本函数仅做字段校验、
    降级与组装，不再独立判断关键词。
    """
    level = (output.risk_level or RISK_LEVEL_SAFE).strip().lower()
    if level not in ALL_RISK_LEVELS:
        level = RISK_LEVEL_SAFE

    category = (output.risk_category or RISK_CATEGORY_NONE).strip().lower()
    if category not in ALL_RISK_CATEGORIES:
        category = RISK_CATEGORY_NONE

    if level == RISK_LEVEL_SAFE and category != RISK_CATEGORY_NONE:
        category = RISK_CATEGORY_NONE

    is_safe = level != RISK_LEVEL_HIGH

    return SafetyResult(
        is_safe=is_safe,
        risk_level=level,
        risk_category=category,
        current_state=output.current_state or "",
        consequence=output.consequence or "",
        rollback_hint=output.rollback_hint or "",
        reason=output.risk_reason or "",
    )


# feature 908: text-level defence line replacing the 825 element-level
# CLICK-UI alignment (elements no longer exist in the slim schema). A CLICK
# whose `target` hits a keyword is forced to high risk regardless of the
# model's self-assessment.
_RISK_KEYWORDS: Dict[str, List[str]] = {
    "payment": ["支付", "付款", "转账", "收银台", "买单"],
    "delete": ["删除", "清空", "移除", "卸载", "清除"],
    "communication": ["发送", "拨号", "呼叫", "打电话"],
    "auth": ["授权", "允许并", "同意并"],
    "submit": ["提交", "确认订单", "下单", "确认提交"],
}


def augment_risk_from_text(output: AgentOutput) -> AgentOutput:
    """CLICK 命中高风险关键词 → 代码强制 risk_level=high(文本级防线)。

    纯函数(review m-3):命中时返回升级后的**新** AgentOutput,入参对象
    保持不变。仅升级、不降级:模型已自评 high 的保持不变。非 CLICK 动作
    原样返回(对应被移除的 825 FR-04 元素级命中,防线从元素级降为
    文本级,用户已确认接受)。
    """
    if output.action != "CLICK":
        return output
    target = str(output.parameters.get("target", "") or "")
    if not target:
        return output
    for category, keywords in _RISK_KEYWORDS.items():
        if any(k in target for k in keywords):
            if output.risk_level == RISK_LEVEL_HIGH:
                return output
            updates: Dict[str, Any] = {
                "risk_level": RISK_LEVEL_HIGH,
                "risk_category": category,
            }
            if not output.risk_reason:
                updates["risk_reason"] = (
                    f"点击目标「{target}」命中{category}类高风险关键词")
            return replace(output, **updates)
    return output
