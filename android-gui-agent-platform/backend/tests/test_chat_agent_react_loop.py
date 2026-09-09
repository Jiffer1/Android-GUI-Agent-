"""Contract tests: pure-ReAct decision loop rewrite (feature 908).

Locks the slim decision schema and the ReAct mechanics (README §4.6):
- exactly ONE VLM call per step, output JSON = thought / action / parameters
  (product mode adds the risk self-assessment block);
- no plan / elements / page_type / progress anywhere in the output or prompt
  (the plan-and-execute track and the CLICK-UI pre-alignment are gone);
- history is a full-text append-only messages trace
  (assistant(thought+action) / user(回执) pairs), never truncated,
  exactly one screenshot (the current one) per call;
- CLICK requires `target`; ASK may carry `reason_code`;
- three-level stuck ladder: symptom injection (L1) then forced ASK (L2);
- mode switch: benchmark prompt carries no risk / memory / chat-context
  sections; `disable_business_overrides=True` still maps to benchmark mode;
- degradation semantics unchanged (invalid JSON / invalid action -> skip,
  never a silent COMPLETE).

All VLM traffic is stubbed by monkeypatching ``ChatGuiAgent._call_api``.
"""

import json
from types import SimpleNamespace

from PIL import Image

import pytest

from app.agent.chat_agent import (
    ASK_ON_STUCK_STEPS,
    STUCK_SOFT_STEPS,
    ChatGuiAgent,
)
from app.agent.schemas import (
    ACTION_ASK,
    ACTION_CLICK,
    ACTION_COMPLETE,
    ACTION_WAIT,
    AgentInput,
)


# ---------------------------------------------------------------------------
# VLM stub & reply builders
# ---------------------------------------------------------------------------

