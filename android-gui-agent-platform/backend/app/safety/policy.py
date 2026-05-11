from typing import Any, Dict, List, Optional

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
    ui_risk_elements: List[Dict[str, Any]] = []


def assess_output(output: AgentOutput, ui_risk_elements: Optional[List[Dict[str, Any]]] = None) -> SafetyResult:
    """组装风险评估结果。

    风险评估由 ActionAnalyzer 在同一次模型调用中产出（写入 AgentOutput 的
    risk_* 字段）。本函数仅做字段校验、降级与组装，不再独立判断关键词。
    """
    level = (output.risk_level or RISK_LEVEL_SAFE).strip().lower()
    if level not in ALL_RISK_LEVELS:
        level = RISK_LEVEL_SAFE

    category = (output.risk_category or RISK_CATEGORY_NONE).strip().lower()
    if category not in ALL_RISK_CATEGORIES:
        category = RISK_CATEGORY_NONE

    if level == RISK_LEVEL_SAFE and category != RISK_CATEGORY_NONE:
        category = RISK_CATEGORY_NONE

    elements = ui_risk_elements if ui_risk_elements is not None else list(output.ui_risk_elements or [])

    is_safe = level != RISK_LEVEL_HIGH

    return SafetyResult(
        is_safe=is_safe,
        risk_level=level,
        risk_category=category,
        current_state=output.current_state or "",
        consequence=output.consequence or "",
        rollback_hint=output.rollback_hint or "",
        reason=output.risk_reason or "",
        ui_risk_elements=elements,
    )


def check_action(action: str, parameters: Dict[str, Any]) -> SafetyResult:
    """旧入口，保留以兼容尚未迁移的调用方。

    没有模型自评字段可读，默认判定为安全；新代码应改为调用 assess_output。
    """
    return SafetyResult(is_safe=True, risk_level=RISK_LEVEL_SAFE, risk_category=RISK_CATEGORY_NONE)
