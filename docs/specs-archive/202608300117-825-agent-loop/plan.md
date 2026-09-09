# Implementation Plan: ChatGuiAgent 单循环 ReAct 化（feature 825）

## Overview

将 `ChatGuiAgent` 的三阶段流水线（Planner → UIExtractor → ActionAnalyzer，每步 2~3 次 VLM 调用）重写为单一 agent loop：每步恰好一次 VLM 调用，统一输出 schema 同步产出 plan / thought / elements / action / progress / 风险自评。历史上下文以结构化条目（page_type + elements + thought 截断 + 已执行动作）注入，多模态输入仅当前一张截图。新增 CLICK 命中 high_risk 元素的代码级强制风险确认（独立于模型自评）。动作 schema 新增第 9 种动作 WAIT：加载期真实等待（复用屏幕稳定等待机制），等待期间不发起 VLM 调用，防止密集轮询消耗 token。顺带修复 engine M-2（同轮第二次 ASK 不暂停）。

对外契约不变：`agent.act(input) -> AgentOutput`、`AgentInput`/`AgentOutput` 字段、WS 事件、安全确认流程、既有 56 个契约测试全部不动；动作 schema 为向后兼容扩展（+WAIT，其余 8 种不变）。

## Architecture Decisions

| 决策 | 理由 |
|---|---|
| 单次调用统一 schema（Think/Observe/Act 同步产出，无无动作步） | 每步 VLM 调用 2~3 次 → 1 次，延迟约减半；符合 feature FR-01/FR-02 |
| 结构化历史为 agent 实例内部状态（`self._history`，与 `self.progress` 同级），不扩展 `AgentInput`/`AgentOutput` | 影响面收敛在 agent 内部（FR-07/FR-08）；保住 56 个契约测试与 adapter 兼容性 |
| 窗口参数模块级常量：`HISTORY_WINDOW_N = 8`、`HISTORY_ELEMENTS_K = 10`、`HISTORY_THOUGHT_MAX_CHARS = 200` | FR-07 Open Questions 落定：K=10 为 20 全量与上下文体积的折中；常量便于后续调参重跑 |
| FR-04 强制升级发生在 agent 输出组装阶段（`assess_output` 之前），直接改写 `risk_level=high` | feature Integration Points 约定；engine/assess_output 零改动 |
| FR-03 基准修正为 826 合入后现状：仅保留「桌面页 + app_name → 强制 OPEN」覆写 | 「立即支付→COMPLETE」已被 826 删除（never silently declare completion）；feature.md FR-03/AC-05 已同步修正。高危防线 = FR-04 强制确认，语义更合理 |
| plan 结构沿用旧 Planner 格式 `{"app_name": str, "subgoals": [str]}`，step 0 必须输出，后续可省略（=沿用）或输出修订版；解析失败降级 `[instruction]` | 保持 `current_subgoal_index` 语义与 `_postprocess_action` 的 app_name 来源连续 |
| 降级语义不变：`_extract_json_object` 容错 → 正则 fallback（`_extract_action_fallback` 保留）→ COMPLETE；VLM 3 次重试指数退避 | AC-07 与 NFR 可靠性要求 |
| 仅**实际执行**的步骤进入结构化历史（skip 步页面不变，下一步会重新观察） | 与 engine `history_actions` 只记执行步的语义对齐，避免冗余条目 |
| 新增 WAIT 动作（第 9 种）：agent 侧无必填参数、`executable=True`；engine 侧无设备操作但触发 `_wait_for_stable_screen` 专属预算（基础 2s / 上限 20s），等待期间零 VLM 调用，稳定截图经 `stable_image` 机制直喂下一步；adapter 直接映射官方 `JSONAction(action_type="wait")`（官方动作空间原生支持） | 给「页面加载中想等一下」一个诚实出口，替代「被拦 skip + 立即下一轮 VLM 调用」的密集轮询，省 token；防滥用由既有卡死检测兜底（连续 WAIT → progress 不变 → 3 步强制 ASK），无需新机制 |

## Implementation Steps

