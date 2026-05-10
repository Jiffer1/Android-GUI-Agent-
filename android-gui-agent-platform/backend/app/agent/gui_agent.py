from app.agent.schemas import (
    AgentInput, AgentOutput,
    ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN, ACTION_COMPLETE,
)


class MockGuiAgent:
    """Scripted mock agent for testing without LLM calls.

    Returns deterministic actions based on step_index so the full
    runtime loop can be exercised without a real device or model.
    Replace this class with a real VLM-backed agent later.
    """

    def reset(self):
        pass

    def act(self, input_data: AgentInput) -> AgentOutput:
        step = input_data.step_count

        if step == 0:
            return AgentOutput(
                action=ACTION_CLICK,
                parameters={"point": [500, 500]},
                raw_output="mock: observe center",
            )
        elif step == 1:
            return AgentOutput(
                action=ACTION_SCROLL,
                parameters={"start_point": [500, 700], "end_point": [500, 300]},
                raw_output="mock: scroll up",
            )
        elif step == 2:
            return AgentOutput(
                action=ACTION_TYPE,
                parameters={"text": "hello world"},
                raw_output="mock: type text",
            )
        elif step == 3:
            return AgentOutput(
                action=ACTION_CLICK,
                parameters={"point": [500, 900]},
                raw_output="mock: tap bottom button",
            )
        else:
            return AgentOutput(
                action=ACTION_COMPLETE,
                parameters={},
                raw_output="mock: task complete",
            )
