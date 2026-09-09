"""Contract tests: engine M-2 fix + WAIT semantics (feature 825).

M-2: a second ASK in the SAME turn must pause again. The old code never
cleared ``ask_event`` before waiting, so the reply to the first ASK left the
event set and the second ``wait()`` fell straight through — the turn kept
running without the user's answer. Contract here:

- after the first reply, the second ASK leaves the turn in ``waiting_ask``
  and the agent is NOT called again until the second reply arrives;
- stopping while waiting still exits immediately (stop_turn sets the event).

WAIT (engine side): a WAIT step performs no device operation but triggers
the screen-stabilization wait with the WAIT budget — no agent/VLM call may
happen while that wait is in flight.
"""

import asyncio

from PIL import Image

from app.agent.schemas import ACTION_ASK, ACTION_COMPLETE, ACTION_WAIT, AgentOutput
from app.storage.models import Conversation
from tests.conftest import get_turn, wait_for


# ---------------------------------------------------------------------------
# Scripted agent & helpers
# ---------------------------------------------------------------------------

class ScriptAgent:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def reset(self):
        pass

    def act(self, input_data):
        self.inputs.append(input_data)
        if not self.outputs:
            return AgentOutput(action=ACTION_COMPLETE, parameters={}, raw_output="script exhausted")
        return self.outputs.pop(0)

    def summarize_turn(self, input_data):
        return "本轮已完成"


def ask(question="需要哪个?", options=None):
    return AgentOutput(
        action=ACTION_ASK,
        parameters={"question": question, "options": options or ["A", "B"]},
        raw_output="mock: ask",
    )


def scripted_factory(agents, outputs):
    def factory():
        agent = ScriptAgent(outputs)
        agents.append(agent)
        return agent

    return factory


async def wait_turn_status(turn_id, statuses, timeout=8.0):
    def check():
        t = get_turn(turn_id)
        return t.status if (t is not None and t.status in statuses) else None

    return await wait_for(check, timeout=timeout,
                          message=f"turn {turn_id} did not reach {statuses}")


def count_ask_messages(db, conversation_id):
    from app.storage.models import Message

    return (db.query(Message)
            .filter_by(conversation_id=conversation_id, role="assistant", kind="ask")
            .count())


# ---------------------------------------------------------------------------
# M-2: second ASK in the same turn pauses again
# ---------------------------------------------------------------------------

class TestSecondAskWaits:
    async def test_second_ask_holds_until_reply(self, db, conversation_id, make_engine):
        agents = []
        engine = make_engine(scripted_factory(
            agents, [ask("第一问"), ask("第二问"), AgentOutput(action=ACTION_COMPLETE, parameters={})]))

        turn = await engine.start_turn(conversation_id, "任务A")
        await wait_turn_status(turn.id, ["waiting_ask"])

        await engine.reply_ask(conversation_id, "第一次回复")

        def second_ask_seen():
            db.expire_all()
            return count_ask_messages(db, conversation_id) >= 2 or None

        await wait_for(second_ask_seen, message="second ask message never appeared")

        # Give a buggy engine (event not cleared) time to race past the wait.
        await asyncio.sleep(0.3)
        assert get_turn(turn.id).status == "waiting_ask"
        assert len(agents[0].inputs) == 2  # no third decision before the reply

        await engine.reply_ask(conversation_id, "第二次回复")
        await wait_turn_status(turn.id, ["finished"])
        assert len(agents[0].inputs) >= 3

    async def test_stop_while_waiting_second_ask_exits(self, db, conversation_id, make_engine):
        agents = []
        engine = make_engine(scripted_factory(
            agents, [ask("第一问"), ask("第二问"), AgentOutput(action=ACTION_COMPLETE, parameters={})]))

        turn = await engine.start_turn(conversation_id, "任务B")
        await wait_turn_status(turn.id, ["waiting_ask"])
        await engine.reply_ask(conversation_id, "第一次回复")

        def second_ask_seen():
            db.expire_all()
            return count_ask_messages(db, conversation_id) >= 2 or None

        await wait_for(second_ask_seen, message="second ask message never appeared")
        engine.stop_turn(conversation_id)
        await wait_turn_status(turn.id, ["stopped"])


# ---------------------------------------------------------------------------
# WAIT semantics (engine side)
# ---------------------------------------------------------------------------

class FakeController:
    def __init__(self):
        self.ops = []

    def screenshot(self):
        return Image.new("RGB", (64, 64))

    def click(self, point):
        self.ops.append(("click", point))

    def scroll(self, start, end):
        self.ops.append(("scroll", start, end))

    def type_text(self, text):
        self.ops.append(("type", text))

    def open_app(self, name):
        self.ops.append(("open", name))

    def back(self):
        self.ops.append(("back",))

    def home(self):
        self.ops.append(("home",))


class TestEngineWait:
    async def test_wait_triggers_stable_wait_and_no_vlm_during_it(
            self, db, make_engine, monkeypatch):
        from app.runtime import engine as engine_module
        from app.runtime.engine import ConversationEngine

        wait_output = AgentOutput(
            action=ACTION_WAIT, parameters={"reason": "页面加载中"}, raw_output="wait")
        done_output = AgentOutput(action=ACTION_COMPLETE, parameters={}, raw_output="done")
        agents = []
        engine = make_engine(scripted_factory(agents, [wait_output, done_output]))

        conversation = Conversation(title="WAIT 会话", device_id="emu-test")
        db.add(conversation)
        db.commit()
        cid = conversation.id

        controller = FakeController()
        monkeypatch.setattr(engine_module, "get_controller", lambda device_id: controller)

        stable_actions = []

        async def fake_stable(self, controller, loop, action):
            before = len(agents[0].inputs)
            stable_actions.append(action)
            await asyncio.sleep(0.25)
            after = len(agents[0].inputs)
            assert after == before, "agent was called during the WAIT stabilization"
            return Image.new("RGB", (64, 64))

        monkeypatch.setattr(ConversationEngine, "_wait_for_stable_screen", fake_stable)

        turn = await engine.start_turn(cid, "加载后完成任务")
        await wait_turn_status(turn.id, ["finished"])

        assert stable_actions == ["WAIT"]      # stabilization keyed on WAIT
        assert controller.ops == []            # no device operation for WAIT
        assert len(agents[0].inputs) == 2       # WAIT step + COMPLETE step
