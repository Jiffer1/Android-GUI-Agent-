"""TaskRouter: pick an agent based on task complexity.

Stage 5 implementation. Routing is rule-based first (cheap, deterministic) and
only consults the LLM when the rules are ambiguous. The router never looks at
the device — it only sees the instruction and the configured override.

Complexity buckets:
- simple   : a single screen will likely finish the task ("打开抖音", "回到桌面")
- standard : default; multi-step but the goal is clear (current VlmGuiAgent)
- react    : explicitly requested or instruction is vague / comparative /
             contains exception handling
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from app.agent.gui_agent import MockGuiAgent
from app.agent.react_agent import ReactGuiAgent
from app.agent.schemas import ROUTE_REACT, ROUTE_SIMPLE, ROUTE_STANDARD
from app.agent.simple_agent import SimpleGuiAgent
from app.agent.vlm_agent import VlmGuiAgent

logger = logging.getLogger(__name__)


SIMPLE_PATTERNS = [
    r"^打开\S{1,8}$",
    r"^启动\S{1,8}$",
    r"^回到桌面$",
    r"^返回(上一页|主页)$",
    r"^切换到\S{1,8}$",
]

REACT_KEYWORDS = [
    "比较", "对比", "找一个", "推荐", "尽量", "尝试", "如果",
    "看起来", "可能", "随便", "或者", "不确定",
]


@dataclass
class RouteDecision:
    route: str
    reason: str


def classify(instruction: str, override: Optional[str] = None) -> RouteDecision:
    """Pure decision function. Easy to unit test without instantiating agents."""
    if override:
        v = override.strip().lower()
        if v in (ROUTE_SIMPLE, ROUTE_STANDARD, ROUTE_REACT):
            return RouteDecision(route=v, reason=f"override={v}")

    text = (instruction or "").strip()
    if not text:
        return RouteDecision(route=ROUTE_STANDARD, reason="empty instruction")

    for pat in SIMPLE_PATTERNS:
        if re.match(pat, text):
            return RouteDecision(route=ROUTE_SIMPLE, reason=f"matches simple pattern: {pat}")

    if len(text) <= 8 and any(text.startswith(prefix) for prefix in ("打开", "启动", "退出", "卸载")):
        return RouteDecision(route=ROUTE_SIMPLE, reason="short app-launch instruction")

    for kw in REACT_KEYWORDS:
        if kw in text:
            return RouteDecision(route=ROUTE_REACT, reason=f"contains hint keyword: {kw}")

    if len(text) > 60:
        return RouteDecision(route=ROUTE_REACT, reason="instruction is long and likely complex")

    return RouteDecision(route=ROUTE_STANDARD, reason="default")


def build_agent(instruction: str, *, override: Optional[str] = None, mock: bool = False):
    """Pick an agent implementation. Returns (agent, RouteDecision)."""
    if mock:
        return MockGuiAgent(), RouteDecision(route=ROUTE_STANDARD, reason="mock agent forced")

    decision = classify(instruction, override=override or os.environ.get("AGENT_ROUTE_OVERRIDE"))
    if decision.route == ROUTE_SIMPLE:
        agent = SimpleGuiAgent()
    elif decision.route == ROUTE_REACT:
        agent = ReactGuiAgent()
    else:
        agent = VlmGuiAgent()

    logger.info("TaskRouter route=%s reason=%s instruction=%r", decision.route, decision.reason, instruction[:80])
    return agent, decision
