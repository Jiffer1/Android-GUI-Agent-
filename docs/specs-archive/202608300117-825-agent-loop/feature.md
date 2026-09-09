# Feature: ChatGuiAgent Agent Loop 架构演进（AndroidWorld 提分导向）

## Summary

抛弃固定三阶段流水线（Planner → UIExtractor → ActionAnalyzer），`ChatGuiAgent` 改为单一 agent loop：每步恰好一次 VLM 调用，输入会话上下文、长期记忆、当前截图与结构化历史（每步 UI 提取结果、thought 与已执行动作，替代历史截图注入，见 FR-07），直接输出本步推理（thought）、下一步动作（含 ASK / COMPLETE）、本次识别的 UI 元素、滚动进度摘要与风险自评。引擎契约（`agent.act(input) -> AgentOutput`）与安全确认机制、代码级硬约束全部保留；动作 schema 新增第 9 种动作 WAIT（加载期真实等待，见 FR-09），其余 8 种不变；同时将「CLICK 目标命中 high_risk 元素」从 prompt 软约束升级为代码级强制风险确认（独立于模型自评的第二道防线）。顺带修复 review.md M-2（`ask_event` 等待前未 clear，同轮第二次 ASK 不暂停）。

本重构以提升 AndroidWorld 基准成功率为首要量化目标：依赖 feature 824（AndroidWorld 测试框架）产出的基线分数作对照，合入后在相同配置下重跑冒烟子集验证不回退、力争提升；同时保持产品对话链路既有契约不回退。

## User Stories

- 作为用户，我希望每步决策只需 1 次 VLM 调用（原 2~3 次），这样任务执行延迟显著降低。
- 作为用户，我希望对话上下文直接驱动每一步决策（而非仅在规划阶段参考），这样多轮指代（「继续」「用刚才那个 App」）的消解更自然可靠。
- 作为用户，我希望即使 Agent 把点击高危按钮（立即支付等）误自评为 safe，系统也能强制弹出人工确认，这样我不会因模型误判蒙受损失。
- 作为开发者，我希望引擎调用契约与既有契约测试保持不变，这样本次重构的影响面收敛在 agent 内部。
- 作为开发者，我希望用 AndroidWorld 冒烟子集分数量化验证重构收益，这样「更低延迟/更好决策」的判断有数据支撑。

## Functional Requirements

### FR-01: 单循环决策（替代三阶段）
- `ChatGuiAgent.act()` 每步只发起一次 VLM 调用；删除 `_run_planner`、`_extract_ui` 独立阶段与 `subgoals` 独立状态。
- 原 Planner 的职责（指代消解、记忆偏好融入、app_name 判断）并入主决策 prompt。
- 原 UIExtractor 的职责（元素识别）合并为统一输出 schema 的 `elements` 字段。

### FR-02: 统一输出 schema（内嵌 ReAct：Think / Observe / Act 同步产出）
单次 VLM 调用输出一个 JSON，不引入无动作步（纯推理/纯观察不单独成步，每步仍必输出动作），包含：
- `plan`：子目标序列（仅 step 0 必须输出；后续步骤可输出修订版，省略=沿用，见 FR-05）
- `thought`：本步推理过程（ReAct 之 Think）：页面观察分析、指代消解结论、动作选择理由；自由文本可长可短，prompt 要求必填，代码容错（缺失=空字符串，不阻塞、不影响 `executable`）
- `elements`：本次识别的相关 UI 元素（ReAct 之 Observe；沿用现字段：`text/type/point/selected/high_risk`，≤20 个，`[0,1000]` 归一化坐标）——维持 CLICK-UI 坐标校验的数据来源
- `action` + `parameters`：9 种动作（原 8 种 + 新增 WAIT，含 ASK / COMPLETE；ReAct 之 Act），每步必输出
- `progress`：一句中文滚动摘要（已完成 + 当前页面状态 + 下一步目标），承担跨步历史压缩
- 风险自评：`risk_level / risk_category / current_state / consequence / rollback_hint / risk_reason / confidence / current_subgoal_index`（字段与现行为一致）
- `thought` 不新增 `AgentOutput` 契约字段：随 `raw_output`（完整 JSON）既有链路自然持久化到 `TurnStep`；截断版进入结构化历史跨步注入（见 FR-07）

