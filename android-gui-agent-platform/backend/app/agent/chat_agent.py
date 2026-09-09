"""ChatGuiAgent: pure-ReAct decision loop (feature 908).

Evolved from the 825 unified-schema loop into a slim ReAct decision: each
step makes ONE VLM call that outputs only

- thought    natural-language observation + reasoning (page state, the
             task-relevant elements seen, brief plan, action rationale)
- action     one of the 9 legal actions (incl. ASK / COMPLETE / WAIT)
- parameters minimal params; CLICK carries `target` (visible text of the
             element being clicked) as the UI/diagnosis anchor

The plan/subgoals track, full-page element extraction, page_type, progress
and the CLICK-UI pre-alignment are gone: observation lives in `thought`,
and mistakes are caught by POST-HOC feedback — the caller computes a
deterministic receipt ("上步 ... 后页面已变化/无变化") from before/after
screenshots and injects it via ``AgentInput.last_step_feedback``.

History is a full-text append-only messages trace kept on the agent:

    [system, user(goal)] + (assistant(thought+action), user(receipt))* +
    [user(current: [ask_reply] [stuck symptom] + current screenshot)]

Never truncated, no historical screenshots — the only image per call is
the current one.

Stuck handling is a three-level ladder (tests/README §4.6): the agent
counts same-action+params repeats whose receipts said 无变化. At
``STUCK_SOFT_STEPS`` a symptom hint is injected so the model can
self-recover (L1); at ``ASK_ON_STUCK_STEPS`` the step is forced to ASK
(L2). The skip-streak escalation is unchanged from 825/826.

``mode="benchmark"`` trims the prompt to scoring-relevant rules only (no
risk block, no chat context / memory injection, ASK = infeasible verdict);
``disable_business_overrides=True`` remains as a deprecated alias.
"""
import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from PIL import Image

from app.agent.schemas import (
    AgentInput,
    AgentOutput,
    ACTION_ASK,
    ACTION_BACK,
    ACTION_CLICK,
    ACTION_COMPLETE,
    ACTION_HOME,
    ACTION_OPEN,
    ACTION_SCROLL,
    ACTION_TYPE,
    ACTION_WAIT,
    ALL_RISK_CATEGORIES,
    ALL_RISK_LEVELS,
    RISK_CATEGORY_NONE,
    RISK_LEVEL_SAFE,
)

logger = logging.getLogger(__name__)

# L1 soft threshold: after this many same-action repeats with no-change
# receipts a stuck symptom is injected into the current user message so the
# model can self-recover (change approach / BACK / decide to ASK itself).
STUCK_SOFT_STEPS = 2

# L2 hard threshold: after this many repeats the step is forced to ASK
# (product: waits for the user; benchmark: mapped to `infeasible`).
ASK_ON_STUCK_STEPS = 3

# After this many consecutive skipped steps (invalid action, missing params,
# ...) the agent stops waiting and asks the user for help (unchanged).
ASK_ON_SKIP_STREAK = 3

# ASK reason codes surfaced in parameters (product display / benchmark
# failure attribution). Missing code is tolerated. Code-escalated ASKs are
# distinct for failure attribution: L2 stuck ladder vs skip streak.
REASON_CODE_STUCK = "stuck"
REASON_CODE_DEGRADED = "degraded"


