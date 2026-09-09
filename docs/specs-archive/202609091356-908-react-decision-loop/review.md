# Code Review: Agent 纯 ReAct 决策循环(feature 908)

## Summary

核心重构质量高:瘦身 schema、append-only trace、回执注入、三级阶梯、mode 开关均与 README §4.6 契约精确对齐,132 个测试全绿,AC-01~05/08 已被测试直接锁定。发现 1 个 Major:VLM 调用三次重试全失败后静默降级为 COMPLETE,违反 feature NFR「绝不静默 COMPLETE」;另有 3 个 Minor(ask_reply 每步重复注入、skip-streak ASK 缺 reason_code、augment_risk_from_text 副作用式变更入参)。跑分(AC-07)正在后台执行,不影响上述结论。

## Findings

### 🔴 Critical

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | — | — | 无 | — |

### 🟠 Major

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [x] | `backend/app/agent/chat_agent.py:285-287` + `:321-334` | 降级语义 | VLM 调用 3 次重试全失败时 `_decide` 捕获异常返回 `_fallback_decision()`(action=COMPLETE、executable=True),静默宣称任务完成——违反 NFR「解析失败/非法动作 → skip + 诊断,绝不静默 COMPLETE」;产品端会直接结束轮次并总结"已完成",benchmark 端误判 complete | 改为不可执行降级(如 `(WAIT, {}, skip_reason="vlm_unavailable")` 借既有 skip 阶梯走 ASK)或让异常上抛由 engine 判 turn failed;修法需用户拍板(涉及行为语义) |

> **已修复(方案 a,用户拍板)**:`_fallback_decision` 返回 `(WAIT, {}, "vlm_unavailable")` 不可执行 skip,连续失败经 skip 连击阶梯升级 ASK;新增用例 `test_vlm_unavailable_degrades_to_skip_not_complete` 锁定。

### 🟡 Minor

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [x] | `backend/app/runtime/engine.py:421` + `:464` | 提示冗余 | `session.ask_reply` 在整个 turn 内不清空,ASK 恢复后的**每一步** current user 消息都重复携带同一段「用户对上次提问的回复」,步骤多了之后误导模型以为又有新回复 | engine 构造 AgentInput 传入后即消费(`session.ask_reply = ""`,与 `last_step_feedback` 同款"用后即清"模式) |
| [x] | `backend/app/agent/chat_agent.py:574-583` | 可观测性 | `_apply_skip_streak` 升级出的 ASK 不带 `reason_code`,与 L2 强制 ASK(`reason_code="stuck"`)不一致,benchmark 失败归因无法区分「格式降级连击」与「卡住」两种升级来源 | 补 `reason_code`(如复用 `"stuck"` 或新增 `"degraded"`),与 REASON_CODE_STUCK 常量并列 |
| [x] | `backend/app/safety/policy.py:82-89` | API 设计 | `augment_risk_from_text` 直接 mutate 入参 `output` 并返回同一对象,调用方若再持有原引用会看到隐式变更 | 返回前用 `dataclasses.replace(output, ...)` 生成新对象,或在 docstring 显式注明 mutate 语义 |

> **已修复**:m-1 用后即清(与 `last_step_feedback` 同款);m-2 新增 `REASON_CODE_DEGRADED = "degraded"`,用例 `test_skip_streak_ask_carries_degraded_reason_code` 锁定;m-3 改 `dataclasses.replace` 纯函数,README §5.3-6 同步。

### 🔵 Info / Suggestions

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [x] | `backend/app/runtime/engine.py:83-89` + `backend/benchmark/aw_adapter.py:55-63` | 代码重复 | `_images_are_similar` 在 engine 与 adapter 各有一份(plan 已知决定:adapter 须避免 FastAPI 依赖,注释已说明) | 后续提取到无 FastAPI 依赖的共享纯工具模块(如 `app/utils/`),两处各删一份 |
| [ ] | `backend/app/safety/policy.py:61-67` | 防线校准 | `_RISK_KEYWORDS` 的 submit/delete 类词偏宽(「提交」「清除」),普通表单提交也会触发 high 确认,产品端可能过扰 | 上线观察误报数据后收窄词表或按 category 分级;benchmark 链路不受影响(不走 safety) |
| [x] | `backend/app/runtime/engine.py:528-536` | 可观测性 | 回执生成(变化判定)无日志,冒烟后排查「变化误判」(TYPE 后光标闪烁误报已变化等)时不便 | 加一行 `logger.debug("receipt: %s", last_step_feedback)` |
| [ ] | `backend/tests/test_engine_feedback.py:22-26` | 测试基建 | 跨测试文件 import `test_engine_ask_reask` 的 FakeController/scripted_factory,第三处使用时建议下沉 | 移到 `tests/fakes.py` 或 conftest 公共 helper |

> **已修复**:i-1 提取 `app/utils.py::images_are_similar`(纯 PIL、无 FastAPI,adapter 可导入,venv313 下验证通过),engine/adapter 各删本地副本;i-3 回执生成处加 `logger.debug`。
> **按建议暂缓**:i-2 等误报数据再收窄词表(现在动同样武断);i-4 建议本身是"第三处使用时"才下沉,目前仅两处。

## Acceptance Criteria Coverage

| AC | Test | Status |
|----|------|--------|
| AC-01: 单调用 + 新 schema + 降级语义 | `TestSingleCallLoop` ×2、`TestSlimSchema` ×4、`TestDegradation` ×2 | ✅ Covered(注:Major-1 的 API 失败路径未被测试锁定) |
| AC-02: trace 全量 append-only | `TestMessagesTrace` ×6 | ✅ Covered |
| AC-03: 回执注入 | `test_engine_feedback.py` ×5 | ✅ Covered |
| AC-04: 三级卡住阶梯 | `TestStuckLadder` ×3 | ✅ Covered |
| AC-05: ASK reason_code | `TestAskReasonCode` ×2 | ✅ Covered |
| AC-06: benchmark prompt 裁剪 + adapter 跑通 | `TestModeSwitch` ×3(prompt 侧) | ✅ prompt 侧 Covered;adapter 以 mode="benchmark" 跑通全部 18 任务 |
| AC-07: 冒烟 ≥ 7/18 | `run_benchmark.py --smoke` | ✅ **9/18 = 50.0%**(run 20260909-132723,基线 7/18 = 38.9%) |
| AC-08: pytest 全绿 + README 同步 | 全量 pytest | ✅ 132 passed |

## Verdict

- [x] ✅ Ready to merge
- [ ] 🟡 Merge after minor fixes (no re-review needed)
- [ ] 🟠 Requires fixes and re-review
- [ ] 🔴 Do not merge — significant issues found

> 2026-09-09 修复后更新:M-1(方案 a)/m-1/m-2/m-3/i-1/i-3 全部修复,全量 pytest **134 passed**(含 2 个新增锁定用例);i-2/i-4 按建议暂缓。AC-07 冒烟已完成:**9/18 = 50.0%**(run 20260909-132723),全部 8 项 AC 达标。
