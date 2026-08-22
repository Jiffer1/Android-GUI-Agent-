from datetime import datetime
from typing import Any, Dict
from pydantic import BaseModel, Field

MESSAGE_CREATED = "message.created"
TURN_STARTED = "turn.started"
TURN_FINISHED = "turn.finished"
TURN_FAILED = "turn.failed"
TURN_STOPPED = "turn.stopped"
STEP_STARTED = "step.started"
STEP_COMPLETED = "step.completed"
ASK_REQUESTED = "ask.requested"
ASK_ANSWERED = "ask.answered"
RISK_DETECTED = "risk.detected"
RISK_OBSERVED = "risk.observed"
MEMORY_UPDATED = "memory.updated"


class WSEvent(BaseModel):
    event: str
    conversation_id: str
    data: Dict[str, Any] = {}
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
