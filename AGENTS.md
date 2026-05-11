# AGENTS.md

本文件用于指导 Codex、Claude Code 以及其他 coding agent 在本仓库中工作。请优先阅读本文件，再进行代码修改、架构调整或问题排查。

## 仓库概览

仓库中包含两个项目：

- **`android-gui-agent-platform/`**：Android GUI Agent 可视化控制平台。用户在浏览器中提交自然语言任务，后端 Agent 根据手机截图做决策，并通过 ADB 控制 Android 真机或模拟器执行动作。
- **`code-for-student/`**：GUI Agent 参考实现和离线评测项目，核心是 `Planner / UIExtractor / ActionAnalyzer` 三阶段 Agent 流水线。

## 启动方式

后端：

```bash
cd android-gui-agent-platform/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd android-gui-agent-platform/frontend
npm install
npm run dev
```

ADB 设备检查：

```powershell
adb devices -l
```

MuMu 模拟器常见连接方式：

```powershell
adb connect 127.0.0.1:5557
```

## 当前平台架构

```text
Browser (React) --REST--> FastAPI Backend --asyncio--> RuntimeEngine
       ^                         |                         |
       |                         v                         v
       +------ WebSocket ---- task events           AndroidAdbController
                                                           |
                                                           v
                                                  Android device/emulator
```

## 关键模块

后端：

- `backend/app/runtime/engine.py`：异步任务执行循环。
- `backend/app/agent/vlm_agent.py`：当前真实 VLM Agent。
- `backend/app/agent/gui_agent.py`：Mock Agent。
- `backend/app/agent/schemas.py`：Agent 输入输出结构和动作常量。
- `backend/app/device/android_adb.py`：ADB 控制器。
- `backend/app/safety/policy.py`：当前关键词式风险检测。
- `backend/app/storage/models.py`：`Task` 和 `TaskStep` 数据模型。
- `backend/app/ws/connection_manager.py`：WebSocket 连接和广播。

前端：

- `frontend/src/App.tsx`：React Router。
- `frontend/src/api/client.ts`：REST API。
- `frontend/src/api/websocket.ts`：WebSocket hook。
- `frontend/src/stores/taskStore.ts`：Zustand 状态管理。
- `frontend/src/pages/Dashboard.tsx`：任务创建和任务列表。
- `frontend/src/pages/TaskDetail.tsx`：任务详情、截图、动作轨迹、风险确认。
- `frontend/src/pages/Devices.tsx`：设备列表。

## 坐标系统

所有设备坐标统一使用 `[0, 1000] x [0, 1000]` 归一化坐标。不要在 Agent 输出中直接使用真实像素坐标。

## 当前 Agent 行为

当前 `VlmGuiAgent` 是固定三阶段流水线：

```text
Planner -> UIExtractor -> ActionAnalyzer -> ActionNormalizer -> RuntimeEngine
```

- Planner：生成 `app_name` 和 `subgoals`。
- UIExtractor：从当前截图提取相关 UI 元素和页面类型。
- ActionAnalyzer：根据任务、子目标、当前 UI、历史动作和 progress 输出下一步动作。

## 计划中的自适应 Agent 架构升级

后续目标是将固定流水线升级为按任务复杂度路由的自适应 GUI Agent：

```text
TaskRouter
  -> SimpleUIAgent
  -> StandardPipelineAgent
  -> ReasoningReActAgent
  -> EscalationPolicy
  -> Human-in-the-Loop Gate
  -> ActionNormalizer
  -> RiskClassifier
```

### 三种执行路径

#### SimpleUIAgent

用于简单明确任务：

- 点击搜索框
- 返回上一页
- 回到桌面
- 输入指定文本
- 点击确定、取消、关闭弹窗
- 打开某个 App

链路：

```text
Screenshot -> UIExtractor -> DirectActionSelector -> ActionNormalizer
```

#### StandardPipelineAgent

用于目标明确但需要多步完成的任务。当前 `VlmGuiAgent` 应优先封装为该路径。

链路：

```text
Planner -> UIExtractor -> ActionAnalyzer -> ActionNormalizer
```

#### ReasoningReActAgent

用于模糊、复杂、需要比较候选或异常恢复的任务。

链路：

```text
VLM Observe -> Reasoning Think -> UI Grounding -> Act -> Reflect
```

适用场景：

