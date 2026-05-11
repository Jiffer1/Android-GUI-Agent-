"""EscalationPolicy: decide when to swap from Simple/Standard to ReAct.

Signals (all derived from data the runtime already has):
- low_confidence  : AgentOutput.confidence below threshold
- no_element      : agent.last_ui_state.elements is empty
- repeat_action   : the last N history actions are identical
- screen_stuck    : last N pre-action screenshots are pairwise similar
- plan_mismatch   : current_subgoal_index regresses or oscillates

The runtime decides what to do with the decision (typically: rebuild the agent
as a ReactGuiAgent, transferring history_actions). This module is pure logic;
it never builds or mutates an agent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.agent.schemas import AgentOutput, ROUTE_REACT

logger = logging.getLogger(__name__)


CONFIDENCE_THRESHOLD = 0.45
REPEAT_WINDOW = 3
SCREEN_STUCK_WINDOW = 3


@dataclass
class EscalationDecision:
    should_escalate: bool
    reason: str = ""
    signals: List[str] = field(default_factory=list)
    target_route: str = ROUTE_REACT


def evaluate(
    output: AgentOutput,
    *,
    history_actions: Sequence[Dict[str, Any]],
    agent_last_ui_state: Optional[Dict[str, Any]],
    screen_similar_streak: int,
    last_subgoal_index: Optional[int],
    current_route: str,
) -> EscalationDecision:
    if current_route == ROUTE_REACT:
        return EscalationDecision(should_escalate=False, reason="already react")

    signals: List[str] = []

    if output.confidence < CONFIDENCE_THRESHOLD:
        signals.append(f"low_confidence={output.confidence:.2f}")

    if agent_last_ui_state is not None:
        elements = agent_last_ui_state.get("elements") or []
        if not elements:
            signals.append("no_element")

    if len(history_actions) >= REPEAT_WINDOW:
        recent = history_actions[-REPEAT_WINDOW:]
        first = recent[0]
        if all(a.get("action") == first.get("action") and a.get("parameters") == first.get("parameters") for a in recent):
            signals.append(f"repeat_action x{REPEAT_WINDOW}")

    if output.stuck_count >= 2:
        signals.append(f"stuck_count={output.stuck_count}")

    if screen_similar_streak >= SCREEN_STUCK_WINDOW:
        signals.append(f"screen_stuck x{screen_similar_streak}")

    if (
        last_subgoal_index is not None
        and output.current_subgoal_index is not None
        and output.current_subgoal_index < last_subgoal_index
    ):
        signals.append(
            f"plan_mismatch (subgoal {last_subgoal_index} -> {output.current_subgoal_index})"
        )

    if signals:
        reason = "; ".join(signals)
        logger.info("Escalation triggered: %s", reason)
        return EscalationDecision(should_escalate=True, reason=reason, signals=signals)

    return EscalationDecision(should_escalate=False)
