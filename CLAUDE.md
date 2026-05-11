# CLAUDE.md

本文件用于指导 Claude Code 在本仓库中工作。这里仅记录当前已经存在、相对稳定的项目结构和开发约定；尚未落地的 Agent 架构设想不要写成强约束，避免后续实现方向调整时产生冲突。

## 仓库概览

仓库中包含两个相互独立但技术路线相关的项目：

- **`android-gui-agent-platform/`**：Android GUI Agent 可视化控制平台。用户在浏览器中提交自然语言任务，后端 Agent 根据手机截图做决策，并通过 ADB 控制 Android 真机或模拟器执行动作。前端通过 WebSocket 实时展示截图、动作轨迹和任务状态。
- **`code-for-student/`**：GUI Agent 参考实现和离线评测项目，核心是 `Planner / UIExtractor / ActionAnalyzer` 三阶段 Agent 流水线。

---

## Android GUI Agent Platform

### 启动后端

```bash
cd android-gui-agent-platform/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

PowerShell 下也可以使用：

```powershell
cd D:\guiagent\android-gui-agent-platform\backend
D:\guiagent\android-gui-agent-platform\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000
```

### 启动前端

```bash
cd android-gui-agent-platform/frontend
npm install
npm run dev        # 开发服务器，默认 http://localhost:5173
npm run build      # 生产构建，输出 dist/
```

PowerShell 便捷脚本：

- `scripts/run_backend.ps1`
- `scripts/run_frontend.ps1`

### ADB 设备检查

```powershell
adb devices -l
```

MuMu 模拟器常见连接方式：

```powershell
adb connect 127.0.0.1:5557
```

如果网页无设备，优先确认后端接口：

```powershell
Invoke-RestMethod http://localhost:8000/api/devices
```

---

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

### 后端关键模块

| 模块 | 作用 |
|---|---|
| `backend/app/main.py` | FastAPI 应用入口，注册 REST、WebSocket 路由，并在启动时初始化数据目录和数据库表 |
| `backend/app/api/tasks.py` | 任务 REST API：创建、查询、启动、暂停、恢复、停止、确认 |
| `backend/app/api/devices.py` | 设备 REST API：列出当前 ADB 设备 |
| `backend/app/runtime/engine.py` | 异步任务执行循环：调用 Agent、执行设备动作、做安全检查、保存步骤、广播 WebSocket 事件 |
| `backend/app/runtime/session.py` | 单任务运行状态：暂停、恢复、停止、人工确认 |
| `backend/app/runtime/events.py` | WebSocket 事件常量和事件结构 |
| `backend/app/agent/vlm_agent.py` | 当前真实 VLM Agent，实现 Planner / UIExtractor / ActionAnalyzer 三阶段决策 |
| `backend/app/agent/gui_agent.py` | Mock Agent，用于无模型或无设备时验证运行链路 |
| `backend/app/agent/schemas.py` | Agent 输入输出结构和动作常量 |
| `backend/app/device/android_adb.py` | ADB 控制器，负责截图、点击、滑动、输入、打开应用、返回、Home |
| `backend/app/device/registry.py` | ADB 设备发现和控制器缓存 |
| `backend/app/safety/policy.py` | 当前关键词式风险检测，命中高风险动作时暂停等待用户确认 |
| `backend/app/storage/db.py` | SQLAlchemy 数据库连接和 session 管理 |
| `backend/app/storage/models.py` | SQLAlchemy ORM：`Task` 和 `TaskStep` |
| `backend/app/storage/artifact_store.py` | 保存任务截图 artifacts |
| `backend/app/ws/task_stream.py` | 任务 WebSocket 入口 |
| `backend/app/ws/connection_manager.py` | 按 task_id 管理 WebSocket 连接并广播事件 |

### 前端关键模块

- `frontend/src/App.tsx`：React Router，页面包括 Dashboard、TaskDetail、Devices。
- `frontend/src/api/client.ts`：REST API 客户端。
- `frontend/src/api/websocket.ts`：任务 WebSocket hook。
- `frontend/src/stores/taskStore.ts`：Zustand 全局任务状态。
- `frontend/src/pages/Dashboard.tsx`：创建任务、查看任务列表。
- `frontend/src/pages/TaskDetail.tsx`：实时截图、动作时间线、控制按钮、风险弹窗。
- `frontend/src/pages/Devices.tsx`：查看 ADB 设备。
- `frontend/src/components/`：截图面板、动作检查器、时间线、风险确认弹窗等展示组件。

---

## 当前 Agent 行为

当前后端使用 `VlmGuiAgent`。它是固定三阶段流水线：

```text
Planner -> UIExtractor -> ActionAnalyzer -> RuntimeEngine
```

- Planner：根据用户任务生成 `app_name` 和 `subgoals`。
- UIExtractor：根据当前截图提取相关 UI 元素和页面类型。
- ActionAnalyzer：结合任务、子目标、当前 UI、历史动作和 progress，输出下一步动作。
- RuntimeEngine：对 Agent 输出做安全检查、动作分发、截图保存和 WebSocket 广播。

当前动作 schema 定义在 `backend/app/agent/schemas.py`：

```text
CLICK:    {"point": [x, y]}
SCROLL:   {"start_point": [x, y], "end_point": [x, y]}
TYPE:     {"text": "..."}
OPEN:     {"app_name": "..."}
BACK:     {}
HOME:     {}
COMPLETE: {}
```

所有设备坐标统一使用 `[0, 1000] x [0, 1000]` 归一化坐标，不直接暴露真实像素坐标。`AndroidAdbController` 会根据屏幕尺寸转换为真实像素。

---

## 当前安全确认机制

当前安全逻辑位于 `backend/app/safety/policy.py`，主要基于关键词识别高风险动作。

RuntimeEngine 在每一步 Agent 输出动作后调用安全检查：

```text
AgentOutput -> check_action -> safe: execute
                         -> unsafe: pause + broadcast risk.detected
