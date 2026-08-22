"""ChatGuiAgent: conversation-mode GUI agent (evolved from VlmGuiAgent).

Keeps the three-stage skeleton (Planner -> UIExtractor -> ActionAnalyzer)
plus the JSON tolerance, coordinate normalization and risk self-assessment
of the original implementation, and adds:

- conversation_context / memory_text injection into the Planner and Analyzer
- ask_reply injection (the user's answer to the agent's last ASK)
- ACTION_ASK output when information is missing or progress has stalled
- summarize_turn(): the natural-language reply posted to the chat at turn end
- module-level call_vlm() for text-only VLM calls (memory extractor, …)
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
    ALL_RISK_CATEGORIES,
    ALL_RISK_LEVELS,
    RISK_CATEGORY_NONE,
    RISK_LEVEL_SAFE,
)

logger = logging.getLogger(__name__)

_EMPTY_UI_STATE: Dict = {"elements": [], "page_type": "unknown"}

# After this many no-progress steps the agent stops guessing and asks the user.
ASK_ON_STUCK_STEPS = 3


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
    """Conversation-mode agent: Planner -> UIExtractor -> ActionAnalyzer."""

    def __init__(self):
        self._client: Optional[OpenAI] = None
        self.subgoals: List[str] = []
        self.last_ui_state: Optional[Dict] = None
        self.progress: str = ""
        self._stuck_count: int = 0
        self._last_progress: str = ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = _get_vlm_client()
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
        action, params, skip_reason = self._normalize_schema(action, params)

        # Stalled for too long: stop guessing, ask the user for help.
        if (not skip_reason and action not in (ACTION_ASK, ACTION_COMPLETE)
                and self._stuck_count >= ASK_ON_STUCK_STEPS):
            action = ACTION_ASK
            params = {"question": "任务似乎停滞了，需要你提供帮助：要不要返回上一页重试，还是换个方式？"}
            skip_reason = ""

        if not skip_reason and action == ACTION_CLICK:
            point = params.get("point")
            if not self._click_point_matches_ui(point, ui_state):
                skip_reason = "click_off_ui"
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
            confidence=0.0 if skip_reason else analyzer["confidence"],
            current_subgoal_index=analyzer["current_subgoal_index"],
            stuck_count=self._stuck_count,
            ui_risk_elements=ui_risk_elements,
            executable=not skip_reason,
            skip_reason=skip_reason,
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
    # Module 1 — Planner
    # ------------------------------------------------------------------

    def _run_planner(self, input_data: AgentInput) -> Tuple[List[str], str, str]:
        system_prompt = (
            "你是一个任务规划器。根据用户当前任务（结合会话上下文与长期记忆），输出：\n"
            "1. 抽象子目标序列\n"
            "2. 需要打开的应用名称\n\n"
            "规划要求：\n"
            "- 子目标必须严格按照任务语义顺序排列，不得跳步或倒序\n"
            "- 子目标只描述阶段目标，不含按钮名称或坐标\n"
            "- 禁止同义重复或拆分重复：同一个目标不要拆成两个等价步骤\n"
            "- 输入并选择候选属于同一阶段时，应合并为一个子目标\n"
            "- 用户指令中的指代（如\"刚才那个\"\"继续\"）要结合会话上下文消解后再规划\n"
            "- 长期记忆中的用户偏好应直接体现在规划里（如记忆说常用高德则 app_name 用高德）\n\n"
            "只输出严格 JSON，格式：\n"
            "{\"app_name\":\"应用名\",\"subgoals\":[\"子目标1\",\"子目标2\"]}\n"
            "禁止输出任何额外文字。"
        )
        context_text = _format_context(input_data.conversation_context)
        memory_text = input_data.memory_text or "（无）"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"当前任务：{input_data.instruction}\n"
                f"会话上下文：\n{context_text}\n"
                f"长期记忆：\n{memory_text}"
            )},
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
                "- 可考虑输出 BACK/HOME 恢复，或输出 ASK 请用户指示\n"
            )

        history_text = ""
        if input_data.history_actions:
            recent = input_data.history_actions[-5:]
            history_text = f"\nrecent_actions: {json.dumps(recent, ensure_ascii=False)}"

        ask_reply_text = ""
        if input_data.ask_reply:
            ask_reply_text = (
                f"\n【用户对上次提问的回复】\n{input_data.ask_reply}\n"
                "请优先按该回复执行，不要再问同样的问题。\n"
            )

        context_text = _format_context(input_data.conversation_context)
        memory_text = input_data.memory_text or "（无）"

        system_prompt = (
            "你是一个手机 GUI 自动化对话助手，同时也是自身决策的风险评估器。\n\n"
            "决策依据（按优先级）：\n"
            "1. ask_reply 非空时，优先按用户对上次提问的回复执行\n"
            "2. instruction 为本轮最终目标，判断任务是否完成\n"
            "3. conversation_context 是本会话此前的对话（含上轮总结与问答），用于消解指代（如\"继续\"\"刚才那个App\"）\n"
            "4. memory_text 是长期记忆（用户偏好与历史路径），无冲突时应遵循\n"
            "5. previous_progress 是上一轮状态摘要，必须作为连续上下文参考\n"
            "6. recent_actions 是最近几步的实际执行记录\n"
            "7. subgoals 是规划的参考预测，不作为执行约束\n"
            "8. 结合当前截图和 ui_elements 决定下一步操作\n\n"
            "【何时输出 ASK（向用户提问）】满足任一：\n"
            "- instruction 存在指代或歧义，且 conversation_context/memory_text 无法消解\n"
            "- 存在多个候选（如选哪个App、哪条路线），且用户偏好与记忆无法裁决\n"
            "- 需要用户提供输入（验证码、账号选择、确认对象）才能继续\n"
            "ASK 的 parameters: {\"question\":\"一句中文问题\",\"options\":[\"选项A\",\"选项B\"]}（options 可省略）\n"
            "能够自主推进时禁止使用 ASK。\n\n"
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
            "  · safe：浏览、滚动、返回、回到桌面、打开应用本体、输入搜索关键词、点击普通列表项、ASK\n"
            "  · medium：仅限进入收费/实名/授权页面之前的可返回入口动作；此类动作仍可 BACK 撤销\n"
            "  · high：不可逆或有外部影响的动作，包括：确认支付/转账、删除/清空数据、发送消息/拨号、提交不可撤销表单、授权第三方账号、卸载/清除应用数据\n"
            "- risk_category 取值：payment / delete / auth / submit / communication / system / none\n"
            "- current_state：一句话描述当前页面与上下文\n"
            "- consequence：执行该动作后会发生什么（包含金额、对象、影响范围等关键信息）\n"
            "- rollback_hint：如何撤销；若不可撤销，写\"不可撤销\"\n"
            "- risk_reason：为何判定为该 risk_level\n"
            "- confidence：当前决策的置信度，0~1\n"
            "- current_subgoal_index：当前正在执行的 subgoals 下标（0 起），若无法判断填 null\n"
            + recovery_hint
            + "\n输出严格 JSON：\n"
            "{\n"
            "  \"action\":\"CLICK|SCROLL|TYPE|OPEN|BACK|HOME|COMPLETE|ASK\",\n"
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
                    f"conversation_context:\n{context_text}\n"
                    f"memory_text:\n{memory_text}\n"
                    f"subgoals: {json.dumps(self.subgoals, ensure_ascii=False)}\n"
                    f"page_type: {ui_state.get('page_type', 'unknown')}\n"
                    f"ui_elements: {json.dumps(ui_state.get('elements', []), ensure_ascii=False)}\n"
                    f"previous_progress: {self.progress or 'None'}\n"
                    f"current_step: {input_data.step_count}"
                    + history_text
                    + ask_reply_text
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
    # Turn summary (chat reply)
    # ------------------------------------------------------------------

    def summarize_turn(self, input_data: AgentInput) -> str:
        """One-shot summary of the finished turn. Degrades to a progress-based
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
            f"执行动作：{json.dumps(actions, ensure_ascii=False)}\n"
            f"最终进度：{self.progress or '无'}"
        )
        try:
            text = call_vlm(system_prompt, user_prompt)
            if text and text.strip():
                return text.strip()
        except Exception as e:
            logger.warning("summarize_turn failed (fallback used): %s", e)
        done = len(actions)
        base = f"本轮共执行了 {done} 步操作。"
        if self.progress:
            base += f"最终进度：{self.progress}"
        return base

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

        if normalized == ACTION_ASK:
            return normalized, params if isinstance(params, dict) else {}

        if self._has_final_confirmation_element(ui_state):
            return ACTION_COMPLETE, {}

        if self._is_initial_page(ui_state) and app_name:
            return ACTION_OPEN, {"app_name": app_name}

        return normalized, params if isinstance(params, dict) else {}

    def _normalize_schema(self, action: str, params: Dict) -> Tuple[str, Dict, str]:
        action = (action or "").upper().strip()
        alias_map = {"OPEN_APP": ACTION_OPEN, "APP_OPEN": ACTION_OPEN,
                     "ASK_USER": ACTION_ASK, "QUESTION": ACTION_ASK}
        action = alias_map.get(action, action)

        valid = {ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN,
                 ACTION_COMPLETE, ACTION_BACK, ACTION_HOME, ACTION_ASK}
        if action not in valid:
            return ACTION_COMPLETE, {}, ""

        if action == ACTION_ASK:
            question = str(params.get("question", "") or "").strip()
            if not question:
                return ACTION_COMPLETE, {}, ""
            clean: Dict[str, Any] = {"question": question}
            options = params.get("options")
            if isinstance(options, list):
                clean["options"] = [str(o) for o in options if str(o).strip()]
            return ACTION_ASK, clean, ""

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

    def _click_point_matches_ui(self, point: Any, ui_state: Dict, tolerance: int = 30) -> bool:
        if not self._is_point(point):
            return False
        elements = ui_state.get("elements") or []
        if not elements:
            return True  # nothing to validate against
        px, py = point[0], point[1]
        for el in elements:
            ep = el.get("point")
            if not self._is_point(ep):
                continue
            if abs(px - ep[0]) <= tolerance and abs(py - ep[1]) <= tolerance:
                return True
        return False

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
