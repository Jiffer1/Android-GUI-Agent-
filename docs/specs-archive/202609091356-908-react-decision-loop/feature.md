# Feature: Agent 纯 ReAct 决策循环(schema 瘦身 + 事后反馈 + 三级卡住升级)

## Summary

把 ChatGuiAgent 的每步决策从「全量结构化输出(plan/elements/progress/risk 自评 + CLICK-UI 事前对齐)」重构为纯 ReAct 形态:每步仅输出 `thought`(自然语言观察+推理)+ `action` + 极简 `parameters`;删除 plan/subgoals 规划轨道与全页 elements 提取;历史改为 assistant/user 交替的**全量文本 trace**(不再压缩截断);引擎在动作执行后将**页面变化回执**作为 observation 注入下一步(事后反馈替代事前防御);卡住处理升级为「症状注入自纠 → 硬阈值强制 ASK」三级阶梯;ASK 语义按 product/benchmark 双模式劈开(benchmark 下即 infeasible 判定)。

动机(实测数据,2026-09-08):满配 20 元素 JSON ≈ 860–1340 token 且为**输出侧** token(单价高、逐 token 生成决定步内延迟);8 步结构化历史 ≈ 4.3K–6.6K token,而文本 trace 更便宜且无截断失真;CLICK-UI 自洽对齐防线强度低(模型幻觉坐标时通常也会把幻觉元素列进自己的清单),事后反馈可覆盖坐标幻觉 + 一大类非坐标错误(点错、页面未响应、加载中误判)。

## User Stories

- 作为跑分迭代者,我要每步 VLM 调用的输出短、历史便宜,以便 116 任务全量跑分的时间与成本可控、实验迭代更快。
- 作为产品用户,我要 agent 出错时先拿到环境反馈自我纠正(换点法/滚动/返回),只在真搞不定时才问我,减少无效打扰。
- 作为 benchmark adapter,我需要 agent 对 infeasible 任务及时、依据明确地终止(而非烧满 30 步),以提升跑分诚实度。

## Functional Requirements

### FR-01: 输出 schema 瘦身
每步 VLM 输出 JSON 仅含:`thought`(自然语言:页面观察、关键所见、简要计划与动作理由)、`action`(9 种法定动作不变)、`parameters`。
- CLICK 的 `parameters` 新增必填 `target`(目标元素可见文本,供 UI 展示与事后诊断);其余动作参数不变。
- 删除输出字段:`plan`/`app_name`/`subgoals`/`current_subgoal_index`、`elements`、`page_type`、`progress`。
- 删除历史注入的 `structured_history` 结构化条目与 `previous_progress`。
- 删除 825 的首屏强制 OPEN override(其输入 page_type/app_name 已不存在),由 prompt 引导「任务起点通常先打开目标应用」替代(仅 product 模式;benchmark 本就已禁用)。

### FR-02: 历史 = 全量文本 trace
- 上下文以标准 messages 序列注入:每步 `assistant(thought + action)` / `user(observation)` 交替,append-only。
- trace 全量保留至 MAX_STEPS,不设滑窗、不截断 thought。
- 多模态输入:仅当前一张高清截图,无历史截图(降清历史图为 v1.1 迭代项,不在本 feature)。

### FR-03: 事后反馈(engine)
- 动作执行后,engine 利用已有的稳定等待期前后截图比对(`_images_are_similar`),生成系统回执注入下一步 observation:`上步 <action> <params>,页面已变化/无变化`(确定性短句,零 VLM 成本)。
- 删除 CLICK-UI 事前对齐(elements 已不存在);保留弱校验:point ∈ [0,1000]²、CLICK 时 target 非空。

### FR-04: 卡住三级阶梯
- L0 正常步:prompt 弱化 ASK,模型不主动操心「要不要问」。
- L1 软阈值:同动作+参数重复且页面无变化连续 2 次 → 将症状作为 observation 注入(「你已连续 2 次 <action>,页面无变化」),引导自纠(换点法/SCROLL/BACK)。
- L2 硬阈值:连续 3 次且自纠机会已给 → 代码强制 ASK(复用现有通道);benchmark 模式下由 adapter 映射 `infeasible`。
- stuck 信号源从 progress 字符串比较改为:回执「无变化」计数 + action/参数重复计数;`skip_streak`(格式降级)语义不变。
- 状态震荡检测(A→B→A 页面指纹)为二期,不在本 feature。

### FR-05: ASK 双模式语义 + reason_code
- product 模式:求助(歧义裁决、缺验证码等信息、确认意图)。
- benchmark 模式:infeasible 判定,prompt 给明确规则:页面明示无结果/未找到 → 立即判定(不进 stuck 流程);前置信息缺失(登录/验证码/权限)→ 判定;已尝试不同方法仍无进展 → 判定;**仅仅不确定 → 禁止 ASK,选最可能的继续执行**。
- ASK 的 `parameters` 新增可选 `reason_code`:`no_target` / `missing_info` / `ambiguity` / `stuck`(产品端分流展示,跑分端失败归因)。

### FR-06: 风险自评开关 + 防线降级
- product 模式:保留 risk 自评字段块(engine 确认流、`assess_output` 依赖不变)。
- benchmark 模式:risk 块从 prompt 裁掉(纯负担)。
- 825-FR04「CLICK 命中 high_risk 元素强制 high」降级为 **target/参数文本关键词规则**(如「立即支付/确认转账」类);`ui_risk_elements` 字段删除。用户已确认接受此降级。

