# Implementation Summary: Agent 纯 ReAct 决策循环

日期:2026-09-09

## 状态

Step 1~7 完成(含 review 修复);全量 pytest **134 passed**。
**AC-07 冒烟跑分达标:9/18 = 50.0%**(2026-09-09,run `artifacts/benchmark/20260909-132723/`;
824 基线 7/18 = 38.9%,**+11.1pp**)。
观测指标:raw_output 字符数**中位数 249**(目标 <400 ✅;p75=420,max=1310,无输出爆炸);
平均 11.89 步/任务、约 70s/任务(214 步全程)。
825 中间基线跑分由用户拍板跳过,归因以 824 基线为共同参照。

## Files Modified

| 文件 | 变更 |
|---|---|
| `backend/app/agent/schemas.py` | `AgentInput` 新增 `last_step_feedback: str = ""`;`AgentOutput` 删除 `current_subgoal_index`/`ui_risk_elements` |
| `backend/app/agent/chat_agent.py` | 核心重写:`__init__(mode="product"\|"benchmark", disable_business_overrides=None)`(旧参数 deprecated 映射 benchmark);状态 `_trace`(append-only 文本 trace)/`_executed_sigs`/`_nochange_count`/`_skip_streak`;`_decide` 组装 `[system, user(goal)] + trace + [user(ask_reply+L1 症状+截图)]`;`_update_nochange_count`(回执「无变化」+ 连续相同 action+params 签名 → 计数,恢复/变化清零);`_normalize_schema` CLICK 校验 point+target(missing_point/missing_target)、ASK 清洗 question/options/reason_code;L2 强制 ASK(`ASK_ON_STUCK_STEPS=3`)、L1 症状注入(`STUCK_SOFT_STEPS=2`);删除 plan/elements/progress 轨道、CLICK-UI 对齐、首屏 OPEN override、`HISTORY_*` 常量 |
| `backend/app/safety/policy.py` | 新增 `_RISK_KEYWORDS`(支付/删除/通讯/授权/提交五类中文关键词)与 `augment_risk_from_text(output)`(仅 CLICK、读 `parameters["target"]`、命中强制 high、只升不降);`SafetyResult` 删 `ui_risk_elements`;删无调用方的旧 `check_action` |
| `backend/app/runtime/engine.py` | 回执生成:决策图 vs `_wait_for_stable_screen` 稳定图经 `_images_are_similar` 比对 → `上步 <action> <参数摘要(≤80字符)> 后页面已变化/无变化`,turn 局部变量注入下一步 `last_step_feedback`(用后即清);`augment_risk_from_text` 前置于 `assess_output`;STEP_COMPLETED 删 `current_subgoal_index`、RISK_DETECTED 删 `ui_risk_elements` |
| `backend/app/agent/gui_agent.py` | Mock step0 CLICK 补 `target: "中央按钮"`;docstring 同步 |
| `backend/benchmark/aw_adapter.py` | `ChatGuiAgent(mode="benchmark")`;模块级 `_images_are_similar` 副本(避免 FastAPI 依赖);`_prev_before`/`_last_executed` 状态 + `_build_feedback`(与 engine 同格式回执,skip 步置 None);`reset()` 清状态 |
| `backend/tests/README.md` | §4.1 加 `last_step_feedback`/`reason_code`;§4.2 Mock 表加 target;§4.5 标注被 §4.6 替代(仍有效部分注明);新增 §4.6 纯 ReAct 契约全文;§5.3 回执语义 + `augment_risk_from_text`;§7 映射表更新为 908 版 |
| `backend/tests/test_chat_agent_react_loop.py` | 全量重写:27 用例 9 类(单调用循环/瘦身 schema/messages trace/CLICK target/mode 开关/三级阶梯/WAIT/降级/reason_code) |
| `backend/tests/test_engine_feedback.py` | 新建:FakeController + monkeypatch `_wait_for_stable_screen`,覆盖首步空串/已变化/无变化/skip 步/无设备会话 |
| `docs/project.md` | Mission/Features/包表(app.agent、app.safety)/Agent 决策流水线图/Architecture Decisions(新增 2026-09-08 行)/Conventions(历史上下文、动作 schema)更新为 908 语义 |
| 前端 | 仅核对无改动:`RiskConfirmModal.tsx` 对 `ui_risk_elements` 为防御式读取(`Array.isArray`),字段消失自动退化 |

## Acceptance Criteria

