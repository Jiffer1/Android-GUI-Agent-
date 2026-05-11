import asyncio
from typing import Optional

from app.agent.schemas import AgentOutput, ROUTE_STANDARD


class TaskSession:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.status = "pending"

        # pause_event: set = running, clear = paused
        self.pause_event = asyncio.Event()
        self.pause_event.set()

        self.stop_requested = False
        self.waiting_for_confirm = False
        self.pending_action: Optional[AgentOutput] = None
        self.confirm_event = asyncio.Event()
        self.confirm_approved = False

        self.route: str = ROUTE_STANDARD
        self.escalated: bool = False
        self.escalation_reason: str = ""
