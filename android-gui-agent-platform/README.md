# Android GUI Agent Platform

基于浏览器的 Android GUI 自动化控制台。输入自然语言任务，连接 Android 真机或模拟器，Agent 自动截图、决策、执行，并通过 WebSocket 将截图和操作实时推送到界面。

---

## 系统架构

```
浏览器 (React)
  │  REST API
  │  WebSocket (实时事件)
  ▼
FastAPI 后端
  │  asyncio 任务循环
  ▼
RuntimeEngine
  ├── TaskRouter          → 按指令复杂度选择 Agent
  ├── Agent (act)         → 截图 + VLM 决策
  ├── EscalationPolicy    → 检测卡死信号，切换到 ReAct
  ├── SafetyPolicy        → 风险评估，high 暂停等待确认
  └── AndroidAdbController → adb 执行动作
```

**技术栈**

- 后端：Python 3.10+ · FastAPI · asyncio · SQLite / SQLAlchemy · Pydantic
- 前端：React · Vite · TypeScript · Tailwind CSS · Zustand
- 设备控制：adb 子进程，归一化 [0, 1000] 坐标系
- VLM：通过 OpenAI 兼容接口调用（默认 Doubao Vision）

---

## Agent 架构

### TaskRouter

每个任务启动时，Router 根据指令文本选择 Agent 类型：

| 路由 | Agent | 适用场景 |
|------|-------|---------|
| `simple` | SimpleGuiAgent | 单屏可完成的短指令（"打开抖音"） |
| `standard` | VlmGuiAgent | 默认；多步骤、目标明确的任务 |
| `react` | ReactGuiAgent | 指令模糊、含比较/推荐/条件判断 |

可通过环境变量 `AGENT_ROUTE_OVERRIDE=react/standard/simple` 强制指定路由。

### VlmGuiAgent（standard 路由）

三阶段流水线，每步三次 VLM 调用：

```
Planner → UIExtractor → ActionAnalyzer
```

- **Planner**：解析 `app_name` 和 `subgoals` 子目标列表（仅首步执行）
- **UIExtractor**：从截图提取可交互元素列表和页面类型
- **ActionAnalyzer**：结合子目标、UI 元素、历史动作，输出下一步动作及风险自评

### SimpleGuiAgent（simple 路由）

每步单次 VLM 调用，直接输出动作，适合单屏任务。

### ReactGuiAgent（react 路由 / 升级目标）

ReAct 风格：每步输出 `observation → analysis → candidates → decision` 思维链，再给出动作。支持 `adopt()` 接口从其他 Agent 接管上下文。

### EscalationPolicy

每步执行后评估以下信号，任意触发则将当前 Agent 替换为 ReactGuiAgent（每个任务只升级一次）：

| 信号 | 触发条件 |
|------|---------|
| `low_confidence` | `confidence < 0.45` |
| `no_element` | UIExtractor 返回空元素列表 |
| `repeat_action` | 最近 3 步动作完全相同 |
| `stuck_count` | Agent 内部 stuck 计数 ≥ 2 |
| `screen_stuck` | 最近 3 帧截图高度相似 |
| `plan_mismatch` | subgoal 下标回退 |

---

## 安全与 HITL

风险评估由 ActionAnalyzer 在同一次模型调用中完成，输出 `risk_level` / `risk_category` / `current_state` / `consequence` / `rollback_hint`。

| 风险等级 | 行为 |
|---------|------|
| `safe` | 直接执行 |
| `medium` | 广播 `risk.observed` 事件，前端 Log 标黄，不暂停 |
| `high` | 广播 `risk.detected`，任务暂停，前端弹窗等待人工确认 |

**high 风险类别**：`payment`（支付/转账）· `delete`（删除/清空）· `auth`（授权第三方）· `submit`（不可撤销提交）· `communication`（发送消息/拨号）· `system`（卸载/清除数据）

**medium 风险**：仅限进入收费/实名/授权页面之前的可返回入口动作，仍可 BACK 撤销。

---

## 动作 Schema

所有 Agent 输出统一使用以下动作，坐标为 [0, 1000] 归一化整数：

```
CLICK    {"point": [x, y]}
SCROLL   {"start_point": [x, y], "end_point": [x, y]}
TYPE     {"text": "..."}
OPEN     {"app_name": "..."}
BACK     {}
HOME     {}
COMPLETE {}
```

参数非法（如 CLICK 缺少 point）时，该步跳过执行并计入 stuck，不写入 history_actions，由 EscalationPolicy 检测后切换到 ReAct。

---

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- Android SDK Platform Tools（`adb` 已加入 PATH）
- VLM API Key（兼容 OpenAI 接口）

### 环境变量

在 `backend/` 目录下创建 `.env` 或直接设置：

```bash
VLM_API_URL=https://ark.cn-beijing.volces.com/api/v3
VLM_MODEL_ID=doubao-seed-1-6-vision-250815
VLM_API_KEY=your_api_key_here

# 可选
USE_MOCK_AGENT=false          # true 时跳过 VLM，使用脚本化 MockAgent
AGENT_ROUTE_OVERRIDE=         # 强制路由：simple / standard / react
WAIT_STABLE_MAX_SECONDS=25    # 截图稳定等待硬上限（秒）
```

### 启动后端

```bash
cd android-gui-agent-platform/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

PowerShell 快捷脚本：

```powershell
.\scripts\run_backend.ps1
```

首次运行会在 `backend/data/app.db` 创建 SQLite 数据库。

### 启动前端

```bash
cd android-gui-agent-platform/frontend
npm install
npm run dev
```

PowerShell 快捷脚本：

```powershell
.\scripts\run_frontend.ps1
```

前端默认运行于 `http://localhost:5173`。