### FR-03: 保留的代码级硬约束（行为不变）
- `_normalize_schema`：动作白名单（含 WAIT）、别名映射、必填参数校验（缺失 → `executable=False` + `skip_reason`）、坐标 clamp。
- CLICK-UI 坐标对齐校验：CLICK 坐标须命中本次输出 `elements` 之一（±30 tolerance），否则 skip（`click_off_ui`）。
- 覆写规则（以 826 合入后现状为准）：桌面页且已知 app_name → 强制 OPEN。（「立即支付/呼叫 → 强制 COMPLETE」覆写已被 826 以 never silently declare completion 为由删除，本 feature 不恢复；高危防线由 FR-04 承担）
- 卡死检测：progress 连续 3 步不变（代码侧计数）→ 强制 ASK 求助。
- `max_steps` 兜底（引擎侧，不变）。

### FR-04: high_risk 元素点击强制风险确认（新增代码校验）
- CLICK 目标坐标命中 `elements` 中 `high_risk=true` 的元素（同 ±30 tolerance）时，无论模型自评如何，代码强制 `risk_level=high`，走现有 `waiting_confirm` 人工确认流程。
- 用户批准 → 执行；取消 → 按现有语义终止轮次并记入 declined 集合（同动作不再重复弹窗）。
- 该防线独立于模型自评，二者任一判 high 即触发确认。

### FR-05: plan 机制
- step 0：模型在输出 JSON 中携带 `plan`（子目标序列，含目标 app）。
- step>0：prompt 注入当前 plan，模型可输出修订后的 plan（省略=沿用上一步 plan）。
- plan 解析失败/为空：降级为 `[instruction]`，不阻塞执行。
- `current_subgoal_index` 语义保留（相对 plan 下标）。

### FR-06: M-2 修复（engine）
- ASK 分支在 `ask_event.wait()` 前 `clear()`（与 confirm 分支对称），确保同一轮次内第二次 ASK 正常暂停等待用户回复。

### FR-07: 上下文窗口控制（结构化历史，无历史截图）
- 历史不注入截图：多模态输入仅当前截图一张。历史以「结构化提取结果 + thought + 动作序列」表示，用结构化提取数据替代原始截屏，大幅压缩上下文体积。
- 每步历史条目 = 该步输出的结构化内容（`page_type` + `elements`，紧凑字段表示，条数上限 K，plan 阶段确定）+ 该步 `thought` 截断版（上限约 200 字符，plan 阶段结合实测 prompt 长度确定）+ 该步已执行动作（`action` + `parameters`）。
- 注入最近 N 步（默认 8，plan 阶段确定）；N 窗口之外的更早历史由 `progress` 滚动摘要承载。
- 结构化历史由 agent 实例内部维护（turn 生命周期内与 `self.progress` 同级的自持状态），不扩展 `AgentInput` / `AgentOutput` 契约；`AgentInput.history_actions` 按现契约继续传递，作为执行记录参照。
- `conversation_context`（最近 6 条消息）与 `memory_text`（4000 字符预算）注入策略不变。

### FR-08: 兼容性
- `agent.act(input) -> AgentOutput` 契约不变；`AgentInput` 现有字段语义不变。
- 动作 schema 为向后兼容扩展：`schemas.py` 新增 `ACTION_WAIT` 常量与 `ALL_ACTIONS` 条目，原 8 种动作定义与语义不变；WS 事件体系不变；前端动作名透传展示 WAIT，无结构变更。
- `MockGuiAgent` 与 `USE_MOCK_AGENT` 链路不变。
- `backend/tests/` 既有 56 个契约测试全部保持通过，不修改；新行为（FR-04、FR-09、每步单次调用）以新增测试覆盖。
- `summarize_turn` 保留（单次调用 + 失败降级，语义不变）。
- `AndroidWorldAdapter`（feature 824 引入）同步适配重构后的 agent（调用方式变化仅限 adapter 内部），WAIT 直接映射官方 `JSONAction(action_type="wait")`（官方动作空间原生支持，`json_action.py:50`），benchmark 链路不回退。

