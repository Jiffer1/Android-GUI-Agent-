# Code Review: ChatGuiAgent 单循环 ReAct 化（feature 825）

日期：2026-08-30 · 范围：`backend/app/agent/chat_agent.py`（重写）、`schemas.py`、`runtime/engine.py`、`benchmark/aw_adapter.py`、`tests/`（两新文件 + 联动）、`docs/project.md` · 全量测试 **127 passed**（review 中重跑确认）

## Summary

实现与 feature/plan 高度一致：单循环（每步恰 1 次 VLM 调用）、统一 schema、结构化历史（无历史截图）、FR-04 代码级强制确认、WAIT 真实等待、M-2 修复均已正确落地，且 engine 侧改动真正收敛在两个字典常量 + 一行 clear。未发现 Critical/Major 问题；3 条 Minor 中最值得注意的是一个**恒真断言**（plan 降级测试实际检不出回归）。AC-11（AndroidWorld 冒烟）尚未执行，是合入前唯一未闭环的验收项。

## Findings

### 🔴 Critical

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| — | — | — | 无 | — |

### 🟠 Major

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| — | — | — | 无 | — |

### 🟡 Minor

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | `backend/tests/test_chat_agent_react_loop.py:192` | Test Quality | 断言 `"帮我完成任务" in user_text(...)` 恒真——instruction 文本必然出现在 prompt 的 `instruction:` 行，无论 plan 是否降级，该测试检不出降级失效 | 改为断言降级 plan 的注入形态，如 `'["帮我完成任务"]' in text` 或同时要求 `"current_plan" in text` |
| [ ] | `backend/app/agent/chat_agent.py:430` | Input Validation | `progress` 非 str 时 `str()` 强转：JSON `null` → 字面 `"None"` 成为 progress，dict/list 输入产生 Python repr 注入下一步 prompt | 非 str 一律归空串：`progress = progress if isinstance(progress, str) else ""` |
| [ ] | `backend/app/runtime/engine.py:478` | Test Coverage | AC-04 验收引用的「同会话拒绝后同动作自动跳过（`risk_previously_declined`）」为既有行为但无任何契约测试直接覆盖 | 补一条 engine 用例：跨 turn 两次相同 risky_click，第一次取消、第二次断言 skip 且不再弹确认 |

### 🔵 Info / Suggestions

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | `change/825-agent-loop/feature.md:90` | Spec Clarity | AC-11 写「不低于 824 基线」（18 任务 38.9%），plan.md Step 5 写「对照 826 冒烟 44.1%」，两口径在 38.9%~44.1% 区间结论相反 | 跑分前定口径；建议从严以 826 的 44.1% 为准（代码直接前序） |
| [ ] | `backend/app/agent/chat_agent.py:384` | Design | API 3 次重试全失败 → 降级 COMPLETE，与 826「never silently declare completion」精神有张力（本 feature 规格明确保留该降级，非缺陷） | 后续 feature 可评估改为 fail-turn 或 skip+续跑语义 |
| [ ] | `backend/app/agent/chat_agent.py:433` | Code Style | `_decide` 返回裸 dict，act() 中 17 处魔法字符串键索引，键名拼写错误无静态防护 | 可引入 TypedDict 或模块级 dataclass（不改行为） |
| [ ] | `backend/tests/test_engine_ask_reask.py:104` | Test Quality | 固定 `sleep(0.3)` 不符合「测试禁 arbitrary sleep」惯例——但此处是给 buggy 实现暴露 race 的观察窗（断言「不应发生之事」），已注释说明 | 可接受，保持现状 |

## Acceptance Criteria Coverage

| AC | Test | Status |
|----|------|--------|
| AC-01: 每步恰 1 次 VLM 调用 | `TestSingleCallLoop::test_one_vlm_call_per_step`（桩计数） | ✅ Covered |
| AC-02: 多轮上下文注入不回退 | 既有 `test_chat_engine.py` 多轮契约用例（127 passed 含，未修改） | ✅ Covered |
| AC-03: click_off_ui skip | `TestHardConstraints::test_click_off_ui_skip` | ✅ Covered |
| AC-04: high_risk 强制确认 | `TestHighRiskEnforcement`（agent 侧强制 high）+ 既有 `TestRiskConfirmation`（engine 侧批准/取消）；declined 子句缺直接测试（Minor #3） | ✅ Covered（含缺口记录） |
| AC-05: 桌面页强制 OPEN | `TestBusinessOverrides::test_initial_page_forced_open`（含 benchmark 关闭用例） | ✅ Covered |
| AC-06: 卡死→ASK + M-2 | `TestStuckEscalation` + `TestSecondAskWaits`（含 stop 退出用例） | ✅ Covered |
| AC-07: 非法输出降级 | `TestDegradation`（非 JSON / 非法动作两用例） | ✅ Covered |
| AC-08: 既有测试 + mock 冒烟 | 全量 127 passed（review 重跑确认）+ `USE_MOCK_AGENT=1` 冒烟（impl 期执行） | ✅ Covered |
| AC-09: 旧三阶段删除 | `test_no_separate_planner_stage` + grep `_run_planner\|_extract_ui\|_analyze_action\|self.subgoals` 零命中 | ✅ Covered |
| AC-10: thought 持久化与容错 | `TestUnifiedSchema::test_step0_output_fields` / `test_missing_thought_degrades_to_empty` | ✅ Covered |
| AC-11: 冒烟跑分不回退 | —（`.venv313` + `run_benchmark.py`，需设备/模拟器） | ⏳ 待用户手动执行 |
| AC-12: 结构化历史无历史截图 | `TestStructuredHistory`（条目形态/单图/K 截断/thought 截断/skip 不入史/N=8） | ✅ Covered |
| AC-13: WAIT 全链路 | `TestWaitAction`（agent）+ `TestEngineWait`（engine：零 VLM、零设备操作、WAIT 预算键）+ `aw_adapter.py:169` 映射核对 | ✅ Covered |

## Verdict

- [ ] ✅ Ready to merge
- [x] 🟡 Merge after minor fixes (no re-review needed)
- [ ] 🟠 Requires fixes and re-review
- [ ] 🔴 Do not merge — significant issues found

（前置条件：AC-11 冒烟跑分完成且不回退。Minor #1 建议修复——1 行改动；#2/#3 可随后处理。）