### FR-07: product/benchmark 模式机制
- `ChatGuiAgent(disable_business_overrides=...)` 扩展为显式 `mode: "product" | "benchmark"`(adapter 迁移到新参数,旧参数可过渡兼容)。
- system prompt 按 mode 组装裁剪;benchmark 裁掉:risk 块、conversation_context/memory_text 注入、首屏引导 override、ASK 求助语义。
- `summarize_turn` 与记忆提取保留(turn 结束后独立调用,与主循环无关)。

### FR-08: 兼容面清理
- `AgentOutput`:删除 elements 派生字段(`ui_risk_elements`、`current_subgoal_index`),`stuck_count` 保留(信号源变更);`raw_output` 仍为自由 JSON(DB 不动)。
- `STEP_COMPLETED` WS 事件:删除 `ui_risk_elements`/`current_subgoal_index` 字段;前端确认无 elements 渲染依赖(已确认无)。
- `MockGuiAgent`、benchmark adapter、契约测试(`tests/test_chat_agent_react_loop.py` 等)与 `tests/README.md` 同步更新。
- 不保留旧 schema 双实现开关,git 即回滚。

## Acceptance Criteria

- [x] AC-01: 每步恰一次 VLM 调用;输出 JSON 仅含 thought/action/parameters(product 模式另含 risk 块);契约测试以 stub 锁定,含非法动作/缺字段的降级语义(降级为 skip,不静默 COMPLETE)。
- [x] AC-02: 第 N 步的 VLM 输入包含全部前序 thought/action/observation 文本 trace(无窗口、无截断),多模态仅当前一张截图;契约测试锁定 messages 结构为 append-only 交替序列。
- [x] AC-03: 集成测试(mock 控制器)验证:动作后页面变化/无变化两种情形下,下一步输入的 observation 携带对应确定性回执。
- [x] AC-04: 契约/集成测试验证三级阶梯:同动作+无变化 2 次 → observation 出现症状提示且不强制 ASK;第 3 次 → 输出 ASK(stuck 语义)。
- [x] AC-05: ASK 输出携带合法 `reason_code`(缺省时 tolerated 并计诊断)。
- [x] AC-06: benchmark 模式的 system prompt 不含 risk/记忆/会话上下文注入;adapter 以 `mode="benchmark"` 跑通冒烟子集。
- [x] AC-07: AndroidWorld 冒烟子集(18 任务)成功率 ≥ 7/18(824 基线 38.9%)。—— 实际 **9/18 = 50.0%**(2026-09-09,run 20260909-132723)
- [x] AC-08: `cd backend && ../.venv/Scripts/python.exe -m pytest -q` 全绿;`tests/README.md` 同步为权威规格。

## Technical Scope

### Affected Modules
- `app/agent/chat_agent.py` — 主改:_decide 重写(prompt 组装、解析、卡住信号)、模式开关
- `app/agent/schemas.py` — AgentOutput 字段清理
- `app/runtime/engine.py` — 回执生成与注入、stuck 信号源、STEP_COMPLETED 字段
- `app/safety/policy.py` — 高风险关键词规则(替代元素级命中)
- `benchmark/aw_adapter.py` — mode 迁移
- `tests/` — 契约测试重写 + README 同步
- 前端 — 仅核对 chatStore/components 对被删 WS 字段无引用(已确认无渲染依赖)

### New Components Required
- 无新文件/表/端点;变更集中在既有模块内

### Integration Points
- AndroidWorld harness(经 adapter 直连 `ChatGuiAgent.act()`,agent 改动直接影响跑分)
- 方舟 VLM API(输出变短直接改变成本/延迟;append-only 结构为后续 context caching 留基础)

## Non-Functional Requirements

- 性能:每步输出 token 目标 ~1500 → ~400 以内(以跑分轨迹 raw_output 字符数中位数统计为观测指标);全量跑分 wall-clock 预期约减半(观测项,非硬门槛)。
- 成本:历史 token 较 825 结构化条目下降(30 步封顶 ≈ 5K token)。
- 可靠性:降级语义不变(解析失败/非法动作 → skip + 诊断,绝不静默 COMPLETE)。
- 可测性:两个卡住阈值(2/3)为常量,冒烟可扫参。

## Out of Scope

- 降清历史图注入(v1.1,文本 trace 稳定后单独迭代验证)
- a11y tree / `uiautomator dump` 接入(二期,强坐标校验)
- thinking 模式实验、方舟 context caching 开启
- 页面震荡检测(需页面指纹)
- 按需验证调用(点击落空时的诊断第二调用,视失败数据再决定)
- ConversationEngine 产品链路重构(ASK 暂停/风险确认/WS/记忆均不变)

## Open Questions

- 首屏强制 OPEN override 删除后,product 模式 step 0 是否需要额外引导(默认:仅 prompt 引导,上线观察)。
- 825 的 AC-11 冒烟跑分从未执行:建议在动手改代码前先跑一次当前代码的冒烟取中间基线(便于归因 908 的分数变化),是否执行由用户定(不阻塞开发)。
