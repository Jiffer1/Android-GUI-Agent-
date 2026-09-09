# Implementation Plan: Agent 纯 ReAct 决策循环

## Overview

TDD 契约先行:先改权威规格(`tests/README.md`)与契约测试(红),再自内向外实现——schemas → chat_agent(核心重写)→ engine(回执注入/事件清理)→ safety 关键词规则 → Mock/adapter 迁移(绿),最后 AndroidWorld 冒烟跑分对照验收。

核心数据流(feature 908 起):

```text
ChatGuiAgent.act(每步一次 VLM 调用,输出仅 thought/action/parameters[+product: risk 块])
  输入:instruction + [product: conversation_context/memory_text/ask_reply]
        + messages trace(assistant(thought+action) / user(回执) 交替,agent 实例内自持,
          全量不截断)+ 当前一张高清截图
  <- engine/adapter 每步传入 feedback:上一步(动作,参数,页面已变化/无变化/未执行)
```

## Architecture Decisions

| 决策 | 理由 |
|---|---|
| trace 由 **agent 实例内自持**(`self._messages`),engine/adapter 不组装历史 | 沿 825 惯例「历史为 agent turn 内自持状态」;engine 与 adapter 两条调用路径零重复逻辑 |
| 回执(feedback)由**调用方计算**经 `AgentInput.last_step_feedback: str` 传入 | 「页面变没变」的判定需要前后截图,engine(`_wait_for_stable_screen` 前后图)与 adapter(上一步 before vs 当前 state)各自持有;扩展 AgentInput 一字段推翻 825 的「不扩展」约束,理由:回执本质是 engine→agent 的环境输入,与历史自持不冲突 |
| 三级卡住在 **agent 内部**实现(基于自持 trace 的 action/参数重复 + feedback 计数) | engine 的 stuck 相关代码无需感知;`ASK_ON_STUCK_STEPS` 常量语义映射为 L2 阈值,新增 L1 软阈值常量 |
| `mode: "product" \| "benchmark"` 显式参数替代 `disable_business_overrides` | prompt 按模式组装裁剪(FR-06/07);旧参数保留过渡兼容(deprecated) |
| 高风险关键词规则放 `app/safety/policy.py`,engine 在 assess 前调用 | 防线集中一处,`assess_output` 结构不变 |
| 不做双 schema 开关、不做降清图 | git 即回滚;一次一个变量,冒烟归因干净 |

## Implementation Steps

### Step 1: 契约先行(TDD 红)
- [x] 更新 `tests/README.md`:§4.3/§4.5 重写为新 schema 契约(输出字段、`target`/`reason_code`、trace 全量注入、`last_step_feedback`、三级阶梯、mode 开关),§5.3 增回执注入语义,§7 映射表同步
- [x] 重写 `tests/test_chat_agent_react_loop.py`:AC-01(单调用+新 JSON schema+降级语义:非法动作/解析失败→skip 不静默 COMPLETE)、AC-02(trace 全量/append-only/无历史图)、AC-04(L1 症状注入不强制 ASK、L2 强制 ASK)、AC-05(reason_code)
- [x] 扩展 `tests/test_engine_ask_reask.py`(或新增 `test_engine_feedback.py`):AC-03(mock 控制器页面变化/无变化两情形 → 下一步 AgentInput.last_step_feedback 携带对应回执)
- [x] 运行确认新测试红、存量测试状态清点

### Step 2: schemas.py
- [x] `AgentInput` 新增 `last_step_feedback: str = ""`(默认空,兼容 Mock/adapter 旧调用)
- [x] `AgentOutput` 删除 `current_subgoal_index`、`ui_risk_elements`;其余保留(product 确认流依赖 risk 系字段)
- [x] 动作常量、ALL_ACTIONS 不变
- 文件:`app/agent/schemas.py`

### Step 3: chat_agent.py 核心重写
- [x] `__init__(self, mode: str = "product", disable_business_overrides: Optional[bool] = None)`(旧参数 deprecated 映射到 mode);`reset()` 清 trace/计数器
- [x] 内部状态:`self._messages: List[Dict]`(assistant/user 交替)、`self._repeat_count`(action+参数重复 + feedback 无变化计数)
- [x] `_decide()` 重写:
  - system prompt 按 mode 组装:公共段(9 动作、`target` 必填、thought 含观察/计划/理由、COMPLETE/ASK/WAIT 语义、降级即 skip 的诚实性约定);benchmark 附加段(infeasible 判定规则:无结果页立即判/前置缺失判/反复失败判/仅不确定禁止 ASK);product 附加段(risk 块、ask_reply 优先、会话上下文/记忆、首屏 OPEN 引导)
  - user content:instruction + [product 注入] + L1 症状提示(触发时)+ 上一步回执已含于 trace,末尾当前截图
  - 解析:thought/action/parameters(+product: risk 块);`target`/`reason_code` 归一化;沿用 `_extract_json_object`/`_extract_action_fallback`
- [x] `act()` 重排:决策 → `_normalize_schema`(CLICK 校验 target 非空替代 CLICK-UI 对齐;point 合法性不变)→ 三级阶梯判定 → trace append(assistant 条目;skip/ASK/COMPLETE 是否入 trace 按 README 契约定)→ 回执占位由调用方补
- [x] 删除:`_update_plan`/`_clean_elements`/`_click_point_matches_ui`/`_matched_element`/`_postprocess_action`(首屏 override)及 plan/elements/progress 相关常量(`HISTORY_*` 改为无窗口语义后移除)
- [x] `_apply_skip_streak` 保留;`_stuck_count` 信号源改为上述 `_repeat_count`
- 文件:`app/agent/chat_agent.py`

