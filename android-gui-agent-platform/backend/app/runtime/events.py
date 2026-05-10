from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel, Field

TASK_STARTED = "task.started"
STEP_STARTED = "step.started"
STEP_COMPLETED = "step.completed"
TASK_PAUSED = "task.paused"
TASK_RESUMED = "task.resumed"
TASK_FINISHED = "task.finished"
TASK_FAILED = "task.failed"
TASK_STOPPED = "task.stopped"
RISK_DETECTED = "risk.detected"


class WSEvent(BaseModel):
    event: str
    task_id: str
    data: Dict[str, Any] = {}
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
