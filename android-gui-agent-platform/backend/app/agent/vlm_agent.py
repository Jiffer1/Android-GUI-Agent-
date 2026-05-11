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

_EMPTY_UI_STATE: Dict = {"elements": [], "page_type": "unknown"}


class VlmGuiAgent:
    """Three-stage VLM-based GUI agent: Planner -> UIExtractor -> ActionAnalyzer."""

    def __init__(self):
        self._api_url = os.environ.get("VLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self._model_id = os.environ.get("VLM_MODEL_ID", "doubao-seed-1-6-vision-250815")
        self._api_key = os.environ.get("VLM_API_KEY", "")
        self._client: Optional[OpenAI] = None
        self.subgoals: List[str] = []
        self.last_ui_state: Optional[Dict] = None
        self.progress: str = ""
        self._stuck_count: int = 0
        self._last_progress: str = ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("VLM_API_KEY environment variable is not set")
            self._client = OpenAI(base_url=self._api_url, api_key=self._api_key)
        return self._client

    def reset(self):
        self.subgoals = []
        self.last_ui_state = None
        self.progress = ""
        self._stuck_count = 0
        self._last_progress = ""

    def act(self, input_data: AgentInput) -> AgentOutput:
        if not self.subgoals:
            subgoals, app_name, planner_raw = self._run_planner(input_data)
            self.subgoals = subgoals
        else:
            app_name = ""
            planner_raw = ""

        # Track stuck state before updating progress
        if self.progress and self.progress == self._last_progress:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_progress = self.progress

        ui_state = self._extract_ui(input_data)
        analyzer = self._analyze_action(input_data, ui_state)
        action = analyzer["action"]
        params = analyzer["parameters"]
        progress = analyzer["progress"]
        raw = analyzer["raw"]
        action, params = self._postprocess_action(input_data, ui_state, action, params, app_name)
        action, params = self._normalize_schema(action, params)
        self.progress = progress
        self.last_ui_state = ui_state

        combined_raw = (
            json.dumps(
                {
                    "planner": self._extract_json_object(planner_raw) or planner_raw,
                    "analyzer": self._extract_json_object(raw) or raw,
                },
                ensure_ascii=False,
            )
            if planner_raw
            else raw
        )

        ui_risk_elements = [
            {"text": el.get("text", ""), "point": el.get("point", [])}
            for el in ui_state.get("elements", [])
            if el.get("high_risk")
        ]

        return AgentOutput(
            action=action,
            parameters=params,
            raw_output=combined_raw,
            risk_level=analyzer["risk_level"],
            risk_category=analyzer["risk_category"],
            current_state=analyzer["current_state"],
            consequence=analyzer["consequence"],
            rollback_hint=analyzer["rollback_hint"],
            risk_reason=analyzer["risk_reason"],
            confidence=analyzer["confidence"],
            current_subgoal_index=analyzer["current_subgoal_index"],
            stuck_count=self._stuck_count,
            ui_risk_elements=ui_risk_elements,
        )

    # ------------------------------------------------------------------
    # API
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
    # Module 1 — Planner
    # ------------------------------------------------------------------

    def _run_planner(self, input_data: AgentInput) -> Tuple[List[str], str, str]:
        system_prompt = (
            "你是一个任务规划器。根据用户任务，输出：\n"
            "1. 抽象子目标序列\n"
            "2. 需要打开的应用名称\n\n"
            "规划要求：\n"
            "- 子目标必须严格按照用户任务描述的语义顺序排列，不得跳步或倒序\n"
            "- 子目标只描述阶段目标，不含按钮名称或坐标\n"
            "- 禁止同义重复或拆分重复：同一个目标不要拆成两个等价步骤\n"
            "- 输入并选择候选属于同一阶段时，应合并为一个子目标（例如：搜索并选择地点）\n"
            "- 对有先后依赖的任务，必须完成前一对象后再处理后一对象\n\n"
            "只输出严格 JSON，格式：\n"
            "{\"app_name\":\"应用名\",\"subgoals\":[\"子目标1\",\"子目标2\"]}\n"
            "禁止输出任何额外文字。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"任务：{input_data.instruction}"},
        ]
        try:
            resp = self._call_api(messages)
            raw = self._extract_text(resp)
            obj = self._extract_json_object(raw) or {}
            subgoals = obj.get("subgoals", [])
            if not isinstance(subgoals, list) or not subgoals:
                raise ValueError("empty subgoals")
            app_name = obj.get("app_name", "")
        except Exception:
            subgoals = [input_data.instruction]
            app_name = ""
            raw = ""

        self.subgoals = subgoals
        raw = json.dumps({"subgoals": subgoals, "app_name": app_name}, ensure_ascii=False)
        return subgoals, app_name, raw

    # ------------------------------------------------------------------
    # Module 2 — UIExtractor
    # ------------------------------------------------------------------

    def _extract_ui(self, input_data: AgentInput) -> Dict:
        system_prompt = (
            "你是一个移动端界面解析器。\n"
            "任务：结合用户 instruction 与 planner_subgoals，从当前截图中提取结构化 UI 信息。\n\n"
            "输出严格 JSON，格式：\n"
            "{\n"
            "  \"elements\": [{\n"
            "    \"text\":\"...\",\n"
            "    \"type\":\"button|input|list_item|icon|tab|...\",\n"
            "    \"point\":[x,y],\n"
            "    \"selected\":false,\n"
            "    \"high_risk\":false\n"
            "  }],\n"
            "  \"page_type\": \"home|search|result|detail|form|payment|loading|...\"\n"
            "}\n\n"
            "字段说明：\n"
            "- 只提取与 instruction 或 planner_subgoals 相关的可交互元素\n"
            "- page_type 若页面正在加载（转圈、骨架屏、进度条），设为 loading\n"
            "- point 为 0-1000 归一化整数坐标\n"
            "- 最多返回 20 个最重要且与任务相关的可交互元素\n"
            "- high_risk：触发后会产生不可逆高影响操作（如立即支付、立即呼叫），设为 true\n"
            "- 禁止输出额外文字"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"instruction: {input_data.instruction}\n"
                            f"planner_subgoals: {json.dumps(self.subgoals, ensure_ascii=False)}\n"
                            "仅提取与 instruction 或 planner_subgoals 相关的可交互元素。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": self._encode_image(input_data.current_image)},
                    },
                ],
            },
        ]
        try:
            resp = self._call_api(messages)
            raw = self._extract_text(resp)
            obj = self._extract_json_object(raw)
            if not isinstance(obj, dict):
                raise ValueError("not a dict")
            elements = obj.get("elements", [])
            if not isinstance(elements, list):
                elements = []
            clean: List[Dict] = []
            for el in elements:
                if not isinstance(el, dict):
                    continue
                point = el.get("point")
                if not self._is_point(point):
                    continue
                clean.append({
                    "text": str(el.get("text", "")),
                    "type": str(el.get("type", "")),
                    "point": self._clamp_point(point),
                    "selected": bool(el.get("selected", False)),
                    "high_risk": bool(el.get("high_risk", False)),
                })
                if len(clean) >= 20:
                    break
            return {"elements": clean, "page_type": str(obj.get("page_type", "unknown"))}
        except Exception:
            return dict(_EMPTY_UI_STATE)

    # ------------------------------------------------------------------
    # Module 3 — ActionAnalyzer
    # ------------------------------------------------------------------

    def _analyze_action(self, input_data: AgentInput, ui_state: Dict) -> Dict[str, Any]:
        recovery_hint = ""
        if self._stuck_count >= 2:
            recovery_hint = (
                "\n【当前状态异常】\n"
                f"- 已连续 {self._stuck_count} 步进度未变化，当前状态可能与预期不符\n"
                "- 请优先尝试 BACK 返回上一页，或 HOME 回到桌面重新进入应用\n"
                "- 若已在正确页面但操作无效，尝试 SCROLL 后重新寻找目标元素\n"
            )

        history_text = ""
        if input_data.history_actions:
            recent = input_data.history_actions[-5:]
            history_text = f"\nrecent_actions: {json.dumps(recent, ensure_ascii=False)}"

        system_prompt = (
            "你是一个手机 GUI 自动化决策器，同时也是自身决策的风险评估器。\n\n"
            "决策依据：\n"
            "1. 以 instruction 为最终目标，判断任务是否完成\n"
            "2. previous_progress 是上一轮状态摘要，必须作为当前决策的连续上下文优先参考\n"
            "3. recent_actions 是最近几步的实际执行记录，用于判断当前状态是否符合预期\n"
            "4. subgoals 是规划器对任务步骤的参考预测，仅供理解任务结构，不作为执行约束\n"
            "5. 结合当前截图和 ui_elements 决定下一步操作\n\n"
            "【任务完成判断】\n"
            "- 当前页面出现敏感项（如立即呼叫、立即支付、立即付款）时直接输出 COMPLETE\n"
            "- previous_progress 已明确显示任务完成时直接输出 COMPLETE\n\n"
            "【执行顺序约束】\n"
            "- 按 instruction 语义顺序推进，不要跳步\n"
            "- CLICK 的 point 必须来自 ui_elements\n"
            "- 坐标为 0-1000 归一化整数\n"
            "- high_risk=true 的元素默认禁止点击\n\n"
            "【进度摘要】\n"
            "- 每一步都必须输出 progress，一句中文\n"
            "- progress 必须包含：已完成内容 + 当前页面状态 + 下一步目标\n\n"
            "【风险评估 —— 必须先评估再决定】\n"
            "- risk_level 取值：safe / medium / high\n"
            "  · safe：浏览、滚动、返回、回到桌面、打开应用等可随时撤销的操作\n"
            "  · medium：进入未知页面、点击 high_risk=true 但用途不明的元素、提交可撤销的表单\n"
            "  · high：不可逆或有外部影响的动作\n"
            "- risk_category 取值：payment / delete / auth / submit / communication / system / none\n"
            "  · payment：支付、确认支付、立即付款、立即购买、转账\n"
            "  · delete：删除、清空、移除\n"
            "  · auth：授权、登录、绑定第三方账号\n"
            "  · submit：发布、提交订单等不可撤销的提交类\n"
            "  · communication：发送消息、拨打电话、一键呼叫\n"
            "  · system：卸载、格式化、清除应用数据、root\n"
            "  · none：risk_level=safe 时填 none\n"
            "- current_state：一句话描述当前页面与上下文（让人类操作员能快速理解局势）\n"
            "- consequence：执行该动作后会发生什么（包含金额、对象、影响范围等关键信息）\n"
            "- rollback_hint：如何撤销；若不可撤销，写\"不可撤销\"\n"
            "- risk_reason：为何判定为该 risk_level\n"
            "- confidence：当前决策的置信度，0~1 之间的小数；含义=该动作能推进任务的把握程度\n"
            "- current_subgoal_index：当前正在执行的 subgoals 下标（0 起），若无法判断填 null\n"
            + recovery_hint
            + "\n输出严格 JSON：\n"
            "{\n"
            "  \"action\":\"...\",\n"
            "  \"parameters\":{...},\n"
            "  \"progress\":\"...\",\n"
            "  \"risk_level\":\"safe|medium|high\",\n"
            "  \"risk_category\":\"payment|delete|auth|submit|communication|system|none\",\n"
            "  \"current_state\":\"...\",\n"
            "  \"consequence\":\"...\",\n"
            "  \"rollback_hint\":\"...\",\n"
            "  \"risk_reason\":\"...\",\n"
            "  \"confidence\":0.0,\n"
            "  \"current_subgoal_index\":0\n"
            "}\n"
            "禁止输出额外文字。"
        )

        user_content: List[Any] = [
            {
                "type": "text",
                "text": (
                    f"instruction: {input_data.instruction}\n"
                    f"subgoals: {json.dumps(self.subgoals, ensure_ascii=False)}\n"
                    f"page_type: {ui_state.get('page_type', 'unknown')}\n"
                    f"ui_elements: {json.dumps(ui_state.get('elements', []), ensure_ascii=False)}\n"
                    f"previous_progress: {self.progress or 'None'}\n"
                    f"current_step: {input_data.step_count}"
                    + history_text
                    + "\nReturn the best next action."
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
            obj = self._extract_json_object(raw)
            if obj:
                action = str(obj.get("action", "")).upper().strip()
                params = obj.get("parameters", {})
                progress = obj.get("progress", "")
                risk_level = obj.get("risk_level", RISK_LEVEL_SAFE)
                risk_category = obj.get("risk_category", RISK_CATEGORY_NONE)
                current_state = obj.get("current_state", "")
                consequence = obj.get("consequence", "")
                rollback_hint = obj.get("rollback_hint", "")
                risk_reason = obj.get("risk_reason", "")
                confidence = obj.get("confidence", 1.0)
                current_subgoal_index = obj.get("current_subgoal_index", None)
            else:
                action, params, progress = self._extract_action_fallback(raw)
                risk_level, risk_category = RISK_LEVEL_SAFE, RISK_CATEGORY_NONE
                current_state = consequence = rollback_hint = risk_reason = ""
                confidence = 1.0
                current_subgoal_index = None

            if not isinstance(params, dict):
                params = {}
            if not isinstance(progress, str):
                progress = str(progress)

            risk_level = self._normalize_enum(risk_level, ALL_RISK_LEVELS, RISK_LEVEL_SAFE)
            risk_category = self._normalize_enum(risk_category, ALL_RISK_CATEGORIES, RISK_CATEGORY_NONE)
            confidence = self._clamp_confidence(confidence)
            subgoal_index = self._normalize_subgoal_index(current_subgoal_index)

            return {
                "action": action,
                "parameters": params,
                "progress": progress,
                "raw": raw,
                "risk_level": risk_level,
                "risk_category": risk_category,
                "current_state": str(current_state or ""),
                "consequence": str(consequence or ""),
                "rollback_hint": str(rollback_hint or ""),
                "risk_reason": str(risk_reason or ""),
                "confidence": confidence,
                "current_subgoal_index": subgoal_index,
            }
        except Exception:
            return {
                "action": ACTION_COMPLETE,
                "parameters": {},
                "progress": self.progress,
                "raw": "",
                "risk_level": RISK_LEVEL_SAFE,
                "risk_category": RISK_CATEGORY_NONE,
                "current_state": "",
                "consequence": "",
                "rollback_hint": "",
                "risk_reason": "",
                "confidence": 0.0,
                "current_subgoal_index": None,
            }

    # ------------------------------------------------------------------
    # Post-processing and normalization
    # ------------------------------------------------------------------

    def _postprocess_action(
        self,
        input_data: AgentInput,
        ui_state: Dict,
        action: str,
        params: Dict,
        app_name: str = "",
    ) -> Tuple[str, Dict]:
        normalized = (action or "").upper().strip()

        if self._has_final_confirmation_element(ui_state):
            return ACTION_COMPLETE, {}

        if self._is_initial_page(ui_state) and app_name:
            return ACTION_OPEN, {"app_name": app_name}

        return normalized, params if isinstance(params, dict) else {}

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_initial_page(self, ui_state: Dict) -> bool:
        return str(ui_state.get("page_type", "")).strip().lower() in {
            "home", "initial", "init", "launcher"
        }

    def _has_final_confirmation_element(self, ui_state: Dict) -> bool:
        keywords = ("立即呼叫", "立即支付", "立即付款")
        for el in ui_state.get("elements", []):
            if any(kw in str(el.get("text", "")) for kw in keywords):
                return True
        return False

    def _extract_action_fallback(self, text: str) -> Tuple[str, Dict, str]:
        action = ""
        params: Dict = {}
        progress = ""

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

        m = re.search(r'"progress"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            progress = m.group(1)

        return action, params, progress

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

    def _normalize_subgoal_index(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            i = int(value)
        except (TypeError, ValueError):
            return None
        if i < 0:
            return None
        if self.subgoals and i >= len(self.subgoals):
            return len(self.subgoals) - 1
        return i
