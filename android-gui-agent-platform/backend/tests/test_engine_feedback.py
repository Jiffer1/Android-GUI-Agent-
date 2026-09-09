"""Contract tests: engine post-hoc feedback (feature 908, AC-03).

The engine computes a deterministic receipt after each EXECUTED action —
comparing the decision screenshot with the post-stabilization screenshot —
and injects it into the next step's ``AgentInput.last_step_feedback``:

- turn's first step: empty string;
- executed action + visible page change -> "已变化" receipt;
- executed action + unchanged page -> "无变化" receipt;
- skipped step (or no-device conversation): empty string.

``_wait_for_stable_screen`` is monkeypatched to return a controlled color so
the similarity verdict is deterministic; the scripted agent records every
AgentInput it receives.
"""

from PIL import Image

from app.agent.schemas import ACTION_CLICK, ACTION_COMPLETE, AgentOutput
from app.storage.models import Conversation
from tests.conftest import wait_for
from tests.test_engine_ask_reask import (
    FakeController,
    scripted_factory,
    wait_turn_status,
)


def click_output():
    return AgentOutput(
        action=ACTION_CLICK,
        parameters={"point": [500, 500], "target": "确定"},
        raw_output="click",
    )


def skip_output():
    return AgentOutput(
        action="PAUSE",
        parameters={},
        executable=False,
        skip_reason="invalid_action",
        raw_output="skip",
    )


def done_output():
    return AgentOutput(action=ACTION_COMPLETE, parameters={}, raw_output="done")


class TestStepFeedback:
    async def _run_with_device(self, db, make_engine, monkeypatch,
                               stable_color, first_output):
        from app.runtime import engine as engine_module
        from app.runtime.engine import ConversationEngine

        conversation = Conversation(title="回执会话", device_id="emu-fb")
        db.add(conversation)
        db.commit()
        cid = conversation.id

        controller = FakeController()
        monkeypatch.setattr(engine_module, "get_controller",
                            lambda device_id: controller)

        async def fake_stable(self, c, loop, action):
            # decision screenshot is black; the returned color decides the verdict
            return Image.new("RGB", (64, 64), color=stable_color)

        monkeypatch.setattr(ConversationEngine, "_wait_for_stable_screen",
                            fake_stable)

        agents = []
        engine = make_engine(scripted_factory(
            agents, [first_output, done_output()]))
        turn = await engine.start_turn(cid, "点击后完成任务")
        await wait_turn_status(turn.id, ["finished"])
        return agents[0]

    async def test_first_step_feedback_empty(self, db, make_engine, monkeypatch):
        agents = await self._run_with_device(
            db, make_engine, monkeypatch, (255, 255, 255), click_output())
        assert agents.inputs[0].last_step_feedback == ""

    async def test_changed_page_injects_receipt(self, db, make_engine, monkeypatch):
        agents = await self._run_with_device(
            db, make_engine, monkeypatch, (255, 255, 255), click_output())
        fb = agents.inputs[1].last_step_feedback
        assert "CLICK" in fb
        assert "已变化" in fb

    async def test_unchanged_page_injects_receipt(self, db, make_engine, monkeypatch):
        # FakeController.screenshot is black; a black stable image == unchanged
        agents = await self._run_with_device(
            db, make_engine, monkeypatch, (0, 0, 0), click_output())
        fb = agents.inputs[1].last_step_feedback
        assert "CLICK" in fb
        assert "无变化" in fb

    async def test_skipped_step_feedback_empty(self, db, make_engine, monkeypatch):
        agents = await self._run_with_device(
            db, make_engine, monkeypatch, (255, 255, 255), skip_output())
        assert agents.inputs[1].last_step_feedback == ""

    async def test_no_device_conversation_feedback_empty(
            self, db, conversation_id, make_engine):
        agents = []
        engine = make_engine(scripted_factory(
            agents, [click_output(), done_output()]))
        turn = await engine.start_turn(conversation_id, "点击后完成任务")
        await wait_turn_status(turn.id, ["finished"])
        assert agents[0].inputs[0].last_step_feedback == ""
        assert agents[0].inputs[1].last_step_feedback == ""
