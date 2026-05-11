"""Simple single-stage agent for tasks that can be completed in one screen.

Skips the heavy Planner/UIExtractor split and asks the VLM to directly emit
the next action plus risk self-assessment in a single call.
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


class SimpleGuiAgent:
    """Single VLM call per step. Suitable for tasks that fit on one screen."""

    def __init__(self):
        self._api_url = os.environ.get("VLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self._model_id = os.environ.get("VLM_MODEL_ID", "doubao-seed-1-6-vision-250815")
        self._api_key = os.environ.get("VLM_API_KEY", "")
        self._client: Optional[OpenAI] = None
        self.last_ui_state: Optional[Dict] = None
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
        self._stuck_count = 0
        self._last_action_signature = ""

    def act(self, input_data: AgentInput) -> AgentOutput:
        history_text = ""
        if input_data.history_actions:
            recent = input_data.history_actions[-3:]
            history_text = f"\nrecent_actions: {json.dumps(recent, ensure_ascii=False)}"

        system_prompt = (
            "你是一个手机 GUI 自动化决策器，目标是用尽量少的步骤完成单屏可达成的任务。\n\n"
            "可用动作：CLICK / SCROLL / TYPE / OPEN / BACK / HOME / COMPLETE\n"
            "坐标使用 0-1000 归一化整数。CLICK.point 必须取自截图中真实可见元素。\n\n"
            "【风险评估 —— 必须先评估再决定】\n"
            "- risk_level：safe / medium / high\n"
            "- risk_category：payment / delete / auth / submit / communication / system / none\n"
            "- current_state / consequence / rollback_hint / risk_reason 必须填写\n"
            "- confidence：0~1，反映该动作能推进任务的把握程度\n\n"
            "输出严格 JSON，字段顺序：\n"
            "{\n"
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
                    f"current_step: {input_data.step_count}"
                    + history_text
                    + "\n请输出下一步动作。"
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
            action, params, progress = ACTION_COMPLETE, {}, ""
            risk_level, risk_category = RISK_LEVEL_SAFE, RISK_CATEGORY_NONE
            current_state = consequence = rollback_hint = risk_reason = ""
            confidence = 0.0

        action, params = self._normalize_schema(action, params)

        signature = json.dumps({"a": action, "p": params}, ensure_ascii=False, sort_keys=True)
        if signature == self._last_action_signature and action != ACTION_COMPLETE:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_action_signature = signature

        self.last_ui_state = {"elements": [], "page_type": "unknown"}

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
            confidence=confidence,
            current_subgoal_index=None,
            stuck_count=self._stuck_count,
            ui_risk_elements=[],
        )

    # ------------------------------------------------------------------
    # Helpers (mirrored from VlmGuiAgent — kept private to avoid coupling)
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
                logger.warning("Simple agent VLM call failed (attempt %d/3): %s", attempt + 1, e)
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

    def _normalize_schema(self, action: str, params: Dict) -> Tuple[str, Dict]:
        action = (action or "").upper().strip()
        alias_map = {"OPEN_APP": ACTION_OPEN, "APP_OPEN": ACTION_OPEN}
        action = alias_map.get(action, action)

        valid = {ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
                 ACTION_COMPLETE, ACTION_BACK, ACTION_HOME}
        if action not in valid:
            return ACTION_COMPLETE, {}

        if action == ACTION_CLICK:
            point = params.get("point")
            if not self._is_point(point):
                point = [500, 500]
            return ACTION_CLICK, {"point": self._clamp_point(point)}

        if action == ACTION_SCROLL:
            start = params.get("start_point")
            end = params.get("end_point")
            if not self._is_point(start) or not self._is_point(end):
                start, end = [500, 800], [500, 300]
            return ACTION_SCROLL, {
                "start_point": self._clamp_point(start),
                "end_point": self._clamp_point(end),
            }

        if action == ACTION_TYPE:
            text = params.get("text", "")
            return ACTION_TYPE, {"text": text if isinstance(text, str) else str(text)}

        if action == ACTION_OPEN:
            app_name = params.get("app_name", "")
            return ACTION_OPEN, {"app_name": app_name if isinstance(app_name, str) else str(app_name)}

        return action, {}
