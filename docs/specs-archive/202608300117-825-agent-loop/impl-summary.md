# Implementation Summary: ChatGuiAgent 单循环 ReAct 化（feature 825）

日期：2026-08-30 · 测试基线：`cd backend && ../.venv/Scripts/python.exe -m pytest -q` → **127 passed**

## Files Created

- `backend/tests/test_chat_agent_react_loop.py` — 28 个契约用例（单循环调用数 / 统一 schema / 结构化历史 / 硬约束 / 高危强制 / 业务覆写 / 卡死升级 / WAIT / 降级）
- `backend/tests/test_engine_ask_reask.py` — 3 个用例（M-2 同轮二次 ASK 等待 / engine WAIT 零 VLM 调用 + 无设备操作 + WAIT 等待预算）
- `change/825-agent-loop/impl-summary.md` — 本文件

## Files Modified

- `backend/app/agent/schemas.py` — 新增 `ACTION_WAIT = "WAIT"` 并加入 `ALL_ACTIONS`（动作空间 9 种）
- `backend/app/agent/chat_agent.py` — 完全重写为单循环：删除 `_run_planner` / `_extract_ui` / `_analyze_action` / `self.subgoals` / `self.last_ui_state`；新增 `_decide()`（单次 VLM 调用，统一输出 schema）、`self._plan` / `self._history` 内部状态、结构化历史注入（`HISTORY_WINDOW_N=8` / `HISTORY_ELEMENTS_K=10` / `HISTORY_THOUGHT_MAX_CHARS=200`）、`_matched_element()`（FR-04：CLICK 命中 high_risk 元素强制 `risk_level=high`）
- `backend/app/runtime/engine.py` — 三处：`_STABLE_MAX["WAIT"]=20.0`、`min_waits["WAIT"]=2.0`（FR-09 等待预算）；ASK 分支 `ask_event.wait()` 前 `clear()`（M-2 修复）
- `backend/benchmark/aw_adapter.py` — `_map_action` 新增 WAIT → `JSONAction(action_type="wait")` 分支；skip 的 quiet round 注释同步（WAIT 不再是 skip 替身）
- `backend/tests/test_chat_agent_schema.py` — 非法动作示例 "WAIT"→"PAUSE"（规格联动，见 Notes）
- `backend/tests/README.md` — 4.3 表格示例 WAIT→PAUSE；新增 4.5「Agent 单循环（feature 825）」契约章节；第 7 章补两文件映射
- `docs/project.md` — Mission 单循环描述 / Features 825 条目 / 决策流水线图（structured_history）/ app.agent 职责行 / Conventions（动作 9 种 + 结构化历史条目）

## Acceptance Criteria

- [x] AC-01 每步恰好 1 次 VLM 调用 — Passed — `TestSingleCallLoop`
- [x] AC-02 多轮上下文注入不回退 — Passed — 既有 `test_chat_engine.py` 多轮契约用例（未修改，全量 127 passed 含）
- [x] AC-03 click_off_ui → skip — Passed — `TestHardConstraints::test_click_off_ui_skip`
- [x] AC-04 CLICK 命中 high_risk 强制确认 — Passed — `TestHighRiskEnforcement::test_click_high_risk_forces_confirm`
- [x] AC-05 桌面页强制 OPEN — Passed — `TestBusinessOverrides::test_initial_page_forced_open`
- [x] AC-06 卡死→ASK + M-2 二次 ASK 等待 — Passed — `TestStuckEscalation` + `TestSecondAskWaits`
- [x] AC-07 非法输出降级不崩溃 — Passed — `TestDegradation`
- [x] AC-08 既有测试全通过 + mock 冒烟 — Passed — 全量 127 passed；`USE_MOCK_AGENT=1` 链路冒烟通过（MockGuiAgent 不受影响）
- [x] AC-09 旧三阶段代码删除 — Passed — grep `_run_planner|_extract_ui|_analyze_action|self.subgoals` 于 `backend/app/` 零命中（`last_ui_state` 仅存于 MockGuiAgent/base.py 接口契约，tests/README §4.2 要求保留）
- [x] AC-10 thought 持久化与容错 — Passed — `TestUnifiedSchema`（`test_missing_thought_degrades_to_empty` 等；plan.md 映射表笔误写作 `TestThoughtPersistence`，实际类名以测试文件为准）
- [ ] AC-11 AndroidWorld 冒烟不回退 — **待用户手动执行**（`.venv313`，`run_benchmark.py`，n_task_combinations=1, seed=30，对照 826 基线 17 任务 44.1%；报告落 `artifacts/benchmark/`）
- [x] AC-12 结构化历史、无历史截图 — Passed — `TestStructuredHistory`（含图像块恰好 1 个断言）
- [x] AC-13 WAIT 合法 + 零 VLM 等待 + 卡死兜底 + 官方映射 — Passed — `TestWaitAction`（agent 侧）+ `TestEngineWait`（engine 侧）+ `aw_adapter._map_action` WAIT 分支核对

## Notes

1. **规格联动（偏差 1）**：826 的既有用例 `test_invalid_action_becomes_skip_not_complete` 以 `"WAIT"` 作非法动作示例，与 FR-09 冲突（825 起 WAIT 合法）——示例换为 `"PAUSE"`，同步 `tests/README.md` §4.3。语义不变：仍锁定「白名单外动作降级为 skip 而非 COMPLETE」。
2. **stuck 计数时序（偏差 2）**：沿用既有实现语义——stuck 计数在 `act()` 开头比较上两步 progress，首次重复在第 3 步才计数，故测试以 `ASK_ON_STUCK_STEPS + 2` 步触发 ASK（plan 未明此细节，以既有行为为准）。
3. **plan.md 映射表笔误（偏差 3）**：AC-10 验证器实际为 `TestUnifiedSchema`，非 `TestThoughtPersistence`。
4. **AC-11 未闭环**：AndroidWorld 冒烟需 `.venv313` 环境与设备/模拟器，留待用户手动跑分；plan.md 该 checkbox 保持未勾。若回退，按 plan Risks 第 4 条对比 `BENCHMARK_RUN_DIR` 落盘的 step 轨迹定位。
5. `raw_output` 现为完整统一 JSON（含 plan/thought/elements），`_write_step_artifacts` 天然兼容，adapter 未做格式适配。
