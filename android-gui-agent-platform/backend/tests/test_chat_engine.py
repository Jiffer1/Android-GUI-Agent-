"""TDD contract tests for the conversation engine (plan.md Step 2/4).

Covers: scripted-agent happy path (AC-08), ASK wait & resume (AC-02), risk
confirm approve/cancel (AC-03), stop while waiting, per-conversation mutual
exclusion, context/memory injection (AC-01), turn summary, memory save
(AC-04/05), and the extended MockGuiAgent script.

Key engine contracts fixed here:
- ``ConversationEngine(agent_factory=None, memory_store=None)``; the factory
  is called ONCE PER TURN (fresh agent per turn, no cross-turn subgoal leak).
- ``start_turn`` returns the created Turn; raises ValueError when the
  conversation already has an active turn.
- ``reply_ask`` raises ValueError when not waiting for an ask;
  ``confirm(cid, approved)`` raises ValueError when not waiting for confirm;
  cancelling a risk terminates the turn (status=stopped) but the conversation
  accepts a new turn afterwards.
- ``stop_turn`` releases BOTH ask_event and confirm_event (no hanging turns).
- Turn status values: pending/running/waiting_ask/waiting_confirm/finished/
  failed/stopped.

These tests are RED until the engine is rewritten.
"""

import asyncio

import pytest

from app.agent.gui_agent import MockGuiAgent
from app.agent.schemas import ACTION_ASK, ACTION_COMPLETE, AgentInput, AgentOutput
from app.storage.db import SessionLocal
from app.storage.models import Message, TurnStep
from tests.conftest import event_names, get_turn, wait_for


# ---------------------------------------------------------------------------
# Scripted agent & output factories
# ---------------------------------------------------------------------------

class ScriptAgent:
    """Pops one scripted output per act() call and records every AgentInput."""

    def __init__(self, outputs, summary="本轮已完成"):
        self.outputs = list(outputs)
        self.inputs = []
        self._summary = summary
        self.last_ui_state = None

    def reset(self):
        pass

    def act(self, input_data):
        self.inputs.append(input_data)
        if not self.outputs:
            return AgentOutput(action=ACTION_COMPLETE, parameters={}, raw_output="script exhausted")
        return self.outputs.pop(0)

    def summarize_turn(self, input_data):
        return self._summary


def click():
    return AgentOutput(action="CLICK", parameters={"point": [500, 500]}, raw_output="mock: click")


def ask(question="用哪个地图?", options=None):
    return AgentOutput(
        action=ACTION_ASK,
        parameters={"question": question, "options": options or ["高德", "百度"]},
        raw_output="mock: ask user",
    )


def risky_click():
    return AgentOutput(
        action="CLICK",
        parameters={"point": [500, 900]},
        raw_output="mock: pay button",
        risk_level="high",
        risk_category="payment",
        current_state="支付确认页",
        consequence="可能扣款",
        rollback_hint="订单可关闭",
    )


def scripted_factory(agents, outputs, summary="本轮已完成"):
    def factory():
        agent = ScriptAgent(outputs, summary=summary)
        agents.append(agent)
        return agent

    return factory


async def wait_turn_status(turn_id, statuses, timeout=8.0):
    def check():
        t = get_turn(turn_id)
        return t.status if (t is not None and t.status in statuses) else None

    return await wait_for(check, timeout=timeout,
                          message=f"turn {turn_id} did not reach {statuses}")


def fetch_steps(turn_id):
    with SessionLocal() as s:
        return s.query(TurnStep).filter_by(turn_id=turn_id).order_by(TurnStep.step_index).all()


def fetch_messages(conversation_id):
    with SessionLocal() as s:
        msgs = s.query(Message).filter_by(conversation_id=conversation_id).all()
        return [(m.role, m.kind, m.content) for m in msgs]


def find_message(messages, role, kind):
    return [m for m in messages if m[0] == role and m[1] == kind]


# ---------------------------------------------------------------------------
# Session & schemas contracts
# ---------------------------------------------------------------------------

class TestConversationSession:
    def test_initial_state(self):
        from app.runtime.session import ConversationSession

        s = ConversationSession("conv-1")
        assert s.conversation_id == "conv-1"
        assert s.stop_requested is False
        assert s.waiting_ask is False
        assert s.waiting_confirm is False
        assert s.ask_reply == ""
        assert s.confirm_approved is False
        assert isinstance(s.ask_event, asyncio.Event)
        assert isinstance(s.confirm_event, asyncio.Event)


class TestSchemas:
    def test_action_ask_constant(self):
        assert ACTION_ASK == "ASK"

    def test_agent_input_conversation_fields_default(self):
        inp = AgentInput(instruction="x", current_image=None, step_count=0)
        assert inp.conversation_context == []
        assert inp.memory_text == ""
        assert inp.ask_reply == ""


# ---------------------------------------------------------------------------
# MockGuiAgent conversation script (plan.md Step 2)
# ---------------------------------------------------------------------------