### FR-09: WAIT 动作（加载期真实等待，防密集轮询）
- 动机：无 WAIT 时「想等页面加载」只能输出会被校验拦下的动作（skip），而 skip 立即进入下一轮 VLM 调用——加载期间等于密集轮询，浪费 token。WAIT 给模型一个诚实的等待出口，并绑定一段真实等待时间。
- agent 侧：WAIT 为第 9 种合法动作，无必填参数（可选 `reason` 仅供展示/诊断），`executable=True`，默认 `risk_level=safe`；prompt 指明触发场景（`page_type=loading`、页面跳转/渲染未完成、等待上一步动作的延迟生效）。
- engine 侧：WAIT 不执行任何设备操作（`_execute_action` 现有 if/elif 链天然 no-op），但触发屏幕稳定等待——复用 `_wait_for_stable_screen`（轮询截图至连续相似即提前返回），专属等待预算：基础 2s、上限 20s（对齐 OPEN）。等待期间**不发起任何 VLM 调用**：一次 WAIT 覆盖一段加载期，替代加载期间的连续决策轮询。等待结束得到的稳定截图经既有 `stable_image` 机制直接作为下一步输入，不重复截屏。
- 与 skip 的语义区分：skip（无效动作被拦）= 立即进入下一轮决策；WAIT（主动等待）= 真实等待至页面稳定再决策。
- 防滥用：连续 WAIT 会使 `progress` 保持不变，触发既有卡死检测（连续 3 步 → 强制 ASK 求助），无需新增机制。
- WAIT 作为实际执行的动作记入 `history_actions` 与结构化历史（它是真实决策，占一步步数预算）。

## Acceptance Criteria

- [x] AC-01: 一个轮次内每步恰好发起 1 次 VLM 调用（含 step 0 的 plan 生成；无独立 Planner/UIExtractor 调用），以调用桩计数验证。
- [x] AC-02: 多轮上下文注入单循环决策 prompt 不回退（`test_multi_turn_context_carried_over` 等既有契约测试通过）。
- [x] AC-03: CLICK 坐标未命中任何 element（±30）→ skip（`click_off_ui`），不执行、记录 TurnStep。
- [x] AC-04: CLICK 命中 high_risk 元素且模型自评 safe → 仍触发 `waiting_confirm`；批准后执行、取消后轮次终止且同动作不再重复弹窗。
- [x] AC-05: 桌面页且已知 app_name → 强制 OPEN（826 后仅存的覆写规则；「立即支付 → COMPLETE」已废弃，高危点击由 AC-04 的强制确认防线承担）。
- [x] AC-06: progress 连续 3 步不变 → 强制 ASK；同轮第二次 ASK 在用户回复前保持等待（M-2 修复验证）。
- [x] AC-07: VLM 输出非法 JSON / 非法动作时单步降级不崩溃，轮次按现有降级语义继续或结束。
- [x] AC-08: 既有 56 个契约测试全部通过；`USE_MOCK_AGENT=1` 全链路冒烟（建会话→消息→ASK→回复→总结）通过。
- [x] AC-09: 旧三阶段代码（`_run_planner` / `_extract_ui` / 独立 `subgoals` 状态）删除，无残留引用。
- [x] AC-10: 每步输出 JSON 含非空 `thought` 且随 `raw_output` 持久化到 TurnStep；模型缺失 `thought` 时该步降级为空字符串，动作照常执行、`executable` 不受影响。
- [ ] AC-11: 重构合入后在 AndroidWorld 冒烟子集（与 824 基线相同配置）重跑，成功率不低于 824 基线。
- [x] AC-12: 单步决策 prompt 中历史仅由结构化条目（`page_type` / `elements` / `thought` 截断版 / `action` / `parameters`）与 `progress` 摘要构成，不含任何历史截图图像；历史窗口 N 与每步元素上限 K 符合 FR-07 配置（以调用桩捕获的实际 prompt 内容验证）。
- [x] AC-13: WAIT 为合法动作（`_normalize_schema` 通过、`executable=True`）；engine 收到 WAIT 后不执行设备操作、不发起 VLM 调用，直至屏幕稳定等待结束才进入下一步；连续 WAIT 触发既有卡死检测升级为 ASK；adapter 将 WAIT 映射为官方 `JSONAction(action_type="wait")`。

