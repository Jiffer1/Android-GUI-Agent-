# Agent 纯 ReAct 决策循环(feature 908)

Implemented on: 2026-09-09

把 ChatGuiAgent 每步决策从「全量结构化输出(plan/elements/progress/risk 自评 + CLICK-UI 事前对齐)」重构为纯 ReAct 形态:每步一次 VLM 调用仅输出 thought/action/parameters(product 模式另含 risk 块);历史为 append-only 全量文本 trace(多模态仅当前一张截图);引擎/adapter 事后回执(决策图 vs 稳定图比对 → "页面已变化/无变化")替代事前防御;三级卡住阶梯(L1 症状注入自纠 → L2 强制 ASK);ASK 双模式语义(product 求助 / benchmark infeasible 判定 + reason_code);`ChatGuiAgent(mode=...)` 显式模式开关;高风险防线降级为文本关键词 `augment_risk_from_text`(纯函数)。

**验收结果**:8 项 AC 全达标;134 pytest 全绿;AndroidWorld 冒烟 **9/18 = 50.0%**(824 基线 7/18 = 38.9%,+11.1pp;run `artifacts/benchmark/20260909-132723/`);raw_output 中位数 249 字符(目标 <400 ✅)。

关键文件:`backend/app/agent/chat_agent.py`(核心重写)、`app/runtime/engine.py`(回执注入)、`app/safety/policy.py`(关键词防线)、`app/utils.py`(新增,images_are_similar 共享)、`benchmark/aw_adapter.py`(mode 迁移)。VLM 不可用降级为不可执行 skip(绝不静默 COMPLETE),经 skip 连击阶梯升级 ASK(reason_code="degraded")。

v1.1 候选(优先级待定):降清历史图注入、按需验证调用、页面震荡检测;失败任务归因与跑分环境备忘见 impl-summary.md。