class VLMStub:
    """Replaces ChatGuiAgent._call_api; records every call's messages."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("unexpected extra VLM call")
        content = self.replies.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def make_reply(remove=(), **overrides):
    """A well-formed slim-schema JSON reply; override/remove fields."""
    base = {
        "thought": "页面已加载，点击目标按钮",
        "action": "CLICK",
        "parameters": {"point": [500, 500], "target": "确定"},
        # product-mode risk block (benchmark replies just remove these)
        "risk_level": "safe",
        "risk_category": "none",
        "current_state": "详情页",
        "consequence": "",
        "rollback_hint": "",
        "risk_reason": "",
        "confidence": 0.9,
    }
    for key in remove:
        base.pop(key, None)
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def benchmark_reply(remove=(), **overrides):
    for key in ("risk_level", "risk_category", "current_state",
                "consequence", "rollback_hint", "risk_reason"):
        remove = tuple(remove) + (key,)
    return make_reply(remove=remove, **overrides)


FB_CHANGED = "上一步 CLICK [500, 500] 后页面已变化"
FB_NOCHANGE = "上一步 CLICK [500, 500] 后页面无变化"


def make_input(step=0, instruction="帮我完成任务", ask_reply="", feedback=""):
    return AgentInput(
        instruction=instruction,
        current_image=Image.new("RGB", (64, 64)),
        step_count=step,
        history_actions=[],
        conversation_context=[],
        memory_text="",
        ask_reply=ask_reply,
        last_step_feedback=feedback,
    )


def patched_agent(monkeypatch, replies, mode="product"):
    agent = ChatGuiAgent(mode=mode)
    stub = VLMStub(replies)
    monkeypatch.setattr(ChatGuiAgent, "_call_api", stub)
    return agent, stub


def user_blocks(messages):
    """The user content blocks of one call: [(type, obj), ...]."""
    for m in messages:
        if m.get("role") == "user":
            content = m["content"]
            if isinstance(content, list):
                return content
            return [{"type": "text", "text": content}]
    return []


def last_user_text(messages):
    """Text of the TRAILING user message (the per-step current prompt)."""
    blocks = None
    for m in messages:
        if m.get("role") == "user":
            c = m["content"]
            blocks = c if isinstance(c, list) else [{"type": "text", "text": c}]
    if not blocks:
        return ""
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def all_user_text(messages):
    return "".join(
        b.get("text", "")
        for m in messages if m.get("role") == "user"
        for b in (m["content"] if isinstance(m["content"], list)
                  else [{"type": "text", "text": m["content"]}])
        if b.get("type") == "text"
    )


def assistant_texts(messages):
    return [m["content"] for m in messages if m.get("role") == "assistant"]


def count_images(messages):
    return sum(
        1
        for m in messages if m.get("role") == "user"
        for b in (m["content"] if isinstance(m["content"], list)
                  else [{"type": "text", "text": m["content"]}])
        if b.get("type") == "image_url"
    )


def system_text(messages):
    return messages[0]["content"]


# ---------------------------------------------------------------------------
# AC-01: single VLM call per step, slim schema
# ---------------------------------------------------------------------------

class TestSingleCallLoop:
    def test_one_vlm_call_per_step(self, monkeypatch):
        agent, stub = patched_agent(monkeypatch, [make_reply(), make_reply(action="COMPLETE", parameters={})])
        agent.act(make_input(step=0))
        agent.act(make_input(step=1, feedback=FB_CHANGED))
        assert len(stub.calls) == 2  # exactly one call per step, step 0 included

    def test_no_separate_planner_stage(self):
        # The three-stage methods must stay gone; so must the 825 plan track.
        assert not hasattr(ChatGuiAgent, "_run_planner")
        assert not hasattr(ChatGuiAgent, "_extract_ui")
        assert not hasattr(ChatGuiAgent, "_analyze_action")
        assert not hasattr(ChatGuiAgent, "_update_plan")
        assert not hasattr(ChatGuiAgent, "_clean_elements")
        assert not hasattr(ChatGuiAgent, "_postprocess_action")


class TestSlimSchema:
    def test_step0_output_fields(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [make_reply()])
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_CLICK
        assert out.parameters == {"point": [500, 500], "target": "确定"}
        raw = json.loads(out.raw_output)
        assert raw["thought"] == "页面已加载，点击目标按钮"
        # the 825 track fields are gone from the output entirely
        assert "plan" not in raw
        assert "elements" not in raw
        assert "progress" not in raw

    def test_missing_thought_degrades_to_empty(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [make_reply(remove=["thought"])])
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_CLICK
        assert out.executable is True
        assert json.loads(out.raw_output).get("thought", "") == ""

    def test_product_mode_parses_risk_block(self, monkeypatch):
        agent, _ = patched_agent(
            monkeypatch,
            [make_reply(risk_level="high", risk_category="payment")],
        )
        out = agent.act(make_input(step=0))
        assert out.risk_level == "high"
        assert out.risk_category == "payment"

    def test_benchmark_reply_without_risk_defaults_safe(self, monkeypatch):
        agent, _ = patched_agent(
            monkeypatch, [benchmark_reply()], mode="benchmark")
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_CLICK
        assert out.risk_level == "safe"


# ---------------------------------------------------------------------------
# AC-02: full-text append-only messages trace
# ---------------------------------------------------------------------------

class TestMessagesTrace:
    def _three_steps(self, monkeypatch, replies):
        agent, stub = patched_agent(monkeypatch, replies)
        agent.act(make_input(step=0))
        agent.act(make_input(step=1, feedback=FB_CHANGED))
        agent.act(make_input(step=2, feedback=FB_NOCHANGE))
        return stub

    def test_trace_full_injection_no_truncation(self, monkeypatch):
        long_thought = "长推理" * 120  # 480 chars — far beyond the old 200 cut
        stub = self._three_steps(monkeypatch, [
            make_reply(thought=long_thought),
            make_reply(),
            make_reply(),
        ])
        # step-2 call still carries the FULL step-0 thought (assistant entry)
        assert long_thought in "\n".join(assistant_texts(stub.calls[2]))

    def test_exactly_one_image_per_call(self, monkeypatch):
        stub = self._three_steps(monkeypatch, [make_reply(), make_reply(), make_reply()])
        assert count_images(stub.calls[0]) == 1
        assert count_images(stub.calls[1]) == 1
        assert count_images(stub.calls[2]) == 1  # current screenshot only

    def test_append_only_prefix_stable(self, monkeypatch):
        stub = self._three_steps(monkeypatch, [make_reply(), make_reply(), make_reply()])
        m1, m2 = stub.calls[1], stub.calls[2]
        # m2 must repeat m1 verbatim except for m1's trailing current-user msg
        k = len(m1) - 1
        assert m2[:k] == m1[:k]
        assert m2[k]["role"] == "assistant"

    def test_feedback_becomes_user_entry(self, monkeypatch):
        stub = self._three_steps(monkeypatch, [make_reply(), make_reply(), make_reply()])
        m2 = stub.calls[2]
        roles = [m["role"] for m in m2]
        # assistant / user pairs are interleaved after the goal message
        assert roles[:4] == ["system", "user", "assistant", "user"]
        assert FB_CHANGED in all_user_text(m2)
        assert FB_NOCHANGE in all_user_text(m2)

    def test_skipped_step_not_in_trace(self, monkeypatch):
        agent, stub = patched_agent(monkeypatch, [
            make_reply(parameters={"point": [500, 500]}),  # no target -> skip
            make_reply(action="BACK", parameters={}),
        ])
        agent.act(make_input(step=0))
        agent.act(make_input(step=1))
        m1 = stub.calls[1]
        assert assistant_texts(m1) == []               # nothing was executed
        assert "页面已加载" not in all_user_text(m1)     # skipped thought is absent

    def test_goal_message_carries_instruction(self, monkeypatch):
        stub = self._three_steps(monkeypatch, [make_reply(), make_reply(), make_reply()])
        goal = stub.calls[0][1]
        assert "帮我完成任务" in goal["content"]


# ---------------------------------------------------------------------------
# CLICK target contract
# ---------------------------------------------------------------------------

class TestClickTarget:
    def test_click_without_target_skips(self, monkeypatch):
        agent, _ = patched_agent(
            monkeypatch,
            [make_reply(parameters={"point": [500, 500]})],
        )
        out = agent.act(make_input(step=0))
        assert out.executable is False
        assert out.skip_reason == "missing_target"

    def test_click_with_target_executes(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [make_reply()])
        out = agent.act(make_input(step=0))
        assert out.executable is True
        assert out.skip_reason == ""
        assert out.parameters["target"] == "确定"


# ---------------------------------------------------------------------------
# AC-06: mode switch
# ---------------------------------------------------------------------------

class TestModeSwitch:
    def test_benchmark_prompt_has_no_risk_or_memory(self, monkeypatch):
        agent, stub = patched_agent(monkeypatch, [benchmark_reply()], mode="benchmark")
        agent.act(make_input(step=0))
        sys = system_text(stub.calls[0])
        assert "risk_level" not in sys
        assert "记忆" not in sys
        assert "会话上下文" not in sys

    def test_product_prompt_contains_risk_block(self, monkeypatch):
        agent, stub = patched_agent(monkeypatch, [make_reply()], mode="product")
        agent.act(make_input(step=0))
        assert "risk_level" in system_text(stub.calls[0])

    def test_deprecated_flag_maps_to_benchmark(self, monkeypatch):
        agent = ChatGuiAgent(disable_business_overrides=True)
        stub = VLMStub([benchmark_reply()])
        monkeypatch.setattr(ChatGuiAgent, "_call_api", stub)
        agent.act(make_input(step=0))
        assert "risk_level" not in system_text(stub.calls[0])


# ---------------------------------------------------------------------------
# AC-04: three-level stuck ladder
# ---------------------------------------------------------------------------

class TestStuckLadder:
    def _run(self, monkeypatch, feedbacks, extra_replies=1):
        replies = [make_reply() for _ in feedbacks]
        replies += [make_reply() for _ in range(extra_replies)]
        agent, stub = patched_agent(monkeypatch, replies)
        outs = [
            agent.act(make_input(step=i, feedback=fb))
            for i, fb in enumerate(feedbacks)
        ]
        return agent, stub, outs

    def test_l1_symptom_injected_not_forced(self, monkeypatch):
        # counts: after N identical no-change repeats the Nth act sees the
        # symptom injected once the count reaches STUCK_SOFT_STEPS.
        fb_seq = ["", FB_NOCHANGE, FB_NOCHANGE]  # counts: 0, 0(no prev pair), 1, 2
        agent, stub, outs = self._run(monkeypatch, fb_seq, extra_replies=2)
        # 4th call: count == 2 == STUCK_SOFT_STEPS -> symptom in prompt
        out4 = agent.act(make_input(step=3, feedback=FB_NOCHANGE))
        assert "无变化" in last_user_text(stub.calls[3])
        assert out4.action == ACTION_CLICK  # not forced — model still decides

    def test_l2_forces_ask(self, monkeypatch):
        fb_seq = ["", FB_NOCHANGE, FB_NOCHANGE, FB_NOCHANGE]
        agent, stub, outs = self._run(monkeypatch, fb_seq, extra_replies=2)
        out5 = agent.act(make_input(step=4, feedback=FB_NOCHANGE))
        assert out5.action == ACTION_ASK
        assert out5.parameters.get("question")

    def test_recovery_resets_counter(self, monkeypatch):
        # One no-change pair, then the page changes -> the counter resets,
        # so the later pair only reaches L1 again — never the ASK threshold.
        fb_seq = ["", FB_NOCHANGE, FB_CHANGED, FB_NOCHANGE, FB_NOCHANGE]
        agent, stub, outs = self._run(monkeypatch, fb_seq, extra_replies=1)
        assert all(o.action == ACTION_CLICK for o in outs)  # L1 never forced
        out6 = agent.act(make_input(step=5, feedback=FB_CHANGED))
        assert out6.action == ACTION_CLICK  # counter was reset, no ASK


# ---------------------------------------------------------------------------
# WAIT (unchanged semantics from 825, now recorded in the trace)
# ---------------------------------------------------------------------------

class TestWaitAction:
    def test_wait_is_valid_action(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [
            benchmark_reply(action="WAIT", parameters={}),
        ], mode="benchmark")
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_WAIT
        assert out.executable is True
        assert out.skip_reason == ""
        assert out.risk_level == "safe"

    def test_wait_with_reason_passthrough(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [
            make_reply(action="WAIT", parameters={"reason": "页面加载中"}),
        ])
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_WAIT
        assert out.executable is True

    def test_wait_recorded_in_trace(self, monkeypatch):
        agent, stub = patched_agent(monkeypatch, [
            make_reply(action="WAIT", parameters={"reason": "页面加载中"}),
            make_reply(),
        ])
        agent.act(make_input(step=0))
        agent.act(make_input(step=1, feedback="上一步 WAIT 后页面已变化"))
        asst = assistant_texts(stub.calls[1])
        assert asst and "WAIT" in asst[0]  # WAIT is real executed history


# ---------------------------------------------------------------------------
# Degradation (unchanged) + ASK reason_code
# ---------------------------------------------------------------------------

class TestDegradation:
    def test_invalid_json_degrades_without_crash(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, ["这不是 JSON"])
        out = agent.act(make_input(step=0))
        assert isinstance(out.action, str)  # no exception, some action produced

    def test_invalid_action_becomes_skip(self, monkeypatch):
        agent, _ = patched_agent(
            monkeypatch, [make_reply(action="PAUSE", parameters={})])
        out = agent.act(make_input(step=0))
        assert out.action == "PAUSE"           # original name kept for diagnosis
        assert out.executable is False
        assert out.skip_reason == "invalid_action"

    def test_vlm_unavailable_degrades_to_skip_not_complete(self, monkeypatch):
        # review M-1 (option a): retries exhausted -> NON-executable skip,
        # never a silent COMPLETE. Repeats ride the skip-streak ladder to ASK.
        def boom(messages, **kwargs):
            raise RuntimeError("vlm down")

        monkeypatch.setattr(ChatGuiAgent, "_call_api", boom)
        agent = ChatGuiAgent()
        out = agent.act(make_input(step=0))
        assert out.executable is False
        assert out.skip_reason == "vlm_unavailable"
        assert out.action != ACTION_COMPLETE

    def test_skip_streak_ask_carries_degraded_reason_code(self, monkeypatch):
        # review m-2: the code-escalated skip-streak ASK carries its own
        # reason code so benchmark failure attribution can tell it from L2.
        replies = [make_reply(action="PAUSE", parameters={}) for _ in range(3)]
        agent, _ = patched_agent(monkeypatch, replies)
        outs = [agent.act(make_input(step=i)) for i in range(3)]
        assert outs[0].skip_reason == "invalid_action"
        assert outs[2].action == ACTION_ASK
        assert outs[2].parameters["reason_code"] == "degraded"


class TestAskReasonCode:
    def test_reason_code_passthrough(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [
            make_reply(action="ASK", parameters={
                "question": "需要验证码",
                "reason_code": "missing_info",
            }),
        ])
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_ASK
        assert out.parameters["reason_code"] == "missing_info"

    def test_missing_reason_code_tolerated(self, monkeypatch):
        agent, _ = patched_agent(monkeypatch, [
            make_reply(action="ASK", parameters={"question": "需要验证码"}),
        ])
        out = agent.act(make_input(step=0))
        assert out.action == ACTION_ASK
        assert out.executable is True
        assert "reason_code" not in out.parameters