## Technical Scope

### Affected Modules
- `backend/app/agent/chat_agent.py` — 核心重写：单循环决策、统一输出 schema、plan 机制、high_risk 强制升级、WAIT 支持
- `backend/app/runtime/engine.py` — M-2（ask_event clear）+ WAIT 等待预算（`_STABLE_MAX` / `min_waits` 新增 WAIT 条目，无控制流变更）
- `backend/app/agent/schemas.py` — 新增 `ACTION_WAIT` 常量与 `ALL_ACTIONS` 条目（plan 暂不进入 AgentOutput，见 Open Questions）
- `backend/benchmark/`（feature 824 引入）— AndroidWorldAdapter 适配调用方式变化 + WAIT→官方 wait 映射
- `backend/tests/` — 新增用例（AC-01/03/04/06/10/13 对应）；既有用例不动
- `docs/project.md` — 实现完成后同步 Agent 行为描述与动作 schema（9 种）

### New Components Required
- 无新模块/表/端点；均为 `chat_agent.py` 内部重构。

### Integration Points
- VLM API（火山方舟）— 调用次数从每步 2~3 次降为 1 次
- `ConversationEngine` — act 契约不变，无感知
- `app/safety/policy.py::assess_output` — 输入不变；FR-04 的强制升级发生在 agent 输出组装阶段（assess 之前）
- AndroidWorld benchmark（feature 824）— 冒烟子集重跑验证 AC-11

## Non-Functional Requirements

- 性能：每步 VLM 调用 1 次，单步决策延迟约减半；prompt 体积有界且显著小于多模态历史截图方案（结构化历史窗口 N × 每步元素上限 K + progress 摘要 + 4000 字符记忆）。
- 可靠性：VLM 3 次重试（指数退避）、JSON 容错解析、各级降级路径全部保留。
- 安全：新增独立于模型自评的高危点击防线（FR-04）；既有风险确认流程与 declined 跳过机制不变。

## Out of Scope

- review.md 的 M-1（前端 paths 删除索引）、M-3（空小节头残留）及其余 MINOR/INFO findings
- 批量动作输出（单次输出多动作序列）
- 记忆的向量检索 / embedding
- 引擎循环、两级互斥、WS 事件、前端页面的任何变更
- `MockGuiAgent` 脚本变更
- AndroidWorld 框架本身的变更（feature 824 范畴）

## Open Questions

- 历史注入窗口 N 的具体值（默认 8，plan 阶段结合实测 prompt 长度确定）
- 每步历史条目的元素上限 K（当步 `elements` 全量 ≤20，历史条目是否截断到更小值如 8~10，结合实测 prompt 长度与 CLICK-UI 校验命中率确定）
- plan 修订的 prompt 表述（每步重新输出 vs 显式修订标记）— plan 阶段定
- `plan` 是否需要暴露到 `AgentOutput` 供引擎/前端展示（如 Timeline 显示当前子目标）— 倾向暂不暴露，保持契约最小
- benchmark 模式下 ASK/卡死检测/强制 COMPLETE 覆写的差异化配置（824 已引入绕过开关，本 feature 保持其语义连续）
