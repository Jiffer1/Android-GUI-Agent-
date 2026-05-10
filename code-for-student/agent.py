import json
import re
from typing import Any, Dict, List, Optional, Tuple

from agent_base import (
    ACTION_CLICK,
    ACTION_COMPLETE,
    ACTION_OPEN,
    ACTION_SCROLL,
    ACTION_TYPE,
    AgentInput,
    AgentOutput,
    BaseAgent,
    UsageInfo,
)

_EMPTY_UI_STATE = {"elements": [], "page_type": "unknown"}


class Agent(BaseAgent):
    """GUI Agent with Planner / UIExtractor / ActionAnalyzer modules."""

    def _initialize(self):
        self.subgoals: List[str] = []
        self.last_ui_state: Optional[Dict] = None
        self.progress: str = ""

    def reset(self):
        self.subgoals = []
        self.last_ui_state = None
        self.progress = ""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def act(self, input_data: AgentInput) -> AgentOutput:
        if input_data.step_count == 1:
            subgoals, app_name, planner_raw, planner_usage = self._run_planner(input_data)
            self.subgoals = subgoals

            ui_state = self._extract_ui(input_data)
            self.last_ui_state = ui_state

            action, params, progress, raw, analyze_usage = self._analyze_action(input_data, ui_state)
            action, params = self._postprocess_action(input_data, ui_state, action, params, app_name)
            action, params = self._normalize_schema(action, params)
            self.progress = progress
            combined_usage = self._merge_usage(planner_usage, analyze_usage)
            combined_raw = (
                json.dumps(
                    {
                        "planner": self._extract_json_object(planner_raw) or planner_raw,
                        "analyzer": self._extract_json_object(raw) or raw,
                    },
                    ensure_ascii=False,
                )
                if planner_raw or raw
                else ""
            )
            return AgentOutput(action=action, parameters=params, raw_output=combined_raw, usage=combined_usage)

        ui_state = self._extract_ui(input_data)
        action, params, progress, raw, usage = self._analyze_action(input_data, ui_state)
        action, params = self._postprocess_action(input_data, ui_state, action, params)
        action, params = self._normalize_schema(action, params)
        self.progress = progress
        self.last_ui_state = ui_state
        return AgentOutput(action=action, parameters=params, raw_output=raw, usage=usage)

    # ------------------------------------------------------------------
    # Module 1 — Planner
    # ------------------------------------------------------------------

    def _run_planner(self, input_data: AgentInput) -> Tuple[List[str], str, str, UsageInfo]:
        system_prompt = (
            "你是一个任务规划器。根据用户任务，输出：\n"
            "1. 抽象子目标序列\n"
            "2. 需要打开的应用名称\n\n"
            "规划要求：\n"
            "- 子目标必须严格按照用户任务描述的语义顺序排列，不得跳步或倒序\n"
            "- 子目标只描述阶段目标，不含按钮名称或坐标\n"
            "- 禁止同义重复或拆分重复：同一个目标不要拆成两个等价步骤\n"
            "- 输入并选择候选属于同一阶段时，应合并为一个子目标（例如：搜索并选择地点）\n"
            "- 对有先后依赖的任务，必须完成前一对象后再处理后一对象（例如打车：先完整处理起点，再完整处理终点）\n\n"
            "示例1：\n"
            "任务：去美团外卖购买窑村干锅猪蹄（科技大学店）店铺的干锅排骨，地址选择默认地址\n"
            "输出：{\"app_name\":\"美团\",\"subgoals\":["
            "\"进入美团\","
            "\"搜索并进入店铺：窑村干锅猪蹄（科技大学店）\","
            "\"搜索并选择菜品：干锅排骨\","
            "\"加入购物车\","
            "\"确认使用默认地址\","
            "\"提交订单\""
            "]}\n\n"
            "示例2：\n"
            "任务：在百度地图打车，从国际医学中心到西安回民街\n"
            "输出：{\"app_name\":\"百度地图\",\"subgoals\":["
            "\"进入百度地图打车页面\","
            "\"设置起点并选择对应地点：国际医学中心\","
            "\"设置终点并选择对应地点：西安回民街\","
            "\"完成打车\""
            "]}\n\n"
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
            usage = self.extract_usage_info(resp)
        except Exception:
            subgoals = [input_data.instruction]
            app_name = ""
            usage = UsageInfo()

        self.subgoals = subgoals
        raw = json.dumps({"subgoals": subgoals, "app_name": app_name}, ensure_ascii=False)
        return subgoals, app_name, raw, usage

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
            "  \"page_type\": \"home|search|result|detail|player|form|payment|...\"\n"
            "}\n\n"
            "字段说明：\n"
            "- 只提取与 instruction 或 planner_subgoals 相关的可交互元素；与当前任务无关的 UI 不要提取\n"
            "- 常见无关项（如广告位、活动入口、会员推广）默认不提取，除非 instruction 或 planner_subgoals 明确要求\n"
            "- 结构化候选组（如选集列表、日期网格、筛选选项组）必须提取：在可见区域内，至少包含当前选中项及其相邻候选项，不能只返回组标题\n"
            "- 日期选择场景必须优先完整提取日期候选：至少返回当前选中日期 + 临近可选日期，并包含状态词对应项（如*今天/明天/后天*）\n"
            "- 若页面同时出现日期数值与状态词（如*9号*与*今天*），不得只提取其中之一；应合并到同一元素 text（如*9(今天)*），或分别提取并保持语义可对齐\n"
            "- 对选集场景，优先提取每一集的可点击按钮（数字集数或带状态的集数项），不要只提取\"选集\"标题\n"
            "- text：填写元素的实际内容标识，而非状态标签。若元素显示的是状态文字（如*今天*、*播放中*、*本地*，或表示已选中的图标），须同时包含其实际内容（如日期数字、集数编号），格式为*内容(状态)*，例如*5(今天)*、*2(播放中)*、*2(本地)*\n"
            "- selected：该元素当前处于选中/激活/高亮状态（如已选中的 tab、正在播放的集数、已激活的筛选项），设为 true\n"
            "- high_risk：该元素触发后会产生不可逆的高影响操作（如*立即支付*、*立即呼叫*），设为 true，*去结算* ui不属于高风险项\n"
            "- 若识别到文本包含*立即呼叫*、*立即支付*或*立即付款*，必须提取该元素，禁止遗漏\n"
            "- point 为 0-1000 归一化整数坐标\n"
            "- 页面存在日期选择语义（如*出发日期*、*日期*、日历区域）时，日期相关元素优先级高于普通输入框，必须优先纳入 elements，避免被无关输入项挤占\n"
            "- 元素按从上到下、从左到右的空间顺序输出\n"
            "- 最多返回 20 个最重要且与任务相关的可交互元素\n"
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
                    }
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
            clean_elements = []
            for el in elements:
                if not isinstance(el, dict):
                    continue
                point = el.get("point")
                if not self._is_point(point):
                    continue
                clean_elements.append({
                    "text": str(el.get("text", "")),
                    "type": str(el.get("type", "")),
                    "point": self._clamp_point(point),
                    "selected": bool(el.get("selected", False)),
                    "high_risk": bool(el.get("high_risk", False)),
                })
                if len(clean_elements) >= 20:
                    break
            clean_elements = self._normalize_episode_elements(clean_elements)
            result = {
                "elements": clean_elements,
                "page_type": str(obj.get("page_type", "unknown")),
            }
            return result
        except Exception:
            return dict(_EMPTY_UI_STATE)

    # ------------------------------------------------------------------
    # Module 3 — ActionAnalyzer
    # ------------------------------------------------------------------

    def _normalize_episode_elements(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not elements:
            return elements

        episode_numbers = []
        for el in elements:
            text = str(el.get("text", ""))
            m = re.match(r"\s*(\d+)\b", text)
            if m:
                episode_numbers.append(int(m.group(1)))

        if len(episode_numbers) < 2:
            return elements

        episode_numbers = sorted(set(episode_numbers))

        for el in elements:
            text = str(el.get("text", "")).strip()
            if text not in {"本地", "播放中", "正在播放"}:
                continue

            point = el.get("point")
            if not self._is_point(point):
                continue

            mapped = self._infer_episode_from_neighbors(self._clamp_point(point), elements, episode_numbers)
            if mapped is not None:
                el["text"] = f"{mapped}({text})"
        return elements

    def _infer_episode_from_neighbors(
        self,
        target_point: List[int],
        elements: List[Dict[str, Any]],
        episode_numbers: List[int],
    ) -> Optional[int]:
        same_row = []
        for el in elements:
            text = str(el.get("text", ""))
            point = el.get("point")
            if not self._is_point(point):
                continue
            clamped = self._clamp_point(point)
            if abs(clamped[1] - target_point[1]) > 80:
                continue
            m = re.match(r"\s*(\d+)\b", text)
            if m:
                same_row.append((clamped[0], int(m.group(1))))

        if not same_row:
            return None

        same_row.sort(key=lambda x: x[0])
        right_candidates = [num for x, num in same_row if x > target_point[0]]
        left_candidates = [num for x, num in same_row if x < target_point[0]]

        if right_candidates:
            guessed = right_candidates[0] - 1
            if guessed >= 1:
                return guessed
        if left_candidates:
            guessed = left_candidates[-1] + 1
            if guessed >= 1:
                return guessed

        min_num = min(episode_numbers)
        if min_num > 1:
            return min_num - 1
        return None

    def _analyze_action(
        self, input_data: AgentInput, ui_state: Dict
    ) -> Tuple[str, Dict, str, str, UsageInfo]:
        system_prompt = (
            "你是一个手机 GUI 自动化决策器。\n\n"
            "决策依据：\n"
            "1. 以 instruction 为最终目标，判断任务是否完成\n"
            "2. previous_progress 是上一轮状态摘要，必须作为当前决策的连续上下文优先参考\n"
            "3. subgoals 是规划器对任务步骤的参考预测，仅供理解任务结构，不作为执行约束\n"
            "4. 结合当前截图和 ui_elements 决定下一步操作\n\n"
            "【任务完成判断】\n"
            "- 满足以下任一条件即可直接输出 COMPLETE：\n"
            "  1) 当前页面出现敏感项（如*立即呼叫*、*立即支付*、*立即付款*）\n"
            "  2) previous_progress 已明确显示任务完成/已完成最终目标\n"
            "- 若以上条件均不满足，再根据当前截图判断是否仍有明显未完成步骤（如仍在编辑态、筛选面板未确认、未到目标结果页）\n\n"
            "【执行顺序约束】\n"
            "- 按 instruction 语义顺序推进，不要跳步\n"
            "- previous_progress 的阶段结论优先级高于当前截图的弱线索；若 previous_progress 已明确某阶段完成，禁止回退去重复该阶段\n"
            "- 多个可执行元素并存时，优先选择与当前语义阶段最匹配的元素\n"
            "- 筛选操作时，优先点击页面中的*了*字形图标（筛选/过滤入口）；仅当不存在该图标时，才直接点击筛选条件\n"
            "- 搜索流程默认顺序：CLICK 搜索框/搜索入口 -> TYPE 关键词 -> 提交搜索\n"
            "- 提交搜索时优先点击*搜索*按钮；若按钮不可点或不稳定，可点击与关键词最匹配的搜索候选项\n"
            "- CLICK 的 point 必须来自 ui_elements\n"
            "- 坐标为 0-1000 归一化整数\n\n"            "【高风险 UI 规则】\n"
            "- high_risk=true 的元素默认禁止点击\n\n"
            "【进度摘要】\n"
            "- 每一步都必须输出 progress，一句中文\n"
            "- progress 必须包含：已完成内容 + 当前页面状态 + 下一步目标\n"
            "- progress 仅用于跨步上下文传递，不要冗长\n\n"
            "输出严格 JSON：\n"
            "{\"action\":\"...\",\"parameters\":{...},\"progress\":\"...\"}\n"
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
                    f"current_step: {input_data.step_count}\n"
                    "Return the best next action."
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
            else:
                action, params, progress = self._extract_action_fallback(raw)
            if not isinstance(params, dict):
                params = {}
            if not isinstance(progress, str):
                progress = str(progress)
            usage = self.extract_usage_info(resp)
            return action, params, progress, raw, usage
        except Exception:
            return ACTION_COMPLETE, {}, self.progress, "", UsageInfo()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_initial_page(self, ui_state: Dict) -> bool:
        page_type = str(ui_state.get("page_type", "")).strip().lower()
        return page_type in {"home", "initial", "init", "launcher"}

    def _should_open_app(self, instruction: str, app_name: str) -> bool:
        if not app_name or not app_name.strip():
            return False
        text = (instruction or "").strip()
        if not text:
            return False
        open_patterns = [
            r"打开",
            r"启动",
            r"进入",
            r"点开",
            r"去(?![一-龥]*支付)",
        ]
        return any(re.search(p, text) for p in open_patterns)

    def _merge_usage(self, left: UsageInfo, right: UsageInfo) -> UsageInfo:
        return UsageInfo(
            input_tokens=(left.input_tokens or 0) + (right.input_tokens or 0),
            output_tokens=(left.output_tokens or 0) + (right.output_tokens or 0),
            total_tokens=(left.total_tokens or 0) + (right.total_tokens or 0),
            cached_tokens=(left.cached_tokens or 0) + (right.cached_tokens or 0),
            reasoning_tokens=(left.reasoning_tokens or 0) + (right.reasoning_tokens or 0),
        )

    def _postprocess_action(
        self,
        input_data: AgentInput,
        ui_state: Dict,
        action: str,
        params: Dict,
        app_name: str = "",
    ) -> Tuple[str, Dict]:
        normalized_action = (action or "").upper().strip()

        if self._has_final_confirmation_element(ui_state):
            return ACTION_COMPLETE, {}

        play_point = self._find_play_button(ui_state)
        if play_point is not None:
            return ACTION_CLICK, {"point": play_point}

        if input_data.step_count == 1 and self._is_initial_page(ui_state) and app_name:
            return ACTION_OPEN, {"app_name": app_name}

        if (
            self._is_initial_page(ui_state)
            and app_name
            and self._should_open_app(input_data.instruction, app_name)
        ):
            return ACTION_OPEN, {"app_name": app_name}

        if self._is_regression_after_filter_done(self.progress, ui_state, normalized_action, params):
            return ACTION_COMPLETE, {}

        if input_data.step_count == 1 and normalized_action == ACTION_COMPLETE:
            fallback_point = self._pick_search_entry_point(ui_state)
            if fallback_point is not None:
                return ACTION_CLICK, {"point": fallback_point}
            return ACTION_SCROLL, {"start_point": [500, 800], "end_point": [500, 300]}

        return normalized_action, params if isinstance(params, dict) else {}

    def _is_regression_after_filter_done(
        self,
        previous_progress: str,
        ui_state: Dict,
        action: str,
        params: Dict,
    ) -> bool:
        progress_text = (previous_progress or "").strip()
        if not progress_text:
            return False

        done_markers = ("已完成筛选", "筛选条件设置完成", "已完成发布时间", "已完成时长")
        if not any(marker in progress_text for marker in done_markers):
            return False

        if action != ACTION_CLICK or not isinstance(params, dict):
            return False

        point = params.get("point")
        if not self._is_point(point):
            return False
        target = self._clamp_point(point)

        for el in ui_state.get("elements", []):
            text = str(el.get("text", "")).strip()
            el_point = el.get("point")
            if not self._is_point(el_point):
                continue
            if self._clamp_point(el_point) != target:
                continue
            if "筛选" in text or text == "了":
                return True
        return False

    def _find_play_button(self, ui_state: Dict) -> Optional[List[int]]:
        play_keywords = ("立即播放", "播放")
        for el in ui_state.get("elements", []):
            text = str(el.get("text", "")).strip()
            el_type = str(el.get("type", "")).lower()
            point = el.get("point")
            if not self._is_point(point):
                continue
            if el_type == "button" and any(kw in text for kw in play_keywords):
                return self._clamp_point(point)
        return None

    def _has_final_confirmation_element(self, ui_state: Dict) -> bool:
        final_keywords = ("立即呼叫", "立即支付", "立即付款")
        for el in ui_state.get("elements", []):
            text = str(el.get("text", "")).strip()
            if not text:
                continue
            if any(keyword in text for keyword in final_keywords):
                return True
        return False

    def _pick_search_entry_point(self, ui_state: Dict) -> Optional[List[int]]:
        candidates = []
        for el in ui_state.get("elements", []):
            text = str(el.get("text", ""))
            el_type = str(el.get("type", "")).lower()
            point = el.get("point")
            if not self._is_point(point):
                continue
            if "搜索" in text or "查找" in text or el_type == "input":
                candidates.append(self._clamp_point(point))
        return candidates[0] if candidates else None

    def _extract_text(self, response: Any) -> str:
        try:
            return response.choices[0].message.content or ""
        except Exception:
            return ""

    def _normalize_schema(self, action: str, params: Dict) -> Tuple[str, Dict]:
        action = (action or "").upper().strip()
        alias_map = {
            "OPEN_APP": ACTION_OPEN,
            "APP_OPEN": ACTION_OPEN,
        }
        action = alias_map.get(action, action)
        if action not in {ACTION_CLICK, ACTION_SCROLL, ACTION_TYPE, ACTION_OPEN, ACTION_COMPLETE}:
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
            if not isinstance(text, str):
                text = str(text)
            return ACTION_TYPE, {"text": text}

        if action == ACTION_OPEN:
            app_name = params.get("app_name", "")
            if not isinstance(app_name, str):
                app_name = str(app_name)
            return ACTION_OPEN, {"app_name": app_name}

        return ACTION_COMPLETE, {}

    def _extract_action_fallback(self, text: str) -> Tuple[str, Dict, str]:
        """Regex fallback when JSON parsing fails — extracts action/parameters/progress individually."""
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
                            params = json.loads(text[start : start + i + 1])
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
        x = max(0, min(1000, int(float(point[0]))))
        y = max(0, min(1000, int(float(point[1]))))
        return [x, y]
