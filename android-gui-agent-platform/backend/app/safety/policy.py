import json
from typing import Any, Dict

from pydantic import BaseModel

HIGH_RISK_KEYWORDS = ["支付", "提交订单", "删除", "发布", "打车", "授权", "转账", "清空", "卸载", "格式化"]
PAYMENT_APPS = ["支付宝", "微信支付", "alipay", "wechatpay", "paypal"]


class SafetyResult(BaseModel):
    is_safe: bool
    risk_level: str
    reason: str


def check_action(action: str, parameters: Dict[str, Any]) -> SafetyResult:
    text_to_check = action + " " + json.dumps(parameters, ensure_ascii=False)

    for kw in HIGH_RISK_KEYWORDS:
        if kw in text_to_check:
            return SafetyResult(is_safe=False, risk_level="high", reason=f"包含高风险关键词: {kw}")

    if action == "OPEN":
        app_name = str(parameters.get("app_name", "")).lower()
        for app in PAYMENT_APPS:
            if app.lower() in app_name:
                return SafetyResult(is_safe=False, risk_level="high", reason=f"打开支付类应用: {app_name}")

    return SafetyResult(is_safe=True, risk_level="safe", reason="")