### Step 1: 契约测试先行（TDD 红）

- [x] 新建 `backend/tests/test_chat_agent_react_loop.py`，桩掉 VLM（monkeypatch `ChatGuiAgent._call_api` 返回预设响应，或注入 fake client），覆盖：
  - 单循环：一个轮次多步、每步恰好 1 次 `_call_api` 调用（AC-01）
  - 统一 schema 解析：plan/thought/elements/action/progress/风险字段全量输出正确组装
  - `click_off_ui`：CLICK 坐标未命中 elements（±30）→ skip（AC-03）
  - high_risk 强制确认：CLICK 命中 `high_risk=true` 元素且模型自评 safe → `risk_level=high`、`is_safe=False`（AC-04）
  - 桌面页覆写：page_type=home 且 plan 含 app_name → 强制 OPEN（AC-05）
  - 卡死升级：progress 连续 3 步不变 → 强制 ASK（AC-06 前半）
  - thought 持久化：`raw_output` JSON 含非空 thought；模型缺失 thought → 空字符串、动作照常、`executable` 不受影响（AC-10）
  - 结构化历史：第 2 步 prompt 含第 1 步的 page_type/elements(K 截断)/thought(200 截断)/action/parameters；user content 中图像块恰好 1 个（无历史截图）（AC-12）
  - 降级：非法 JSON / 非法动作 → skip 或 COMPLETE，不抛异常（AC-07）
  - WAIT：`_normalize_schema("WAIT", {})` 合法且 `executable=True`、无 skip_reason；连续 WAIT（progress 不变）触发卡死检测升级 ASK（AC-13 agent 侧）
- [x] 新建 `backend/tests/test_engine_ask_reask.py`：M-2 —— 同轮第二次 ASK 在用户回复前保持 `waiting_ask`（`ask_event` 等待前 clear）（AC-06 后半）；另覆盖 engine WAIT 语义：收到 WAIT 后不调用 `agent.act` 直至稳定等待结束（以桩计数验证零 VLM 调用）、无设备操作（controller 桩无 click/swipe 调用）、稳定等待被调用且以 WAIT 为 key 取预算（AC-13 engine 侧）
- [x] 同步 `backend/tests/README.md`：新增「Agent 单循环（feature 825）」契约章节（权威规格要求）
- Files: `backend/tests/test_chat_agent_react_loop.py`（新）、`backend/tests/test_engine_ask_reask.py`（新）、`backend/tests/README.md`（追加章节）

### Step 2: chat_agent.py 单循环重写（TDD 绿）

- [x] 删除 `_run_planner` / `_extract_ui` / `_analyze_action` / `self.subgoals` / `self.last_ui_state` 及相关常量引用（AC-09）
- [x] 新增内部状态：`self._plan: Dict`、`self._history: List[Dict]`；`reset()` 清空
- [x] 新增 `_decide(input_data) -> Dict`：组装单次调用的 messages——
  - system prompt 合并三段职责：任务规划（step 0 输出 plan，后续可修订）+ 指代消解与记忆融入 + UI 元素识别要求（沿用 elements 字段与 ≤20 上限）+ 动作决策 + 风险自评（沿用现有 risk 字段定义与 ASK 触发条件）
  - user text：instruction / conversation_context / memory_text / 当前 plan（step>0）/ 结构化历史（最近 N 条）/ previous_progress / current_step / ask_reply（非空时）
  - user content 图像块：仅当前截图 1 张
