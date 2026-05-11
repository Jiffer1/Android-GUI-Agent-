from typing import Optional, Dict, Protocol, runtime_checkable

from app.agent.schemas import AgentInput, AgentOutput


@runtime_checkable
class GuiAgent(Protocol):
    last_ui_state: Optional[Dict]

    def reset(self) -> None: ...

    def act(self, input_data: AgentInput) -> AgentOutput: ...
