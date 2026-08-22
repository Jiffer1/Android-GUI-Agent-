import asyncio
from typing import Optional

from app.agent.schemas import AgentOutput


class ConversationSession:
    """In-memory runtime state for the active turn of one conversation.

    All waits (ask / confirm) are asyncio.Events; stop_turn sets every one of
    them so a waiting coroutine can never hang.
    """

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.status = "pending"

        self.stop_requested = False

        self.waiting_ask = False
        self.ask_event = asyncio.Event()
        self.ask_reply = ""

        self.waiting_confirm = False
        self.pending_action: Optional[AgentOutput] = None
        self.confirm_event = asyncio.Event()
        self.confirm_approved = False

        # Turn bookkeeping (set by the engine when the turn starts)
        self.turn_id: Optional[str] = ""
        self.device_id: Optional[str] = None
        self.turn_task: Optional[object] = None  # concurrent.futures.Future
        self.turn_loop: Optional[asyncio.AbstractEventLoop] = None
