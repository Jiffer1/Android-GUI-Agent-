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

ALL_ACTIONS = [
    ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
    ACTION_COMPLETE, ACTION_BACK, ACTION_HOME, ACTION_ASK,
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
    current_subgoal_index: Optional[int] = None
    stuck_count: int = 0
    ui_risk_elements: List[Dict[str, Any]] = field(default_factory=list)
    executable: bool = True
    skip_reason: str = ""