### Step 4: engine.py + safety
- [x] 回执生成:动作执行前决策图 vs `_wait_for_stable_screen` 返回图,`_images_are_similar` 判定 → 组装 `last_step_feedback`(含动作/参数摘要/变化结论;skip 步=「未执行(原因)」;ASK/WAIT 语义按 README);保存于 turn 循环局部变量,注入下一步 `AgentInput`
- [x] 首步/ASK 恢复步 feedback 为空串
- [x] `safety/policy.py` 新增 `augment_risk_from_text(output) -> AgentOutput`:CLICK 命中关键词(支付/转账/删除/发送/拨号/授权类词表)→ risk_level 强制 high;engine 在 `assess_output` 前调用(product 模式)
- [x] STEP_COMPLETED 事件删 `ui_risk_elements`/`current_subgoal_index` 字段;`ui_risk_elements` 派生逻辑删
- [x] 核对前端 `chatStore.ts`/组件对被删字段无引用(已确认无渲染依赖,仅核对)
- 文件:`app/runtime/engine.py`、`app/safety/policy.py`

### Step 5: Mock + adapter 迁移(绿)
- [x] `MockGuiAgent`:step0 CLICK 补 `target`;确认新 AgentOutput 构造兼容
- [x] `benchmark/aw_adapter.py`:`ChatGuiAgent(mode="benchmark")`;step() 内比对上一步 before 截图与当前 state.pixels → `last_step_feedback` 传入;`reset()` 已调 `agent.reset()` 天然清 trace
- [x] 全量测试转绿:`cd backend && ../.venv/Scripts/python.exe -m pytest -q`;清理 conftest 中 stub 断言的旧字段引用
- 文件:`app/agent/gui_agent.py`、`benchmark/aw_adapter.py`、`tests/conftest.py`

### Step 6: 冒烟跑分验收
- [x] (前置,建议、待用户确认)跑当前 825 代码冒烟 18 任务取中间基线,落盘归档 —— **用户拍板跳过,直接实现 908;归因以 824 基线(7/18)为共同参照**
- [x] 908 代码冒烟 18 任务,成功率 ≥ 7/18(AC-07);记录每步 raw_output 字符数中位数(观测指标,<400 目标) —— **9/18 = 50.0% ✅**(2026-09-09,run 20260909-132723);中位数 **249 字符**(<400 ✅,max 1310),平均 11.89 步/任务
- [x] 失败任务归因分类(按 reason_code/轨迹),决定 v1.1(降清图/验证调用)优先级 —— 初步分类:数据录入类(Contacts/Expense/Calendar)、文件管理器导航类(FilesDelete/VLC/Retro)、复杂 UI(OsmAnd/Browser 算术)/Draw 保存;v1.1 优先级待用户定
- 文件:`benchmark/run_benchmark.py`(不改代码,只运行)

### Step 7: 收尾
- [x] `docs/project.md` 更新(Mission/Features/Architecture 决策表/Conventions 中 825 相关段落改为 908 语义)
- [x] impl-summary.md

## Acceptance Criteria Mapping

| AC | Verified By |
|----|-------------|
| AC-01 单调用+新 schema+降级语义 | `test_chat_agent_react_loop.py::test_single_call_schema_*`、`test_degrade_*`(VLMStub 计数) |
| AC-02 trace 全量 append-only | `test_chat_agent_react_loop.py::test_trace_full_injection`、`test_no_history_screenshot` |
| AC-03 回执注入 | `test_engine_feedback.py::test_feedback_changed/unchanged/skipped` |
| AC-04 三级阶梯 | `test_chat_agent_react_loop.py::test_stuck_l1_symptom`、`test_stuck_l2_force_ask` |
| AC-05 reason_code | `test_chat_agent_react_loop.py::test_ask_reason_code` |
| AC-06 benchmark prompt 裁剪 | `test_chat_agent_react_loop.py::test_benchmark_mode_prompt`(断言不含 risk/记忆/上下文段) |
| AC-07 冒烟 ≥ 7/18 | `benchmark/run_benchmark.py` 运行记录(人工归档) |
| AC-08 pytest 全绿 | 全量 pytest + README 同步 |

## Risks & Mitigations

- **无 elements 后坐标 grounding 质量未知**(doubao grounding 应强,但冒烟前无实证)→ 冒烟对照 824 基线兜底;若跌分,v1.1 降清历史图先行,再考虑按需验证调用
- **页面变化误判**:TYPE 后光标/时钟变化 → 误报「已变化」;短距 SCROLL → 误报「无变化」→ 沿用现有阈值并按 action 分档容忍;L1 软阈值设计本身容忍单次误判(2 次才触发)
- **trace 无上限的 prompt 膨胀**:MAX_STEPS 封顶 ≈5K token,可接受;若超预期,在 agent 侧加字符数上限的保底截断(头尾保留)
- **adapter 行为变化影响跑分可比性**:feedback 机制使 benchmark 步进语义改变 → 这正是 feature 目的地(事后反馈),归因时以 824 基线为共同参照

## Estimated Complexity

**Medium** — 无新模块/表/端点,集中在 chat_agent.py 一处核心重写(~40% 代码删除)+ engine 回执注入;测试重写量大但模式已建立(VLMStub/mock 控制器基建齐全);跑分验收依赖外部环境(emulator + 独立 venv313)。
