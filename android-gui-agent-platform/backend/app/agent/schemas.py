from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from PIL.Image import Image as PILImage

ACTION_CLICK = "CLICK"
ACTION_SCROLL = "SCROLL"
ACTION_TYPE = "TYPE"
ACTION_OPEN = "OPEN"
ACTION_COMPLETE = "COMPLETE"
ACTION_BACK = "BACK"
ACTION_HOME = "HOME"
ACTION_ASK = "ASK"
ACTION_WAIT = "WAIT"  # feature 825: loading-time real wait (see FR-09)

ALL_ACTIONS = [
    ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
    ACTION_COMPLETE, ACTION_BACK, ACTION_HOME, ACTION_ASK,
    ACTION_WAIT,
]

RISK_LEVEL_SAFE = "safe"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
ALL_RISK_LEVELS = [RISK_LEVEL_SAFE, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH]

RISK_CATEGORY_NONE = "none"
RISK_CATEGORY_PAYMENT = "payment"
RISK_CATEGORY_DELETE = "delete"
RISK_CATEGORY_AUTH = "auth"
RISK_CATEGORY_SUBMIT = "submit"
RISK_CATEGORY_COMMUNICATION = "communication"
RISK_CATEGORY_SYSTEM = "system"
ALL_RISK_CATEGORIES = [
    RISK_CATEGORY_NONE,
    RISK_CATEGORY_PAYMENT,
    RISK_CATEGORY_DELETE,
    RISK_CATEGORY_AUTH,
    RISK_CATEGORY_SUBMIT,
    RISK_CATEGORY_COMMUNICATION,
    RISK_CATEGORY_SYSTEM,
]


@dataclass
class AgentInput:
    instruction: str
    current_image: PILImage
    step_count: int
    history_messages: List[Dict[str, Any]] = field(default_factory=list)
    history_actions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    # Conversation mode: recent chat messages ([{"role","kind","content"}]),
    # injected long-term memory text, and the reply to the last ASK (if any).
    conversation_context: List[Dict[str, Any]] = field(default_factory=list)
    memory_text: str = ""
    ask_reply: str = ""
    # Feature 908: deterministic receipt of the previous EXECUTED action
    # ("上步 <action> <参数摘要> 后页面已变化/无变化"), computed by the caller
    # (engine / benchmark adapter) from before/after screenshots. Empty string
    # on the turn's first step, after skipped steps, and in no-device turns.
    last_step_feedback: str = ""


@dataclass
class AgentOutput:
    action: str
    parameters: Dict[str, Any]
    raw_output: str = ""
    risk_level: str = RISK_LEVEL_SAFE
    risk_category: str = RISK_CATEGORY_NONE
    current_state: str = ""
    consequence: str = ""
    rollback_hint: str = ""
    risk_reason: str = ""
    confidence: float = 1.0
    stuck_count: int = 0
    executable: bool = True
    skip_reason: str = ""
