# Android GUI Agent Platform

基于浏览器的 Android GUI 自动化控制台。输入自然语言任务，连接 Android 设备或模拟器，即可实时观看 Agent 逐步执行，并将截图和操作流式传输到界面。

## 架构

```
浏览器 ──REST──► FastAPI ──asyncio task──► RuntimeEngine
                   │                           │
                 WebSocket                  adb 子进程
                   │                           │
                 React                    AndroidAdbController
```

- **后端**：Python + FastAPI + WebSocket + SQLite + SQLAlchemy + Pydantic
- **前端**：React + Vite + TypeScript + Tailwind CSS
- **设备控制**：adb 子进程，使用归一化 [0, 1000] 坐标系
- **Agent**：MockGuiAgent（脚本化）— 后续替换为真实 VLM Agent

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- （可选）Android SDK Platform Tools，且 `adb` 已加入 PATH

### 后端

```powershell
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

或使用脚本：
```powershell
.\scripts\run_backend.ps1
```

后端启动于 `http://localhost:8000`，首次运行时会在 `backend/data/app.db` 创建 SQLite 数据库。

### 前端

```powershell
cd frontend
npm install
npm run dev
```

或使用脚本：
```powershell
.\scripts\run_frontend.ps1
```

前端启动于 `http://localhost:5173`。

## 使用方法

1. 打开 `http://localhost:5173`
2. 在 Dashboard 页面输入任务指令（例如："打开设置并开启 Wi-Fi"）
3. 从下拉菜单选择设备（或留空使用模拟模式，无需真实设备）
4. 点击 **Create Task**，跳转到 TaskDetail 页面
5. 点击 **Start** 运行 Agent
6. 实时观看截图面板、时间线和操作检查器的更新

### 模拟模式

未连接设备时，MockGuiAgent 会执行一段脚本化序列：
- 步骤 0：CLICK 中心
- 步骤 1：SCROLL 向上
- 步骤 2：TYPE "hello world"
- 步骤 3：CLICK 底部
- 步骤 4+：COMPLETE

此模式可在没有硬件的情况下验证完整流程（WebSocket 流式传输、UI 更新、截图保存）。

### 真实设备

通过 USB 连接已开启 USB 调试的 Android 设备，或启动模拟器：

```bash
adb devices   # 确认设备已列出
```

在 Dashboard 创建任务时选择对应的设备序列号。

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 创建任务 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情 + 步骤 |
| POST | `/api/tasks/{id}/start` | 启动任务 |
| POST | `/api/tasks/{id}/pause` | 暂停任务 |
| POST | `/api/tasks/{id}/resume` | 恢复任务 |
| POST | `/api/tasks/{id}/stop` | 停止任务 |
| POST | `/api/tasks/{id}/confirm` | 确认高风险操作 |
| GET | `/api/devices` | 列出 adb 设备 |
| WS | `/ws/tasks/{id}` | 任务实时事件 |

### WebSocket 事件

| 事件 | 说明 |
|------|------|
| `task.started` | 任务循环已开始 |
| `step.started` | 第 N 步已开始 |
| `step.completed` | 第 N 步完成 — 包含 `screenshot_base64`、`action`、`parameters` |
| `task.paused` | 任务已暂停 |
| `task.resumed` | 任务已恢复 |
| `task.finished` | 任务正常完成 |
| `task.failed` | 任务失败并附带错误信息 |
| `task.stopped` | 任务被用户停止 |
| `risk.detected` | 检测到高风险操作 — 任务暂停等待确认 |

## 项目结构

```
android-gui-agent-platform/
  backend/
    app/
      main.py                  # FastAPI 应用、CORS、lifespan
      config/settings.py       # pydantic-settings 配置
      api/tasks.py             # 任务 REST 接口
      api/devices.py           # 设备列表接口
      ws/task_stream.py        # WebSocket 路由
      ws/connection_manager.py # WebSocket 连接注册表
      agent/schemas.py         # Action 常量、AgentInput/Output
      agent/gui_agent.py       # MockGuiAgent（替换为真实 VLM）
      runtime/engine.py        # RuntimeEngine — 异步任务循环
      runtime/session.py       # 单任务暂停/恢复/停止状态
      runtime/events.py        # WSEvent 模型 + 事件常量
      device/base.py           # BaseDeviceController ABC
      device/android_adb.py    # AndroidAdbController（adb 子进程）
      device/registry.py       # 设备单例注册表
      safety/policy.py         # 基于关键词的风险检测
      storage/db.py            # SQLAlchemy 引擎 + 会话
      storage/models.py        # Task + TaskStep ORM 模型
      storage/artifact_store.py # 截图持久化
    requirements.txt
  frontend/
    src/
      App.tsx                  # 路由 + 导航
      pages/Dashboard.tsx      # 任务列表 + 创建表单
      pages/TaskDetail.tsx     # 实时任务视图
      pages/Devices.tsx        # ADB 设备列表
      components/ScreenshotPanel.tsx  # 截图 + 操作叠加层
      components/Timeline.tsx         # 步骤历史列表
      components/ActionInspector.tsx  # 当前操作详情
      components/DevicePanel.tsx      # 设备选择器
      components/RiskConfirmModal.tsx # 高风险确认弹窗
      api/client.ts            # Axios REST 客户端
      api/websocket.ts         # useTaskWebSocket Hook
      stores/taskStore.ts      # Zustand 状态管理
  artifacts/                   # 每个任务的截图存储
  scripts/
    run_backend.ps1
    run_frontend.ps1
```

## 替换 MockGuiAgent

`backend/app/agent/gui_agent.py` 中的 `MockGuiAgent` 实现了一个简单接口：

```python
class MyRealAgent:
    def reset(self): ...
    def act(self, input_data: AgentInput) -> AgentOutput: ...
```

`AgentInput` 提供 `instruction`、`current_image`（PIL Image）、`step_count` 和 `history_actions`。
`AgentOutput` 返回 `action`（CLICK/SCROLL/TYPE/OPEN/COMPLETE/BACK/HOME 之一）、`parameters` 和 `raw_output`。

在 `backend/app/runtime/engine.py` 第 `agent = MockGuiAgent()` 行替换为你的 Agent。

## 安全策略

`backend/app/safety/policy.py` 中的 `SafetyPolicy` 会检测高风险关键词（支付、删除、授权等）和支付类应用。触发后任务暂停，前端显示确认弹窗。可按需扩展 `HIGH_RISK_KEYWORDS` 和 `PAYMENT_APPS`。

## 坐标系

所有 Agent 操作均使用 X、Y 轴归一化到 `[0, 1000]` 范围的坐标。`AndroidAdbController` 在执行时会根据设备实际屏幕尺寸将其转换为真实像素坐标。