- [x] 统一输出 schema 解析与容错（缺失 thought=空串不阻塞；plan 缺失沿用；elements 缺失为空数组；沿用 `_clamp_point`/`_normalize_enum`/`_clamp_confidence`/`_normalize_subgoal_index`）
- [x] 后处理管线按序保留：`_postprocess_action`（桌面页 + `self._plan["app_name"]` → OPEN，受 `_disable_business_overrides` 开关控制）→ `_normalize_schema` → stuck 计数（`ASK_ON_STUCK_STEPS`）→ click_off_ui 校验 → `_apply_skip_streak`（`ASK_ON_SKIP_STREAK`）；`_normalize_schema` 动作白名单加入 `ACTION_WAIT`（无必填参数校验，`reason` 可选透传）
- [x] `backend/app/agent/schemas.py`：新增 `ACTION_WAIT = "WAIT"` 常量并加入 `ALL_ACTIONS`（其余不动）
- [x] 统一 schema 的 system prompt：动作列表加 WAIT 及触发场景（`page_type=loading`、跳转/渲染未完成、等待上一步延迟生效）；WAIT 默认 `risk_level=safe`
- [x] 新增 FR-04：`_matched_element(point, elements, tolerance=30)` 返回命中元素；命中且 `high_risk=true` → 无视模型自评强制 `risk_level=high`，并将该元素补入 `ui_risk_elements`
- [x] 结构化历史更新：仅当步动作实际执行（无 skip_reason 且动作非 ASK/COMPLETE）时 append `{"page_type", "elements"[:K], "thought"[:200], "action", "parameters"}`
- [x] `AgentOutput` 组装：thought/plan/elements 仅随 `raw_output`（完整统一 JSON）持久化，不新增契约字段；`stuck_count`/`executable`/`skip_reason` 照旧
- [x] `summarize_turn` 保持不变
- Files: `backend/app/agent/chat_agent.py`

### Step 3: engine.py —— M-2 修复 + WAIT 等待预算

- [x] ASK 分支在 `await session.ask_event.wait()` 前 `session.ask_event.clear()`（与 confirm 分支对称；注意首次进入该分支时事件本为未设置态，clear 是幂等防护）
- [x] WAIT 等待预算：`_STABLE_MAX` 新增 `"WAIT": 20.0`、`_wait_for_stable_screen` 的 `min_waits` 新增 `"WAIT": 2.0`。控制流零变更——`_execute_action` 的 if/elif 链对 WAIT 天然 no-op（无设备操作），且 `output.action != ACTION_COMPLETE` 判定使 WAIT 自然走 `_wait_for_stable_screen`；WAIT `executable=True` → 记入 `history_actions`（真实决策，占一步预算）；稳定截图经既有 `stable_image` 机制直喂下一步
- Files: `backend/app/runtime/engine.py`（一处 clear + 两个字典常量各一条）

### Step 4: adapter 核对与文档同步

- [x] 核对 `benchmark/aw_adapter.py`：`act` 契约不变；`_map_action` 新增 WAIT 分支 → `JSONAction(action_type="wait")`（官方 `_ACTION_TYPES` 原生支持，等待语义交由官方 harness 执行）；确认 `disable_business_overrides=True` 语义、ASK→`infeasible` 映射、`_write_step_artifacts` 对新 raw_output（统一 JSON）天然兼容；skip→quiet round 的既有注释同步更新（WAIT 不再是 skip 的替身）
- [x] `docs/project.md`：Mission/Architecture 中「三阶段决策（Planner/UIExtractor/ActionAnalyzer）」改为单循环描述；`app.agent` 职责行同步；Conventions 增补「结构化历史（无历史截图）」条目
- Files: `docs/project.md`（`backend/benchmark/aw_adapter.py` 预期不动）

### Step 5: 全量验证与冒烟跑分

- [x] `cd backend && ../.venv/Scripts/python.exe -m pytest -q` 全部通过（既有 56 + 新增）
- [x] `USE_MOCK_AGENT=1` 全链路冒烟（建会话→消息→ASK→回复→总结，MockGuiAgent 不受影响）
- [x] AC-09 残留检查：`grep -n "_run_planner\|_extract_ui\|subgoals" backend/app/` 无命中
- [ ] AC-11：AndroidWorld 冒烟子集重跑（`.venv313`，`run_benchmark.py`，与 824/826 基线相同配置：n_task_combinations=1, seed=30），`report.py` 出分对照 ≥ 基线（当前 826 冒烟 17 任务 44.1%）

## Acceptance Criteria Mapping