def _get_vlm_client() -> OpenAI:
    api_key = os.environ.get("VLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("VLM_API_KEY environment variable is not set")
    return OpenAI(
        base_url=os.environ.get("VLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        api_key=api_key,
    )


def call_vlm(system_prompt: str, user_prompt: str) -> str:
    """Single text-only VLM call, no retry. Raises on failure."""
    client = _get_vlm_client()
    resp = client.chat.completions.create(
        model=os.environ.get("VLM_MODEL_ID", "doubao-seed-1-6-vision-250815"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        extra_body={"thinking": {"type": "disabled"}},
    )
    try:
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


def _format_context(context: List[Dict[str, Any]]) -> str:
    if not context:
        return "（无）"
    lines = []
    for m in context:
        role = m.get("role", "?")
        kind = m.get("kind", "text")
        content = str(m.get("content", ""))
        lines.append(f"[{role}/{kind}] {content}")
    return "\n".join(lines)


class ChatGuiAgent:
    """Pure-ReAct agent: one slim VLM decision per step, full-text trace."""

    def __init__(self, mode: str = "product",
                 disable_business_overrides: Optional[bool] = None):
        if disable_business_overrides is not None:
            # Deprecated alias from 824/825 kept so the benchmark adapter
            # (and any external caller) keeps working during migration.
            if disable_business_overrides:
                mode = "benchmark"
        self._mode = mode if mode in ("product", "benchmark") else "product"
        self._client: Optional[OpenAI] = None
        # Append-only text trace of this turn (README §4.6):
        # [{"role": "assistant"|"user", "content": str}, ...]
        self._trace: List[Dict[str, str]] = []
        # (action, params-json) of EXECUTED steps — the stuck signal source.
        self._executed_sigs: List[Tuple[str, str]] = []
        self._nochange_count: int = 0
        self._skip_streak: int = 0

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = _get_vlm_client()
        return self._client

    def reset(self):
        self._trace = []
        self._executed_sigs = []
        self._nochange_count = 0
        self._skip_streak = 0

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def act(self, input_data: AgentInput) -> AgentOutput:
        feedback = (input_data.last_step_feedback or "").strip()
        if feedback:
            self._trace.append({"role": "user", "content": feedback})
        self._update_nochange_count(feedback)

        decision = self._decide(input_data)

        action = decision["action"]
        params = decision["parameters"]
        action, params, skip_reason = self._normalize_schema(action, params)
        if decision.get("skip_reason"):
            # VLM was unavailable (review M-1): force the non-executable
            # degradation regardless of normalization — never a silent
            # COMPLETE. The skip-streak ladder escalates on repeats.
            action = decision["action"]
            params = decision["parameters"]
            skip_reason = decision["skip_reason"]

        # L2: stalled for too long (same action, page never changed) — stop
        # guessing, ask for help; the counter restarts so a post-reply
        # recovery gets a fresh budget.
        if (not skip_reason and action not in (ACTION_ASK, ACTION_COMPLETE)
                and self._nochange_count >= ASK_ON_STUCK_STEPS):
            action = ACTION_ASK
            params = {
                "question": (
                    "任务似乎停滞了，需要你提供帮助：要不要返回上一页重试，还是换个方式？"
                ),
                "reason_code": REASON_CODE_STUCK,
            }
            skip_reason = ""
            self._nochange_count = 0

        action, params, skip_reason = self._apply_skip_streak(
            action, params, skip_reason)

        # Trace: EXECUTED steps only (ASK/COMPLETE/skip steps carry no page
        # transition worth remembering) — same contract as the 825 history.
        if not skip_reason and action not in (ACTION_ASK, ACTION_COMPLETE):
            self._trace.append({
                "role": "assistant",
                "content": self._assistant_text(decision["thought"], action, params),
            })
            self._executed_sigs.append(
                (action, json.dumps(params, sort_keys=True, ensure_ascii=False)))

        raw_payload = {
            "thought": decision["thought"],
            "action": action,
            "parameters": params,
            "risk_level": decision["risk_level"],
            "risk_category": decision["risk_category"],
            "current_state": decision["current_state"],
            "consequence": decision["consequence"],
            "rollback_hint": decision["rollback_hint"],
            "risk_reason": decision["risk_reason"],
            "confidence": decision["confidence"],
        }

        return AgentOutput(
            action=action,
            parameters=params,
            raw_output=decision["raw_fallback"] or json.dumps(raw_payload, ensure_ascii=False),
            risk_level=decision["risk_level"],
            risk_category=decision["risk_category"],
            current_state=decision["current_state"],
            consequence=decision["consequence"],
            rollback_hint=decision["rollback_hint"],
            risk_reason=decision["risk_reason"],
            confidence=0.0 if skip_reason else decision["confidence"],
            stuck_count=self._nochange_count,
            executable=not skip_reason,
            skip_reason=skip_reason,
        )

    # ------------------------------------------------------------------
    # Stuck signal
    # ------------------------------------------------------------------

    def _update_nochange_count(self, feedback: str) -> None:
        """Count same-action repeats whose receipt said the page didn't move."""
        repeated = (
            len(self._executed_sigs) >= 2
            and self._executed_sigs[-1] == self._executed_sigs[-2]
        )
        if "无变化" in feedback and repeated:
            self._nochange_count += 1
        else:
            self._nochange_count = 0

    # ------------------------------------------------------------------
    # The single decision call
    # ------------------------------------------------------------------

    def _decide(self, input_data: AgentInput) -> Dict[str, Any]:
        """One VLM call over the trace: thought + action + parameters."""
        system_prompt = self._system_prompt()

        current_text = ""
        if input_data.ask_reply:
            current_text += (
                f"【用户对上次提问的回复】\n{input_data.ask_reply}\n"
                "请优先按该回复执行，不要再问同样的问题。\n\n"
            )
        if STUCK_SOFT_STEPS <= self._nochange_count < ASK_ON_STUCK_STEPS:
            current_text += (
                f"【卡住检测】你已连续 {self._nochange_count} 次执行相同动作且页面无变化。\n"
                "请先在 thought 中分析原因，再选择：换一种动作方式（换坐标/滚动）、"
                "BACK 重置页面、或输出 ASK 请求帮助。\n\n"
            )
        current_text += "当前页面截图如下，输出本步决策 JSON。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._goal_text(input_data)},
        ]
        messages.extend(self._trace)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": current_text},
                {
                    "type": "image_url",
                    "image_url": {"url": self._encode_image(input_data.current_image)},
                },
            ],
        })

        try:
            resp = self._call_api(messages)
            raw = self._extract_text(resp)
        except Exception:
            logger.warning(
                "single decision call failed; degrading to skip "
                "(vlm_unavailable)")
            return self._fallback_decision()

        obj = self._extract_json_object(raw)
        if obj:
            action = str(obj.get("action", "")).upper().strip()
            params = obj.get("parameters", {})
            thought = str(obj.get("thought", "") or "")
            risk_level = obj.get("risk_level", RISK_LEVEL_SAFE)
            risk_category = obj.get("risk_category", RISK_CATEGORY_NONE)
            confidence = obj.get("confidence", 1.0)
            raw_fallback = ""
        else:
            action, params, thought = self._extract_action_fallback(raw)
            risk_level, risk_category = RISK_LEVEL_SAFE, RISK_CATEGORY_NONE
            confidence = 1.0
            raw_fallback = raw

        if not isinstance(params, dict):
            params = {}

        return {
            "thought": thought,
            "action": action,
            "parameters": params,
            "risk_level": self._normalize_enum(risk_level, ALL_RISK_LEVELS, RISK_LEVEL_SAFE),
            "risk_category": self._normalize_enum(risk_category, ALL_RISK_CATEGORIES, RISK_CATEGORY_NONE),
            "current_state": str(obj.get("current_state", "") if obj else ""),
            "consequence": str(obj.get("consequence", "") if obj else ""),
            "rollback_hint": str(obj.get("rollback_hint", "") if obj else ""),
            "risk_reason": str(obj.get("risk_reason", "") if obj else ""),
            "confidence": self._clamp_confidence(confidence),
            "raw_fallback": raw_fallback,
            "skip_reason": "",
        }

    def _fallback_decision(self) -> Dict[str, Any]:
        """VLM unavailable: a NON-executable WAIT-shaped skip (review M-1,
        option a). We never silently declare the task COMPLETE; consecutive
        failures ride the existing skip-streak ladder up to an ASK."""
        return {
            "thought": "",
            "action": ACTION_WAIT,
            "parameters": {},
            "risk_level": RISK_LEVEL_SAFE,
            "risk_category": RISK_CATEGORY_NONE,
            "current_state": "",
            "consequence": "",
            "rollback_hint": "",
            "risk_reason": "",
            "confidence": 0.0,
            "raw_fallback": "",
            "skip_reason": "vlm_unavailable",
        }

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        common = (
            "你是一个手机 GUI 自动化 ReAct 智能体。每一步：观察当前截图，"
            "在 thought 中推理，输出一个动作。每次只输出一个动作。\n\n"
            "【输出 JSON 字段】\n"
            "- thought：自然语言推理，必须包含：页面观察（当前是什么页面、"
            "与任务相关的关键元素及其大致位置）、简要计划（长任务在思考中自我提醒剩余步骤）、"
            "动作选择理由\n"
            "- action：CLICK / SCROLL / TYPE / OPEN / BACK / HOME / COMPLETE / ASK / WAIT\n"
            "- parameters：\n"
            "  · CLICK：{\"point\":[x,y], \"target\":\"目标元素可见文本\"}"
            "（point 为 0-1000 归一化整数，必须落在目标元素上；target 必填）\n"
            "  · SCROLL：{\"start_point\":[x,y], \"end_point\":[x,y]}\n"
            "  · TYPE：{\"text\":\"...\"}\n"
            "  · OPEN：{\"app_name\":\"应用名\"}\n"
            "  · ASK：{\"question\":\"一句中文\", \"options\":[...], "
            "\"reason_code\":\"no_target|missing_info|ambiguity|stuck\"}\n"
            "  · WAIT：{\"reason\":\"...\"}（可选）\n\n"
            "【动作语义】\n"
            "- WAIT：页面正在加载、跳转或渲染未完成时输出，系统会真实等待页面稳定\n"
            "- COMPLETE：任务目标已达成时输出\n"
            "- 对话中上一条 user 回执描述了你上个动作的结果；\"页面无变化\"意味着动作可能未生效，"
            "先分析原因再决定，不要原样重试\n"
        )
        if self._mode == "benchmark":
            return common + (
                "\n【ASK = 任务不可行判定】无人环境，输出 ASK 即判定任务无法完成并终止。"
                "仅在以下情形输出：\n"
                "- 页面明示无结果/未找到（搜索或列表为空）→ 立即输出，不要硬撑\n"
                "- 任务所需前置信息缺失（需登录/验证码/权限而环境无法提供）\n"
                "- 已尝试不同方法仍无法推进\n"
                "仅是不确定时禁止 ASK，选择最可能的动作继续执行。\n\n"
                "输出严格 JSON：\n"
                "{\"thought\":\"...\", \"action\":\"...\", \"parameters\":{...}}\n"
                "禁止输出任何额外文字。"
            )
        return common + (
            "\n【ASK = 向用户求助】仅在无法自主推进时使用（存在无法消解的歧义、"
            "需用户提供验证码等信息）；能够自主推进时禁止 ASK。"
            "任务起点通常先 OPEN 打开目标应用。\n\n"
            "【风险评估 —— 必须先评估再决定】\n"
            "- risk_level：safe / medium / high\n"
            "  · safe：浏览、滚动、返回、回到桌面、打开应用本体、输入搜索关键词、"
            "点击普通列表项、ASK、WAIT\n"
            "  · medium：进入收费/实名/授权页面之前的可返回入口动作\n"
            "  · high：不可逆或有外部影响的动作，包括：确认支付/转账、删除/清空数据、"
            "发送消息/拨号、提交不可撤销表单、授权第三方账号\n"
            "- risk_category：payment / delete / auth / submit / communication / system / none\n"
            "- current_state：一句话描述当前页面与上下文\n"
            "- consequence：执行该动作后会发生什么（包含金额、对象、影响范围等关键信息）\n"
            "- rollback_hint：如何撤销；若不可撤销，写\"不可撤销\"\n"
            "- risk_reason：为何判定为该 risk_level\n"
            "- confidence：当前决策的置信度，0~1\n\n"
            "输出严格 JSON：\n"
            "{\"thought\":\"...\", \"action\":\"...\", \"parameters\":{...}, "
            "\"risk_level\":\"safe|medium|high\", \"risk_category\":\"...\", "
            "\"current_state\":\"...\", \"consequence\":\"...\", "
            "\"rollback_hint\":\"...\", \"risk_reason\":\"...\", \"confidence\":0.0}\n"
            "禁止输出任何额外文字。"
        )

    def _goal_text(self, input_data: AgentInput) -> str:
        parts = [f"任务指令: {input_data.instruction}"]
        if self._mode == "product":
            context_text = _format_context(input_data.conversation_context)
            parts.append(f"会话上下文（本会话此前的对话，用于消解指代）：\n{context_text}")
            parts.append(f"用户长期记忆（偏好与历史路径，无冲突时遵循）：\n{input_data.memory_text or '（无）'}")
        parts.append("请逐步完成任务，每步输出一个 JSON 决策。")
        return "\n".join(parts)

    @staticmethod
    def _assistant_text(thought: str, action: str, params: Dict) -> str:
        return (
            f"{thought}\n"
            f"动作: {json.dumps({'action': action, 'parameters': params}, ensure_ascii=False)}"
        )

    # ------------------------------------------------------------------
    # API plumbing (unchanged)
    # ------------------------------------------------------------------

    def _call_api(self, messages: List[Dict], **kwargs) -> Any:
        client = self._get_client()
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                return client.chat.completions.create(
                    model=os.environ.get("VLM_MODEL_ID", "doubao-seed-1-6-vision-250815"),
                    messages=messages,
                    extra_body={"thinking": {"type": "disabled"}},
                    **kwargs,
                )
            except Exception as e:
                last_exc = e
                logger.warning("VLM API call failed (attempt %d/3): %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    def _encode_image(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def _extract_text(self, response: Any) -> str:
        try:
            return response.choices[0].message.content or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Turn summary (chat reply, unchanged purpose)
    # ------------------------------------------------------------------

    def summarize_turn(self, input_data: AgentInput) -> str:
        """One-shot summary of the finished turn. Degrades to a count-based
        fallback on any failure (no retry, keeps turn-end latency low)."""
        actions = input_data.history_actions[-15:]
        context_text = _format_context(input_data.conversation_context)
        system_prompt = (
            "你是手机 GUI 自动化助手。请用 1~3 句中文向用户总结本轮执行结果："
            "完成了什么、当前处于什么状态、有什么需要用户注意的（如未完成的部分）。"
            "直接输出总结文字，禁止 JSON 或额外说明。"
        )
        user_prompt = (
            f"本轮指令：{input_data.instruction}\n"
            f"会话上下文：\n{context_text}\n"
            f"执行动作：{json.dumps(actions, ensure_ascii=False)}"
        )
        try:
            text = call_vlm(system_prompt, user_prompt)
            if text and text.strip():
                return text.strip()
        except Exception as e:
            logger.warning("summarize_turn failed (fallback used): %s", e)
        return f"本轮共执行了 {len(actions)} 步操作。"

    # ------------------------------------------------------------------
    # Post-processing and normalization
    # ------------------------------------------------------------------

    def _normalize_schema(self, action: str, params: Dict) -> Tuple[str, Dict, str]:
        action = (action or "").upper().strip()
        alias_map = {"OPEN_APP": ACTION_OPEN, "APP_OPEN": ACTION_OPEN,
                     "ASK_USER": ACTION_ASK, "QUESTION": ACTION_ASK}
        action = alias_map.get(action, action)

        valid = {ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
                 ACTION_COMPLETE, ACTION_BACK, ACTION_HOME, ACTION_ASK,
                 ACTION_WAIT}
        if action not in valid:
            # Degrade to a skip (one quiet round, the semantics of the
            # official `wait` action) keeping the original name for
            # diagnosis — never silently declare completion.
            return action, {}, "invalid_action"

        if action == ACTION_ASK:
            question = str(params.get("question", "") or "").strip()
            if not question:
                # Same degradation as missing_point / missing_text: skip
                # this round, do not silently declare completion.
                return ACTION_ASK, {}, "missing_question"
            clean: Dict[str, Any] = {"question": question}
            options = params.get("options")
            if isinstance(options, list):
                clean["options"] = [str(o) for o in options if str(o).strip()]
            reason_code = str(params.get("reason_code", "") or "").strip()
            if reason_code:
                clean["reason_code"] = reason_code
            return ACTION_ASK, clean, ""

        if action == ACTION_CLICK:
            point = params.get("point")
            target = str(params.get("target", "") or "").strip()
            if not self._is_point(point):
                return ACTION_CLICK, {}, "missing_point"
            if not target:
                # feature 908: `target` is the diagnosis/UI anchor of a click
                return ACTION_CLICK, {}, "missing_target"
            return ACTION_CLICK, {"point": self._clamp_point(point), "target": target}, ""

        if action == ACTION_SCROLL:
            start = params.get("start_point")
            end = params.get("end_point")
            if not self._is_point(start) or not self._is_point(end):
                start, end = [500, 800], [500, 300]
            return ACTION_SCROLL, {
                "start_point": self._clamp_point(start),
                "end_point": self._clamp_point(end),
            }, ""

        if action == ACTION_TYPE:
            text = params.get("text", "")
            if not isinstance(text, str):
                text = str(text)
            if not text:
                return ACTION_TYPE, {"text": ""}, "missing_text"
            return ACTION_TYPE, {"text": text}, ""

        if action == ACTION_OPEN:
            app_name = params.get("app_name", "")
            if not isinstance(app_name, str):
                app_name = str(app_name)
            if not app_name.strip():
                return ACTION_OPEN, {"app_name": ""}, "missing_app_name"
            return ACTION_OPEN, {"app_name": app_name}, ""

        # WAIT (and any future parameterless action): no mandatory params;
        # an optional `reason` is passed through for display/diagnosis.
        if action == ACTION_WAIT:
            reason = str(params.get("reason", "") or "").strip()
            return (ACTION_WAIT, {"reason": reason} if reason else {}, "")

        return action, {}, ""

    def _apply_skip_streak(
        self, action: str, params: Dict, skip_reason: str
    ) -> Tuple[str, Dict, str]:
        """Track consecutive skips; escalate to ASK when the streak grows.

        An executable action resets the streak. ASK_ON_SKIP_STREAK
        consecutive skips turn into an ASK for help (product: waits for the
        user's reply; benchmark: mapped to `infeasible` by the adapter), and
        the streak restarts so a post-reply recovery gets a fresh budget.
        """
        if not skip_reason:
            self._skip_streak = 0
            return action, params, skip_reason

        self._skip_streak += 1
        if self._skip_streak < ASK_ON_SKIP_STREAK:
            return action, params, skip_reason

        self._skip_streak = 0
        return (
            ACTION_ASK,
            {
                "question": (
                    "连续几步都无法执行有效操作，需要你帮助："
                    "要不要返回上一页重试，还是换个方式？"
                ),
                "reason_code": REASON_CODE_DEGRADED,
            },
            "",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_action_fallback(self, text: str) -> Tuple[str, Dict, str]:
        action = ""
        params: Dict[str, Any] = {}
        thought = ""

        m = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if m:
            action = m.group(1).upper().strip()

        m = re.search(r'"parameters"\s*:\s*(\{)', text)
        if m:
            start = m.start(1)
            depth = 0
            for i, ch in enumerate(text[start:]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            params = json.loads(text[start: start + i + 1])
                        except Exception:
                            pass
                        break

        m = re.search(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            thought = m.group(1)

        return action, params, thought

    def _extract_json_object(self, text: str) -> Optional[Dict]:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start: end + 1])
        except Exception:
            return None

    def _is_point(self, point: Any) -> bool:
        return (
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(v, (int, float)) for v in point)
        )

    def _clamp_point(self, point: List[Any]) -> List[int]:
        return [
            max(0, min(1000, int(float(point[0])))),
            max(0, min(1000, int(float(point[1])))),
        ]

    def _normalize_enum(self, value: Any, allowed: List[str], default: str) -> str:
        v = str(value or "").strip().lower()
        return v if v in allowed else default

    def _clamp_confidence(self, value: Any) -> float:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return 1.0
        if f != f:  # NaN
            return 1.0
        return max(0.0, min(1.0, f))