### 连接设备

```bash
adb devices -l                    # 查看已连接设备
adb connect 127.0.0.1:5557        # MuMu 模拟器示例
```

也可通过后端接口确认：

```bash
curl http://localhost:8000/api/devices
```

---

## 使用流程

1. 打开 `http://localhost:5173`
2. 在 Dashboard 输入自然语言任务（例如："用支付宝查一下余额"）
3. 选择设备（留空则使用 Mock 模式，无需真实设备）
4. 点击 **Create Task** → 跳转到 TaskDetail 页面
5. 点击 **Start** 运行 Agent
6. 实时查看截图面板、步骤时间线、动作检查器
7. 遇到高风险操作时，前端弹出确认弹窗，点击**确认执行**或**取消任务**

---

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 创建任务 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情（含步骤） |
| POST | `/api/tasks/{id}/start` | 启动任务 |
| POST | `/api/tasks/{id}/pause` | 暂停任务 |
| POST | `/api/tasks/{id}/resume` | 恢复任务 |
| POST | `/api/tasks/{id}/stop` | 停止任务 |
| POST | `/api/tasks/{id}/confirm` | 确认高风险操作 |
| GET | `/api/devices` | 列出 adb 设备 |
| WS | `/ws/tasks/{id}` | 任务实时事件流 |

### WebSocket 事件

| 事件 | 说明 |
|------|------|
| `task.started` | 任务循环启动，携带 `instruction` |
| `task.routed` | 路由决策完成，携带 `route` / `reason` |
| `step.started` | 第 N 步开始 |
| `step.completed` | 第 N 步完成，携带 `screenshot_base64` / `action` / `parameters` / `confidence` / `risk_level` / `route` |
| `task.paused` | 任务已暂停 |
| `task.resumed` | 任务已恢复 |
| `task.finished` | 任务正常完成 |
| `task.failed` | 任务异常失败 |
| `task.stopped` | 任务被用户停止 |
| `risk.observed` | medium 风险观察（非阻塞），携带完整风险上下文 |
| `risk.detected` | high 风险检测，任务暂停等待确认，携带完整风险上下文 |
| `escalation.triggered` | Agent 升级到 ReAct，携带触发信号列表 |

---

## 项目结构

```
android-gui-agent-platform/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口，CORS，lifespan
│   │   ├── api/
│   │   │   ├── tasks.py               # 任务 REST 接口
│   │   │   └── devices.py             # 设备列表接口
│   │   ├── ws/
│   │   │   ├── task_stream.py         # WebSocket 路由
│   │   │   └── connection_manager.py  # 按 task_id 管理连接
│   │   ├── agent/
│   │   │   ├── schemas.py             # AgentInput / AgentOutput / 动作常量
│   │   │   ├── base.py                # GuiAgent Protocol
│   │   │   ├── vlm_agent.py           # VlmGuiAgent（Planner+UIExtractor+ActionAnalyzer）
│   │   │   ├── simple_agent.py        # SimpleGuiAgent（单次 VLM 调用）
│   │   │   ├── react_agent.py         # ReactGuiAgent（ReAct 推理链）
│   │   │   ├── gui_agent.py           # MockGuiAgent（无 VLM 测试用）
│   │   │   ├── router.py              # TaskRouter（路由决策）
│   │   │   └── escalation.py          # EscalationPolicy（升级决策）
│   │   ├── runtime/
│   │   │   ├── engine.py              # RuntimeEngine（异步任务主循环）
│   │   │   ├── session.py             # 单任务运行状态
│   │   │   └── events.py              # WSEvent 模型 + 事件常量
│   │   ├── device/
│   │   │   ├── base.py                # BaseDeviceController ABC
│   │   │   ├── android_adb.py         # AndroidAdbController
│   │   │   └── registry.py            # 设备单例注册表
│   │   ├── safety/
│   │   │   └── policy.py              # SafetyPolicy（风险评估 + HITL 判定）
│   │   └── storage/
│   │       ├── db.py                  # SQLAlchemy 引擎 + 会话
│   │       ├── models.py              # Task / TaskStep ORM
│   │       └── artifact_store.py      # 截图持久化
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx                    # 路由 + 导航
│       ├── pages/
│       │   ├── Dashboard.tsx          # 任务列表 + 创建表单
│       │   ├── TaskDetail.tsx         # 实时任务视图
│       │   └── Devices.tsx            # ADB 设备列表
│       ├── components/
│       │   ├── ScreenshotPanel.tsx    # 截图 + 动作叠加层
│       │   ├── Timeline.tsx           # 步骤历史
│       │   ├── ActionInspector.tsx    # 当前动作详情
│       │   └── RiskConfirmModal.tsx   # 高风险确认弹窗
│       ├── api/
│       │   ├── client.ts              # Axios REST 客户端
│       │   └── websocket.ts           # useTaskWebSocket Hook
│       └── stores/
│           └── taskStore.ts           # Zustand 全局状态
├── artifacts/                         # 任务截图存储
└── scripts/
    ├── run_backend.ps1
    └── run_frontend.ps1
```

---

## 坐标系

所有 Agent 输出坐标统一归一化到 `[0, 1000] × [0, 1000]`，不暴露真实像素。`AndroidAdbController` 在执行时根据设备实际分辨率换算为像素坐标。
