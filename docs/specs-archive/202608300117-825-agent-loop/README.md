# Agent 单循环 ReAct 化（feature 825）

Implemented on: 2026-08-30

将 `ChatGuiAgent` 的三阶段流水线（Planner → UIExtractor → ActionAnalyzer，每步 2~3 次 VLM 调用）重写为单一 agent loop：每步恰一次 VLM 调用，统一 JSON schema 同步产出 plan / thought / elements / action / progress / 风险自评。历史上下文以结构化条目（page_type + elements[:10] + thought[:200] + action/parameters，最近 8 步）注入，多模态输入仅当前一张截图。新增 CLICK 命中 high_risk 元素的代码级强制风险确认（FR-04，独立于模型自评）；动作空间新增第 9 种动作 WAIT（加载期真实等待，复用屏幕稳定等待机制，等待期零 VLM 调用）。顺带修复 engine M-2（同轮第二次 ASK 不暂停）。

关键文件：`backend/app/agent/chat_agent.py`（完全重写）、`backend/app/agent/schemas.py`（+ACTION_WAIT）、`backend/app/runtime/engine.py`（两个等待预算字典条目 + ask_event clear）、`backend/benchmark/aw_adapter.py`（WAIT→官方 wait 映射）、`backend/tests/test_chat_agent_react_loop.py` + `test_engine_ask_reask.py`（31 个新契约用例）。

验证：全量 127 passed；review 无 Critical/Major（3 Minor 未修：plan 降级测试断言恒真、progress 非 str 强转、declined-replay 缺直接测试，见 review.md）。**遗留：AC-11 AndroidWorld 冒烟跑分（`.venv313`，n_task_combinations=1, seed=30）未执行**——feature.md 中该 AC 保持未勾，跑分口径建议以 826 基线 44.1% 为准。