class TestMockGuiAgentScript:
    def _input(self, step_count, **kw):
        return AgentInput(instruction="测试", current_image=None,
                          step_count=step_count, **kw)

    def test_step1_outputs_ask_with_question(self):
        out = MockGuiAgent().act(self._input(1))
        assert out.action == ACTION_ASK
        assert out.parameters.get("question")

    def test_resumes_after_ask_reply(self):
        agent = MockGuiAgent()
        agent.act(self._input(1))  # ASK consumed
        out = agent.act(self._input(2, ask_reply="用高德"))
        assert out.action != ACTION_ASK

    def test_completes_at_step3(self):
        out = MockGuiAgent().act(self._input(3))
        assert out.action == ACTION_COMPLETE

    def test_summarize_turn_returns_text(self):
        summary = MockGuiAgent().summarize_turn(self._input(0))
        assert isinstance(summary, str) and len(summary) > 0


# ---------------------------------------------------------------------------
# Engine: happy path & persistence
# ---------------------------------------------------------------------------

class TestTurnExecution:
    async def test_turn_completes_and_persists(self, make_engine, conversation_id, captured_events):
        agents = []
        engine = make_engine(scripted_factory(agents, [click(), click(), AgentOutput(
            action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "打开抖音")
        await wait_turn_status(turn.id, {"finished"})

        # user message linked to the turn
        assert turn.user_message_id is not None
        msgs = fetch_messages(conversation_id)
        user_msgs = find_message(msgs, "user", "text")
        assert any(content == "打开抖音" for _, _, content in user_msgs)

        # summary message from the agent
        summaries = find_message(msgs, "assistant", "summary")
        assert any("本轮已完成" in content for _, _, content in summaries)

        # one TurnStep per agent output (COMPLETE included), like the old engine
        steps = fetch_steps(turn.id)
        assert len(steps) == 3
        assert steps[-1].action == "COMPLETE"

        # WS events, keyed by conversation_id
        names = event_names(captured_events)
        for expected in ["message.created", "turn.started", "step.completed", "turn.finished"]:
            assert expected in names
        for evt in captured_events:
            assert evt["conversation_id"] == conversation_id

    async def test_max_steps_bounds_turn(self, make_engine, conversation_id, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "MAX_STEPS", 2)
        agents = []
        engine = make_engine(scripted_factory(agents, [click(), click(), click(), click()]))

        turn = await engine.start_turn(conversation_id, "一直点")
        await wait_turn_status(turn.id, {"finished"})

        assert len(fetch_steps(turn.id)) == 2


# ---------------------------------------------------------------------------
# Engine: context & memory injection
# ---------------------------------------------------------------------------

class TestContextInjection:
    async def test_multi_turn_context_carried_over(self, make_engine, conversation_id):
        agents = []
        engine = make_engine(scripted_factory(agents, [click(), AgentOutput(
            action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        await engine.start_turn(conversation_id, "打开航旅纵横查航班")
        await wait_turn_status(
            (await _last_turn_id(conversation_id)), {"finished"})

        await engine.start_turn(conversation_id, "继续")
        await wait_turn_status(
            (await _last_turn_id(conversation_id)), {"finished"})

        # second turn's agent received the first turn's user message + summary
        ctx = agents[1].inputs[0].conversation_context
        assert len(ctx) > 0
        assert any("航旅纵横" in str(m) for m in ctx)
        assert any("本轮已完成" in str(m) for m in ctx)

    async def test_memory_text_injected(self, make_engine, conversation_id, monkeypatch):
        from app.runtime import engine as engine_module

        monkeypatch.setattr(
            engine_module, "build_memory_context",
            lambda instruction, store=None: "用户常用高德地图")

        agents = []
        engine = make_engine(scripted_factory(agents, [AgentOutput(
            action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "导航去机场")
        await wait_turn_status(turn.id, {"finished"})

        assert agents[0].inputs[0].memory_text == "用户常用高德地图"


async def _last_turn_id(conversation_id):
    from app.storage.models import Turn

    def check():
        with SessionLocal() as s:
            t = (s.query(Turn).filter_by(conversation_id=conversation_id)
                 .order_by(Turn.created_at.desc()).first())
            return t.id if t else None

    return await wait_for(check, message="no turn created")


# ---------------------------------------------------------------------------
# Engine: ASK wait & resume (AC-02)
# ---------------------------------------------------------------------------

class TestAskWaitAndResume:
    async def test_ask_pauses_then_resumes_from_current_screen(self, make_engine, conversation_id, captured_events):
        agents = []
        engine = make_engine(scripted_factory(
            agents, [click(), ask("用哪个地图?"), click(), AgentOutput(
                action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "导航去机场")
        await wait_turn_status(turn.id, {"waiting_ask"})

        # ask card persisted as an assistant message and broadcast
        names = event_names(captured_events)
        assert "ask.requested" in names
        msgs = fetch_messages(conversation_id)
        assert any(kind == "ask" and "用哪个地图" in content
                   for _, kind, content in msgs)

        await engine.reply_ask(conversation_id, "用高德")
        await wait_turn_status(turn.id, {"finished"})

        # reply persisted as user message
        assert any(role == "user" and content == "用高德"
                   for role, _, content in fetch_messages(conversation_id))
        assert "ask.answered" in event_names(captured_events)

        # reply injected into the next agent input; steps moved FORWARD, not restarted
        inputs = agents[0].inputs
        ask_idx = next(i for i, inp in enumerate(inputs) if inp.ask_reply == "")
        resume_input = inputs[ask_idx + 1]
        assert resume_input.ask_reply == "用高德"
        assert resume_input.step_count > inputs[ask_idx].step_count

    async def test_stop_while_waiting_ask_releases_turn(self, make_engine, conversation_id):
        engine = make_engine(scripted_factory([], [ask()]))  # hangs on ask forever

        turn = await engine.start_turn(conversation_id, "随便")
        await wait_turn_status(turn.id, {"waiting_ask"})

        engine.stop_turn(conversation_id)
        # if stop_turn does not set ask_event, the coroutine hangs and this times out
        await wait_turn_status(turn.id, {"stopped"})

    async def test_reply_ask_without_active_ask_raises(self, make_engine, conversation_id):
        engine = make_engine(scripted_factory([], []))
        with pytest.raises(ValueError):
            await engine.reply_ask(conversation_id, "没人问过我")


# ---------------------------------------------------------------------------
# Engine: risk confirmation (AC-03)
# ---------------------------------------------------------------------------

class TestRiskConfirmation:
    async def test_high_risk_requires_confirm_then_executes(self, make_engine, conversation_id, captured_events):
        agents = []
        engine = make_engine(scripted_factory(
            agents, [click(), risky_click(), AgentOutput(
                action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "帮我付款")
        await wait_turn_status(turn.id, {"waiting_confirm"})
        assert "risk.detected" in event_names(captured_events)

        engine.confirm(conversation_id, True)
        await wait_turn_status(turn.id, {"finished"})

        steps = fetch_steps(turn.id)
        assert any(s.risk_level == "high" for s in steps)

    async def test_risk_cancel_terminates_turn_but_conversation_reusable(self, make_engine, conversation_id):
        agents = []
        engine = make_engine(scripted_factory(agents, [risky_click()]))

        turn = await engine.start_turn(conversation_id, "帮我付款")
        await wait_turn_status(turn.id, {"waiting_confirm"})

        engine.confirm(conversation_id, False)
        await wait_turn_status(turn.id, {"stopped"})

        # conversation survives: a new turn runs to completion
        turn2 = await engine.start_turn(conversation_id, "换个任务")
        await wait_turn_status(turn2.id, {"finished"})

    async def test_confirm_without_pending_risk_raises(self, make_engine, conversation_id):
        engine = make_engine(scripted_factory([], []))
        with pytest.raises(ValueError):
            engine.confirm(conversation_id, True)


# ---------------------------------------------------------------------------
# Engine: mutual exclusion & failure
# ---------------------------------------------------------------------------

class TestTurnLifecycle:
    async def test_same_conversation_rejects_concurrent_turn(self, make_engine, conversation_id):
        engine = make_engine(scripted_factory([], [ask()]))

        turn = await engine.start_turn(conversation_id, "第一条")
        await wait_turn_status(turn.id, {"waiting_ask"})

        with pytest.raises(ValueError):
            await engine.start_turn(conversation_id, "第二条")

        engine.stop_turn(conversation_id)
        await wait_turn_status(turn.id, {"stopped"})


# ---------------------------------------------------------------------------
# Engine: memory write on turn finish (AC-04 / AC-05)
# ---------------------------------------------------------------------------

class TestTurnMemorySave:
    async def test_explicit_memory_saved_and_broadcast(self, make_engine, conversation_id,
                                                       memory_store, monkeypatch, captured_events):
        from app.runtime import engine as engine_module

        monkeypatch.setattr(
            engine_module, "detect_explicit_memory",
            lambda text: "用户常用高德地图" if "记住" in text else None)

        agents = []
        engine = make_engine(scripted_factory(agents, [AgentOutput(
            action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "记住我用高德地图")
        await wait_turn_status(turn.id, {"finished"})

        assert "用户常用高德地图" in memory_store.preferences()
        assert "memory.updated" in event_names(captured_events)

    async def test_auto_extracted_memory_applied(self, make_engine, conversation_id,
                                                 memory_store, monkeypatch):
        from app.runtime import engine as engine_module

        monkeypatch.setattr(
            engine_module, "extract_turn_memory",
            lambda *args, **kwargs: [
                {"op": "add", "kind": "preference", "content": "用户偏好深色模式"}])

        agents = []
        engine = make_engine(scripted_factory(agents, [AgentOutput(
            action=ACTION_COMPLETE, parameters={}, raw_output="done")]))

        turn = await engine.start_turn(conversation_id, "打开设置")
        await wait_turn_status(turn.id, {"finished"})

        assert "用户偏好深色模式" in memory_store.preferences()
