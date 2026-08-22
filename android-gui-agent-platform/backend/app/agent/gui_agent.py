from app.agent.schemas import (
    AgentInput, AgentOutput,
    ACTION_ASK, ACTION_CLICK, ACTION_COMPLETE, ACTION_SCROLL,
)


class MockGuiAgent:
    """Scripted mock agent for testing the conversation pipeline without a VLM.

    Script (by step_count, README §4.2):
    - 0: CLICK center
    - 1: ASK (with question + options)
    - 2: a regular action after the ask reply
    - >=3: COMPLETE
    """

    def __init__(self):
        self.last_ui_state = None

    def reset(self):
        self.last_ui_state = None

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
                action=ACTION_ASK,
                parameters={
                    "question": "用哪个地图导航?",
                    "options": ["高德", "百度"],
                },
                raw_output="mock: ask user",
            )
        elif step == 2:
            return AgentOutput(
                action=ACTION_SCROLL,
                parameters={"start_point": [500, 700], "end_point": [500, 300]},
                raw_output="mock: continue after ask reply",
            )
        else:
            return AgentOutput(
                action=ACTION_COMPLETE,
                parameters={},
                raw_output="mock: task complete",
            )

    def summarize_turn(self, input_data: AgentInput) -> str:
        return "Mock: 本轮操作已完成。"
