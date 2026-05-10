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

ALL_ACTIONS = [ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN, ACTION_COMPLETE, ACTION_BACK, ACTION_HOME]


@dataclass
class AgentInput:
    instruction: str
    current_image: PILImage
    step_count: int
    history_messages: List[Dict[str, Any]] = field(default_factory=list)
    history_actions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    action: str
    parameters: Dict[str, Any]
    raw_output: str = ""