| AC | Verified By |
|----|-------------|
| AC-01 每步 1 次 VLM 调用 | `test_chat_agent_react_loop.py::TestSingleCallLoop`（调用桩计数） |
| AC-02 多轮上下文注入不回退 | 既有 `test_chat_engine.py` 多轮契约用例（不修改） |
| AC-03 click_off_ui skip | `TestHardConstraints::test_click_off_ui_skip` |
| AC-04 high_risk 强制确认 | `TestHighRiskEnforcement::test_click_high_risk_forces_confirm` |
| AC-05 桌面页强制 OPEN | `TestBusinessOverrides::test_initial_page_forced_open` |
| AC-06 卡死→ASK + M-2 二次 ASK 等待 | `TestStuckEscalation` + `test_engine_ask_reask.py` |
| AC-07 非法输出降级不崩溃 | `TestDegradation` |
| AC-08 既有 56 测试 + mock 冒烟 | Step 5 pytest 全量 + `USE_MOCK_AGENT=1` 手动链路 |
| AC-09 旧三阶段代码删除 | Step 5 grep 检查 |
| AC-10 thought 持久化与容错 | `TestThoughtPersistence` |
| AC-11 冒烟跑分不回退 | Step 5 AndroidWorld 重跑（手动，报告落 `artifacts/benchmark/`） |
| AC-12 结构化历史、无历史截图 | `TestStructuredHistory`（捕获实际 prompt 内容断言） |
| AC-13 WAIT 合法 + 零 VLM 等待 + 卡死兜底 + 官方映射 | `TestWaitAction`（agent 侧）+ `test_engine_ask_reask.py` WAIT 用例（engine 侧）+ adapter 映射核对（Step 4） |

## Risks & Mitigations

- **单次输出变长，延迟未必减半**：统一 schema 要求一次输出 elements(≤20) + 全部字段，输出 token 增加。→ prompt 要求 elements 仅保留任务相关项、字段紧凑；实现后实测单步延迟与 826 基线对比，若不达预期调 prompt（如 elements 降为 ≤12）再跑冒烟。
- **长 JSON 解析失败率高于分阶段**：→ 保留 `_extract_json_object` 宽容提取 + `_extract_action_fallback` 正则降级 + 3 次重试；失败降级语义与现状一致（AC-07 锁定）。
- **结构化历史体积随 N×K 增长**：→ K=10 截断 + 常量集中定义；AC-12 锁定注入形态，后续调参只改常量。
- **AC-11 冒烟回退**：→ 与 826 完全相同配置重跑对照；若回退，先对比失败任务的 step 轨迹（`BENCHMARK_RUN_DIR` 落盘产物）定位是 prompt 问题还是 schema 解析问题，再决定回滚或调参。
- **既有测试耦合内部方法**：`test_chat_agent_schema.py` 直接调用 `_normalize_schema`/`_apply_skip_streak`。→ 这两个方法及签名原样保留，只动它们的上游数据流。
- **engine M-2 修复时序**：clear 放在 wait 前可能吞掉 stop_turn 已设置的事件。→ stop_turn 同时 set `ask_event` 与 `confirm_event` 的现有语义不变，clear 紧贴 wait 之前且仅在进入 ASK 等待时执行一次，新增测试覆盖「ASK 等待中 stop 立即退出」。
- **WAIT 双重语义差异（产品 vs benchmark）**：产品侧 WAIT 走我们的稳定等待（2~20s 自适应），benchmark 侧交由官方 wait 语义（官方固定时长）。→ 两者均为「一轮等待」语义，不影响分数可比性；跑分后若 loading 类任务行为异常，优先核对官方 wait 时长是否足够。

## Estimated Complexity

**Medium-High** —— 核心是单文件（chat_agent.py，约 400 行）的完全重写，但对外契约面极窄（AgentInput/AgentOutput/动作 schema 全不变），且有测试先行锁定行为 + 826 冒烟基线作量化对照；engine 仅一行级修复，adapter 预期零改动。主要不确定性集中在统一 prompt 的输出质量（Risks 第 1、2 条），靠 AC-11 跑分闭环验证。
