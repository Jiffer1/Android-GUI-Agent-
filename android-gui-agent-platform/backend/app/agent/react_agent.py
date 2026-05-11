"""ReAct-style agent: think -> observe -> compare candidates -> act.

Used as the escalation target when the Standard (VlmGuiAgent) path gets stuck,
finds no element, repeats actions, sees no page change, or reports low
confidence. The output schema is identical to the other agents so the runtime
loop and HITL machinery do not change.
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
    ACTION_BACK,
    ACTION_CLICK,
    ACTION_COMPLETE,
    ACTION_HOME,
    ACTION_OPEN,
    ACTION_SCROLL,
    ACTION_TYPE,
    ALL_RISK_CATEGORIES,
    ALL_RISK_LEVELS,
    RISK_CATEGORY_NONE,
    RISK_LEVEL_SAFE,
)

logger = logging.getLogger(__name__)


class ReactGuiAgent:
    """Reasoning-Observation-Action loop. One model call per step but with an
    explicit thinking trace and candidate comparison in the prompt.
    """

    def __init__(self):
        self._api_url = os.environ.get("VLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self._model_id = os.environ.get("VLM_MODEL_ID", "doubao-seed-1-6-vision-250815")
        self._api_key = os.environ.get("VLM_API_KEY", "")
        self._client: Optional[OpenAI] = None
        self.last_ui_state: Optional[Dict] = None
        self._thought_history: List[str] = []
        self._stuck_count: int = 0
        self._last_action_signature: str = ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("VLM_API_KEY environment variable is not set")
            self._client = OpenAI(base_url=self._api_url, api_key=self._api_key)
        return self._client

    def reset(self):
        self.last_ui_state = None
        self._thought_history = []
        self._stuck_count = 0
        self._last_action_signature = ""

    def adopt(self, prior_state: Dict[str, Any]) -> None:
        """Take over from another agent and preserve relevant context."""
        self.last_ui_state = prior_state.get("last_ui_state")
        self._stuck_count = int(prior_state.get("stuck_count", 0) or 0)

    def act(self, input_data: AgentInput) -> AgentOutput:
        history_text = ""
        if input_data.history_actions:
            recent = input_data.history_actions[-5:]
            history_text = f"\nrecent_actions: {json.dumps(recent, ensure_ascii=False)}"

        thought_text = ""
        if self._thought_history:
            thought_text = f"\nprior_thoughts: {json.dumps(self._thought_history[-3:], ensure_ascii=False)}"

        system_prompt = (
            "你是一个在异常状态下接手的 GUI 决策器，必须更仔细地推理。\n\n"
            "工作流程：\n"
            "1. observation：先描述当前截图的关键内容（页面类型、显著元素、与任务的关系）\n"
            "2. analysis：分析为什么之前的尝试可能失败 / 当前最可能的目标位置\n"
            "3. candidates：列出 2~3 个候选动作并比较得失\n"
            "4. decision：选择最稳妥的一个动作\n\n"
            "可用动作：CLICK / SCROLL / TYPE / OPEN / BACK / HOME / COMPLETE\n"
            "坐标使用 0-1000 归一化整数。\n\n"
            "异常恢复优先策略：\n"
            "- 若 recent_actions 显示重复点击同一区域且无效，改为 BACK 或 SCROLL\n"
            "- 若 prior_thoughts 中已经指出无法找到目标，考虑 BACK 或 HOME 重新进入\n"
            "- 若画面长期未变，尝试 SCROLL 寻找隐藏元素\n\n"
            "【风险评估】\n"
            "- risk_level：safe / medium / high\n"
            "  · safe：浏览、滚动、返回、回到桌面、打开应用本体、输入搜索关键词、点击普通列表项\n"
            "  · medium：仅限进入收费/实名/授权页面之前的可返回入口动作（弹窗尚未出现，仍可 BACK 撤销）\n"
            "  · high：不可逆或有外部影响的动作：确认支付/转账、删除/清空数据、发送消息/拨号、提交不可撤销表单、授权第三方账号、卸载/清除应用数据\n"
            "- risk_category / current_state / consequence / rollback_hint / risk_reason 必填\n"
            "- confidence：综合候选比较得出的把握程度\n\n"
            "输出严格 JSON：\n"
            "{\n"
            "  \"thought\":\"observation+analysis+candidates+decision 的浓缩自然语言\",\n"
            "  \"action\":\"...\",\n"
            "  \"parameters\":{...},\n"
            "  \"progress\":\"...\",\n"
            "  \"risk_level\":\"safe|medium|high\",\n"
            "  \"risk_category\":\"...\",\n"
            "  \"current_state\":\"...\",\n"
            "  \"consequence\":\"...\",\n"
            "  \"rollback_hint\":\"...\",\n"
            "  \"risk_reason\":\"...\",\n"
            "  \"confidence\":0.0\n"
            "}\n"
            "禁止额外文字。"
        )

        user_content: List[Any] = [
            {
                "type": "text",
                "text": (
                    f"instruction: {input_data.instruction}\n"
                    f"current_step: {input_data.step_count}\n"
                    f"stuck_count: {self._stuck_count}"
                    + history_text
                    + thought_text
                    + "\n请按工作流程推理并输出下一步动作。"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": self._encode_image(input_data.current_image)},
            },
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            resp = self._call_api(messages)
            raw = self._extract_text(resp)
            obj = self._extract_json_object(raw) or {}
            thought = str(obj.get("thought", ""))
            action = str(obj.get("action", "")).upper().strip()
            params = obj.get("parameters", {}) if isinstance(obj.get("parameters"), dict) else {}
            progress = str(obj.get("progress", ""))
            risk_level = self._normalize_enum(obj.get("risk_level"), ALL_RISK_LEVELS, RISK_LEVEL_SAFE)
            risk_category = self._normalize_enum(obj.get("risk_category"), ALL_RISK_CATEGORIES, RISK_CATEGORY_NONE)
            current_state = str(obj.get("current_state", ""))
            consequence = str(obj.get("consequence", ""))
            rollback_hint = str(obj.get("rollback_hint", ""))
            risk_reason = str(obj.get("risk_reason", ""))
            confidence = self._clamp_confidence(obj.get("confidence", 1.0))
        except Exception:
            raw = ""
            thought = ""
            action, params, progress = ACTION_COMPLETE, {}, ""
            risk_level, risk_category = RISK_LEVEL_SAFE, RISK_CATEGORY_NONE
            current_state = consequence = rollback_hint = risk_reason = ""
            confidence = 0.0

        if thought:
            self._thought_history.append(thought)

        action, params, skip_reason = self._normalize_schema(action, params)

        signature = json.dumps({"a": action, "p": params}, ensure_ascii=False, sort_keys=True)
        if signature == self._last_action_signature and action != ACTION_COMPLETE:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_action_signature = signature

        return AgentOutput(
            action=action,
            parameters=params,
            raw_output=raw,
            risk_level=risk_level,
            risk_category=risk_category,
            current_state=current_state,
            consequence=consequence,
            rollback_hint=rollback_hint,
            risk_reason=risk_reason,
            confidence=0.0 if skip_reason else confidence,
            current_subgoal_index=None,
            stuck_count=self._stuck_count,
            ui_risk_elements=[],
            executable=not skip_reason,
            skip_reason=skip_reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call_api(self, messages: List[Dict], **kwargs) -> Any:
        client = self._get_client()
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                return client.chat.completions.create(
                    model=self._model_id,
                    messages=messages,
                    extra_body={"thinking": {"type": "disabled"}},
                    **kwargs,
                )
            except Exception as e:
                last_exc = e
                logger.warning("React agent VLM call failed (attempt %d/3): %s", attempt + 1, e)
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

    def _extract_json_object(self, text: str) -> Optional[Dict]:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start: end + 1])
        except Exception:
            return None

    def _normalize_enum(self, value: Any, allowed: List[str], default: str) -> str:
        v = str(value or "").strip().lower()
        return v if v in allowed else default

    def _clamp_confidence(self, value: Any) -> float:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return 1.0
        if f != f:
            return 1.0
        return max(0.0, min(1.0, f))

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

    def _normalize_schema(self, action: str, params: Dict) -> Tuple[str, Dict, str]:
        action = (action or "").upper().strip()
        alias_map = {"OPEN_APP": ACTION_OPEN, "APP_OPEN": ACTION_OPEN}
        action = alias_map.get(action, action)

        valid = {ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
                 ACTION_COMPLETE, ACTION_BACK, ACTION_HOME}
        if action not in valid:
            return ACTION_COMPLETE, {}, ""

        if action == ACTION_CLICK:
            point = params.get("point")
            if not self._is_point(point):
                return ACTION_CLICK, {}, "missing_point"
            return ACTION_CLICK, {"point": self._clamp_point(point)}, ""

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

        return action, {}, ""
