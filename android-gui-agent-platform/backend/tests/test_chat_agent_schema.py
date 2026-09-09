"""Contract tests: ChatGuiAgent action-schema normalization (feature 826).

Locks the silent-COMPLETE removal in ``_normalize_schema``:
- actions outside the valid set degrade to a skip (``invalid_action``),
  keeping the original action name for diagnosis;
- ASK with an empty question degrades to ``missing_question`` (aligned with
  the existing ``missing_point`` / ``missing_text`` branches);
- consecutive skips escalate to ASK after ``ASK_ON_SKIP_STREAK`` rounds
  (product: ask the user; benchmark: infeasible via the adapter mapping).

Pure post-processing contracts — no VLM calls involved.
"""
import pytest

from app.agent.chat_agent import ASK_ON_SKIP_STREAK, ChatGuiAgent
from app.agent.schemas import ACTION_ASK, ACTION_CLICK, ACTION_COMPLETE


def make_agent() -> ChatGuiAgent:
    # disable_business_overrides keeps _postprocess_action a no-op so these
    # tests exercise _normalize_schema / _apply_skip_streak in isolation.
    return ChatGuiAgent(disable_business_overrides=True)


class TestNormalizeSchema:
    def test_invalid_action_becomes_skip_not_complete(self):
        agent = make_agent()
        # PAUSE stands in for any out-of-vocabulary action (WAIT became a
        # legal action in feature 825, so it can no longer serve as the
        # invalid example here).
        action, params, skip = agent._normalize_schema("PAUSE", {})
        assert action == "PAUSE"  # original name preserved for diagnosis
        assert params == {}
        assert skip == "invalid_action"
        assert action != ACTION_COMPLETE

    def test_known_alias_still_normalized(self):
        agent = make_agent()
        action, _, skip = agent._normalize_schema("OPEN_APP", {"app_name": "chrome"})
        assert action == "OPEN"
        assert skip == ""

    def test_ask_with_question_still_valid(self):
        agent = make_agent()
        action, params, skip = agent._normalize_schema(
            "ASK", {"question": "哪一天出发?", "options": ["今天", "明天"]}
        )
        assert action == ACTION_ASK
        assert params["question"] == "哪一天出发?"
        assert params["options"] == ["今天", "明天"]
        assert skip == ""

    @pytest.mark.parametrize(
        "params", [{}, {"question": ""}, {"question": "   "}],
        ids=["missing", "empty", "blank"],
    )
    def test_ask_empty_question_becomes_skip_not_complete(self, params):
        agent = make_agent()
        action, params_out, skip = agent._normalize_schema("ASK", params)
        assert action == ACTION_ASK
        assert params_out == {}
        assert skip == "missing_question"
        assert action != ACTION_COMPLETE


class TestSkipStreak:
    def test_consecutive_skips_escalate_to_ask(self):
        agent = make_agent()
        # Below the threshold every round stays a plain skip.
        for _ in range(ASK_ON_SKIP_STREAK - 1):
            action, _, skip = agent._apply_skip_streak("WAIT", {}, "invalid_action")
            assert skip == "invalid_action"
            assert action == "WAIT"
        # The ASK_ON_SKIP_STREAK-th consecutive skip escalates to ASK.
        action, params, skip = agent._apply_skip_streak("WAIT", {}, "invalid_action")
        assert action == ACTION_ASK
        assert params.get("question")  # fixed help question, non-empty
        assert skip == ""

    def test_streak_resets_on_executable_action(self):
        agent = make_agent()
        agent._apply_skip_streak("WAIT", {}, "invalid_action")
        agent._apply_skip_streak("WAIT", {}, "invalid_action")
        action, _, skip = agent._apply_skip_streak(
            ACTION_CLICK, {"point": [100, 100]}, ""
        )
        assert action == ACTION_CLICK
        assert skip == ""
        # Counter restarted: two more skips must not escalate yet.
        action, _, skip = agent._apply_skip_streak("WAIT", {}, "invalid_action")
        assert skip == "invalid_action"
        action, _, skip = agent._apply_skip_streak("WAIT", {}, "invalid_action")
        assert skip == "invalid_action"
        assert action == "WAIT"

    def test_reset_clears_streak(self):
        agent = make_agent()
        agent._apply_skip_streak("WAIT", {}, "invalid_action")
        agent._apply_skip_streak("WAIT", {}, "invalid_action")
        agent.reset()
        # After reset a single skip is round 1/ASK_ON_SKIP_STREAK again.
        action, _, skip = agent._apply_skip_streak("WAIT", {}, "invalid_action")
        assert skip == "invalid_action"
        assert action == "WAIT"