- 找评分高、离我近的餐厅。
- 选择最合适路线。
- 找便宜且评价好的商品。
- 当前页面与计划不一致，需要恢复。
- Simple 或 Standard 路径卡住。

## TaskRouter 路由规则

Simple Path：

- 动作明确。
- 目标 UI 明确。
- 通常只依赖当前屏幕。
- 一般 1 到 2 步可完成。

Standard Path：

- 目标明确。
- 需要多个稳定页面步骤。
- 可以拆解为子目标。
- 不需要主观比较。

ReAct Path：

- 任务包含模糊词：合适、推荐、最好、最近、便宜、评分高、性价比。
- 需要比较多个候选。
- 需要用户偏好推理。
- 当前页面与任务明显不匹配。

TaskRouter 建议输出：

```json
{
  "path": "simple | standard | react",
  "confidence": 0.0,
  "reason": "路由原因",
  "expected_steps": 1,
  "requires_planning": false,
  "requires_comparison": false,
  "requires_current_screen_only": true
}
```

## EscalationPolicy：升级到 ReAct

Simple 和 Standard 路径遇到未知情况时，不要硬继续，应交给 ReAct。

升级条件：

- UIExtractor 没有提取到目标元素。
- 候选过多，无法判断选择哪个。
- 动作置信度低。
- 当前页面和预期 subgoal 不匹配。
- 连续多步 progress 没变化。
- 连续多步截图高度相似。
- 重复执行相同动作。
- 出现登录页、权限页、弹窗、错误页、加载页。
- 模型输出非法 action 或参数多次被兜底。

升级到 ReAct 时要保留：

- instruction
- current screenshot
- subgoals
- recent actions
- previous_progress
- ui_state
- failed_subgoal
- failure_type
- risk_level

## Human-in-the-Loop Gate

后续需要把 `safety/policy.py` 升级为正式的人工确认关卡，位置在动作归一化之后、ADB 执行之前：

```text
Agent Output -> ActionNormalizer -> RiskClassifier -> HITL Gate -> RuntimeEngine Execute
```

必须触发人工确认的场景：

- 支付、立即支付、确认付款。
- 提交订单、下单。
- 删除、清空、卸载。
- 转账、红包、银行卡绑定。
- 授权登录、敏感权限授权。
- 发送消息、发布内容。
- 拨打电话、立即呼叫、打车呼叫。
- 修改关键系统设置。
- 访问或上传隐私数据。

HITL 弹窗应展示：

- 当前状态。
- Agent 准备执行的动作。
- 目标元素。
- 风险类型。
- 可能后果。
- 推荐选择。
- 用户可选项：确认执行、取消、到此完成、修改指令。

Runtime 行为：

- `approve`：执行动作。
- `cancel`：停止任务。
- `complete_here`：不执行高风险动作，直接标记任务完成。
- `modify_instruction`：把用户反馈交给 ReAct 重新规划。

## 建议实现顺序

不要一次性替换当前 Agent。推荐分阶段：

1. 提取共享 `ActionNormalizer` 和风险分类数据结构。
2. 将当前 `VlmGuiAgent` 包装为 `StandardPipelineAgent`，保持行为不变。
3. 新增 `TaskRouter`，先只记录路由结果，不改变执行路径。
4. 新增 `SimpleUIAgent`。
5. 新增 `EscalationPolicy`。
6. 新增 `ReasoningReActAgent`。
7. 将 `safety/policy.py` 升级为 `RiskClassifier + HITL Gate`。
8. 扩展前端风险确认弹窗，支持 approve、cancel、complete_here、modify_instruction。
9. 保持 RuntimeEngine 对 Agent 的外部调用契约稳定：`agent.act(input) -> AgentOutput`。

建议新增模块：

```text
backend/app/agent/router.py
backend/app/agent/simple_agent.py
backend/app/agent/standard_agent.py
backend/app/agent/react_agent.py
backend/app/agent/escalation.py
backend/app/agent/action_normalizer.py
backend/app/safety/risk_classifier.py
backend/app/runtime/hitl.py
```

## Code for Student

```bash
cd code-for-student
pip install -r requirements.txt
python agent.py
python test_runner.py
```

该项目中的 `agent.py` 是 Planner / UIExtractor / ActionAnalyzer 三阶段参考实现。后续平台 Agent 架构升级时，可以参考其中的提示词、JSON 容错、动作归一化和 progress 机制。