```

前端通过 `RiskConfirmModal` 展示风险确认弹窗。用户确认后，后端继续执行；用户取消或停止任务时，任务终止。

如果未来要升级安全机制，应先保持现有 pause/resume/confirm 行为兼容，再逐步扩展数据结构和前端交互。

---

## 开发约定

- 不要随意改变 RuntimeEngine 对 Agent 的基本调用方式：`agent.act(input) -> AgentOutput`。
- 不要随意改变现有动作 schema；如需扩展动作，先保证旧动作仍兼容。
- 不要把真实像素坐标传给 Agent；Agent 输出必须保持归一化坐标。
- 后端长任务执行应继续使用异步任务循环，避免阻塞 REST 请求。
- WebSocket 事件字段变更时，需要同步更新前端 `taskStore.ts` 和相关组件。
- 数据库模型变更时，需要考虑现有 `data/app.db` 的兼容性。
- `artifacts/`、`data/`、`.venv/`、`node_modules/` 属于运行或依赖产物，通常不要纳入业务代码修改。

---

## 架构演进说明

Agent 架构后续可能继续演进，例如任务路由、复杂任务推理、异常恢复、人工确认等方向。但这些还不是当前已落地的稳定代码结构。

当用户明确要求实现某个新架构能力时，应先基于当前代码重新分析，再提出最小可行改动方案；不要仅凭旧规划直接创建大量未使用模块。

---

## Code for Student

```bash
cd code-for-student
pip install -r requirements.txt
python agent.py          # 运行 Agent
python test_runner.py    # 运行离线测试
```

该项目中的 `agent.py` 是 Planner / UIExtractor / ActionAnalyzer 三阶段参考实现。`agent_base.py` 定义标准输入输出协议和动作 schema。平台项目需要参考算法实现时，可以阅读 `code-for-student/agent.py` 中的提示词、JSON 容错、动作归一化和 progress 机制。