| AC | 状态 | 验证 |
|---|---|---|
| AC-01 单调用 + 新 schema + 降级语义 | ✅ | `TestSingleCallLoop`(VLMStub 调用计数,旧三阶段方法不得回归)、`TestSlimSchema`、`TestDegradation`(非法 JSON/非法动作 → skip,不静默 COMPLETE) |
| AC-02 trace 全量 append-only | ✅ | `TestMessagesTrace`:前缀稳定/全量注入不截断/多模态仅当前一张/回执入 trace/skip 不入 |
| AC-03 回执注入 | ✅ | `test_engine_feedback.py`:页面已变化/无变化 → 下一步 `last_step_feedback` 携带对应确定性回执;首步与 skip 步空串 |
| AC-04 三级卡住阶梯 | ✅ | `TestStuckLadder`:L1(计数≥2 症状注入不强制)/L2(计数≥3 强制 ASK)/恢复清零 |
| AC-05 ASK reason_code | ✅ | `TestAskReasonCode`:合法值透传、缺失 tolerated |
| AC-06 benchmark prompt 裁剪 | ✅ | `TestModeSwitch`:benchmark prompt 无 risk 块/记忆/会话上下文;product 含 risk 块;`disable_business_overrides=True` deprecated 映射;adapter 以 mode="benchmark" 跑通全部 18 任务 |
| AC-07 冒烟 ≥ 7/18 | ✅ **9/18 = 50.0%** | `run_benchmark.py --smoke`(2026-09-09,run 20260909-132723);成功:AudioRecorder/Camera/ClockStopwatch/Markor/Recipe/SaveCopyOfReceipt/SimpleSmsSend/SystemWifiTurnOn/TurnOnWifiAndOpenApp |
| AC-08 pytest 全绿 | ✅ | `cd backend && ../.venv/Scripts/python.exe -m pytest -q` → **134 passed**(含 review 修复 2 个新增用例);tests/README.md 已同步为权威规格 |

## 测试

```
cd backend && ../.venv/Scripts/python.exe -m pytest -q
134 passed in 5.82s   # 含 review 修复新增的 2 个锁定用例
```

## Review 修复(2026-09-09)

`review.md` 全部可修项已完成,Verdict 更新为 ✅:

| Finding | 修复 |
|---|---|
| M-1 VLM 失败静默 COMPLETE | 方案 a:`_fallback_decision` → `(WAIT, {}, "vlm_unavailable")` 不可执行 skip,连续失败经 skip 连击阶梯升级 ASK;新增 `test_vlm_unavailable_degrades_to_skip_not_complete` |
| m-1 ask_reply 每步重复注入 | engine 构造 AgentInput 后 `session.ask_reply = ""`(用后即清,与 last_step_feedback 同款) |
| m-2 skip-streak ASK 缺 reason_code | 新增 `REASON_CODE_DEGRADED`;新增 `test_skip_streak_ask_carries_degraded_reason_code` |
| m-3 augment_risk_from_text mutate 入参 | 改 `dataclasses.replace` 纯函数,返回新对象 |
| i-1 `_images_are_similar` 两处重复 | 提取 `app/utils.py::images_are_similar`(纯 PIL,adapter 可导入,venv313 验证),engine/adapter 删本地副本 |
| i-3 回执无日志 | 生成处加 `logger.debug` |
| i-2 词表偏宽 / i-4 测试 fake 下沉 | 按建议暂缓(等误报数据 / 等第三处使用) |

同步更新:tests/README.md(§4.3/§4.6/§5.3-6/§7)、docs/project.md(包表加 `app.utils`)、aw_adapter docstring。

## 遗留与后续

- **冒烟失败归因(初步分类,9 个失败任务)**:
  - 数据录入类:ContactsAddContact / ExpenseAddSingle / SimpleCalendarAddOneEvent(多字段表单,SimpleCalendar 34 步耗尽)
  - 文件管理器导航类:FilesDeleteFile / VlcCreatePlaylist / RetroCreatePlaylist(目录层级导航+多选)
  - 复杂 UI/推理类:OsmAndFavorite(坐标输入)、BrowserMultiply(心算乘积)、SimpleDrawProCreateDrawing(命名保存)
  - 轨迹明细在 `artifacts/benchmark/20260909-132723/ep*/`(before.png / raw_output.txt / action.json 逐步落盘)
- v1.1 候选(优先级待用户定):降清历史图注入、按需验证调用、页面震荡检测
- 跑分环境备忘:模拟器须带 `-grpc 8554` 启动(`D:\Android\Sdk\emulator\emulator.exe -avd AndroidWorldAvd -no-snapshot -grpc 8554`);Windows 控制台跑分须 `PYTHONUTF8=1`(否则官方库打印 ✅ 崩 GBK);adb 用 `D:\Android\Sdk\platform-tools\adb.exe`(PATH 里是 MuMu 的)
