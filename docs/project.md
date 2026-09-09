# Project: Android GUI Agent Platform

## Mission

用户在浏览器中以对话方式（Chat 界面）驱动 Android GUI Agent：每条消息触发一个执行轮次（Turn），VLM Agent 每步以单次调用完成纯 ReAct 决策（thought + action + 极简参数，feature 908 起），历史以全量文本 trace（assistant/user 交替，无历史截图）注入上下文，动作执行后引擎注入确定性回执（页面已变化/无变化）作为事后反馈，通过 ADB 控制 Android 真机或模拟器执行动作；Agent 可反问（ASK）、高风险动作暂停等待人工确认（文本关键词防线）、页面加载期可输出 WAIT 真实等待；长期记忆以 Markdown 文件保存用户偏好与历史执行路径并注入决策上下文。WebSocket 实时回传截图、动作轨迹和轮次状态。

## Features
- **AndroidWorld 跑分框架**：Adapter 模式将 ChatGuiAgent 接入官方 android_world harness（`backend/benchmark/`，独立 venv313），冒烟 18 任务基线 7/18=38.9%（[归档](specs-archive/202608262018-androidworld-framework/)）
- **Agent 单循环 ReAct 化**：每步一次 VLM 调用统一输出 plan/thought/elements/action/风险；结构化历史（无历史截图）；CLICK 命中 high_risk 元素代码级强制确认；新增 WAIT 动作（[归档](specs-archive/202608300117-825-agent-loop/)；AC-11 AndroidWorld 冒烟跑分待执行）
- **Agent 纯 ReAct 决策循环**（feature 908）：输出瘦身至 thought/action/parameters（CLICK 带 target），删 plan/elements/page_type/progress 轨道；全量文本 trace 历史；引擎事后回执（已变化/无变化）替代 CLICK-UI 事前对齐；三级卡住阶梯（症状注入自纠→强制 ASK）；ASK 双模式语义（product 求助 / benchmark infeasible 判定 + reason_code）；product/benchmark 模式开关；高风险防线降级为文本关键词（[归档](specs-archive/202609091356-908-react-decision-loop/)；冒烟 **9/18 = 50.0%** vs 824 基线 7/18，raw_output 中位数 249 字符）

## Tech Stack

后端（`android-gui-agent-platform/backend/`）：
- Language: Python 3.13（venv 环境）
- Framework: FastAPI 0.115.0
- Build tool: pip + uvicorn 0.30.6（无打包构建，直接运行 `uvicorn app.main:app`）
- Database: SQLite（`data/app.db`）
- ORM: SQLAlchemy 2.0.36
- Migrations: 无（启动时 `init_db()` 自动建表；旧 tasks/task_steps 表显式 DROP）
- Messaging: 无外部消息队列（FastAPI 原生 WebSocket）
- Testing: pytest + pytest-asyncio（`backend/tests/`，TDD 契约测试，`tests/README.md` 为权威规格）
- Other: pydantic 2.9.2、pydantic-settings 2.5.2、Pillow 10.4.0、websockets 13.1、aiofiles 24.1.0、openai >=1.51.0（通过 OpenAI 兼容接口调用火山方舟 VLM，模型 `doubao-seed-1-6-vision-250815`）

前端（`android-gui-agent-platform/frontend/`）：
- Language: TypeScript 5.5.3
- Framework: React 18.3.1
- Build tool: Vite 5.4.8（`npm run dev` / `npm run build`）
- Other: react-router-dom 6.26.2、axios 1.7.7、zustand 5.0.0、Tailwind CSS 3.4.13 + PostCSS + autoprefixer

外部依赖：
- ADB（Android Debug Bridge）：设备发现、截图、点击、滑动、输入等控制
- 火山方舟 VLM API：视觉语言模型推理（API Key 通过 `.env` 配置）

## Architecture

模块化单体（按技术领域分模块），非微服务，非经典 controller/service/repository 三层。

整体数据流：

```text
Browser (React Chat) --REST--> FastAPI Backend --dedicated loop--> ConversationEngine
       ^                         |                                      |
       |                         v                                      v
       +-- WebSocket (conversation events)               AndroidAdbController
                                                                  |
                                                                  v
                                                         Android device/emulator
```

核心执行模型：`Conversation`（会话）→ 每条用户消息触发一个 `Turn`（轮次，状态机
pending/running/waiting_ask/waiting_confirm/finished/failed/stopped）→ 每步动作记录为
`TurnStep`，聊天消息（text/ask/risk/summary）记录为 `Message`。轮次协程运行在引擎
专用的后台事件循环线程上，生命周期与 HTTP 请求循环解耦。

后端包结构（`backend/app/`）：

| 包 | 职责 |
|---|---|
| `app.api` | REST 路由：conversations（CRUD/发消息/回复 ASK/风险确认/停止）、memory（查看/删除/清空）、devices（列出 ADB 设备） |
| `app.runtime` | ConversationEngine（轮次循环、ASK 等待/恢复、风险确认、总结、记忆写入）、ConversationSession、WebSocket 事件常量 events |
| `app.agent` | ChatGuiAgent（纯 ReAct 决策：每步一次 VLM 调用仅输出 thought/action/parameters[+product: risk 块]，agent 内自持全量文本 trace + summarize_turn）、MockGuiAgent（对话脚本）、schemas（输入输出结构与动作常量，含 ACTION_ASK / ACTION_WAIT、AgentInput.last_step_feedback） |
| `app.memory` | MemoryStore（md 文件读写 + threading.Lock）、retriever（检索注入 + 4000 字符预算）、extractor（VLM 自动提取 + 显式「记住」正则） |
| `app.device` | AndroidAdbController（截图/点击/滑动/输入/打开应用/返回/Home）、设备发现注册 registry |
| `app.safety` | assess_output（基于 Agent 自评风险字段）+ augment_risk_from_text（feature 908：CLICK 的 target/参数文本命中关键词 → 强制 high，纯函数返回新对象），高风险暂停等待用户确认 |
| `app.utils` | 无 FastAPI 依赖的共享纯工具（images_are_similar：engine 与 benchmark adapter 复用的截图相似度判定） |
| `app.storage` | SQLAlchemy 连接 db（含 init_db）、ORM 模型 models（Conversation/Turn/Message/TurnStep）、截图 artifact 存储 |
| `app.ws` | 会话 WebSocket 入口 conversation_stream、按 conversation_id 管理连接的 connection_manager（跨循环广播安全） |
| `app.config` | pydantic-settings 配置（数据库路径、artifacts 目录、CORS、VLM 凭证、MAX_STEPS） |

前端结构（`frontend/src/`）：
- `pages/`：Chat（默认页，三栏：会话列表 + 聊天流（含 ASK/风险卡片）+ 截图/时间线/动作面板）、Memory（记忆查看/删除/清空）、Devices
- `components/`：ScreenshotPanel、ActionInspector、Timeline、DevicePanel、RiskConfirmModal 等展示组件
- `api/`：REST 客户端 client.ts（conversationsApi/memoryApi/screenshotUrl）、会话 WebSocket hook websocket.ts（2s 重连 + 重连后 refetch）
- `stores/`：Zustand 全局状态 chatStore.ts（handleWSEvent 事件分发）

Agent 决策流水线（feature 908 起纯 ReAct）：

```text
ChatGuiAgent.act（每步一次 VLM 调用，输出仅 thought/action/parameters[+product: risk 块]）
  输入：[system（按 mode 组装）, user（goal [+product: 会话上下文/记忆]）]
        + 全量文本 trace（(assistant(thought+action), user(回执))*，append-only 不截断，
          无历史截图，多模态仅当前一张）
        + 当前 user（[ask_reply] [L1 症状提示] + 当前截图）
  <- engine/adapter 事后回执（经 AgentInput.last_step_feedback）：上一步执行后
     决策图 vs 稳定图比对 -> "上步 <action> <参数摘要> 后页面已变化/无变化"
  -> ConversationEngine（augment_risk_from_text 关键词防线 + assess_output -> 动作分发
     -> WAIT/动作后屏幕稳定等待 -> 回执生成 -> 截图保存 -> WebSocket 广播）
```

## Architecture Decisions

| Date | Decision | Rationale | Feature |
|------|----------|-----------|---------|
| 2026-08-26 | benchmark 经 `AndroidWorldAdapter`（`EnvironmentInteractingAgent` 子类）直连 `ChatGuiAgent.act()`，不经 ConversationEngine/DB/WS/记忆；ASK→`status(infeasible)` 无人值守语义；[0,1000] 坐标→像素换算与动作映射集中在 adapter 一处 | agent 核心零修改保基线真实性；绕开产品链路耦合 | [AndroidWorld 跑分框架](specs-archive/202608262018-androidworld-framework/) |
| 2026-08-30 | ChatGuiAgent 重构为单循环：每步恰一次 VLM 调用同步产出 plan/thought/elements/action/progress/风险（统一 JSON schema），替代三阶段流水线；历史上下文以结构化条目注入，为 agent 实例 turn 内自持状态，不扩展 `AgentInput`/`AgentOutput` | 每步 VLM 调用 2~3 次 → 1 次；契约面不变，既有契约测试与 benchmark adapter 全兼容 | [Agent 单循环 ReAct 化](specs-archive/202608300117-825-agent-loop/) |
| 2026-08-30 | 新增 WAIT 动作（第 9 种）：engine 侧无设备操作但触发 `_wait_for_stable_screen` 专属预算（基础 2s/上限 20s），等待期零 VLM 调用；benchmark 侧映射官方 `JSONAction(action_type="wait")`；防滥用由既有卡死检测兜底 | 给加载期一个诚实等待出口，替代「被拦 skip + 立即下一轮调用」的密集轮询，省 token | [Agent 单循环 ReAct 化](specs-archive/202608300117-825-agent-loop/) |
| 2026-08-26 | android_world 官方仓库克隆至 `third_party/`（gitignore）+ 独立 `.venv313`（Python 3.13，需 sqlite FTS4；APK 缓存于 `%TEMP%\android_world\app_data\`）与产品 venv 隔离；跑分用独立入口 `run_benchmark.py`，零修改第三方库 | 依赖冲突隔离；官方 run.py 不认 Windows adb 路径；保留第三方库可更新性 | [AndroidWorld 跑分框架](specs-archive/202608262018-androidworld-framework/) |
| 2026-09-08 | ChatGuiAgent 重构为纯 ReAct 决策循环：输出瘦身至 thought/action/parameters（CLICK 必填 target）；历史改为全量文本 trace（append-only，多模态仅当前一张）；engine/adapter 事后回执（已变化/无变化）替代 CLICK-UI 事前对齐；三级卡住阶梯（L1 症状注入自纠 → L2 强制 ASK）；ASK 双模式语义 + reason_code；`ChatGuiAgent(mode=...)` 显式模式开关；高风险防线降级为文本关键词 `augment_risk_from_text` | 结构化输出是输出侧 token（满配 ≈860–1340/步，贵且拖慢步内延迟）且 CLICK-UI 自洽校验防不住幻觉坐标；事后反馈近零成本（复用稳定等待期前后截图比对）且覆盖更多错误类别（点错/未响应/加载误判） | [Agent 纯 ReAct 决策循环](../specs-archive/202609091356-908-react-decision-loop/) |

## Environment & Configuration

| Key | Description | Required | Default |
|-----|-------------|----------|---------|
| BENCHMARK_RUN_DIR | run_benchmark.py 设置，adapter 逐任务轨迹（before.png/raw_output/action.json）落盘根目录 | 跑分时 | —（未设置则不落轨迹） |

## Conventions

- 后端包命名：按技术领域小写单词（api / runtime / agent / memory / device / safety / storage / ws / config）
- REST 路径：直接写在路由装饰器中，前缀 `/api`（如 `/api/conversations`、`/api/memory`、`/api/devices`）；健康检查 `/health`；静态截图 `GET /artifacts/{relpath}`
- WebSocket 路径：`/ws/conversations/{conversation_id}`
- 认证：无（本地/内网工具，无鉴权）
- 错误处理：各路由内部处理；引擎 `ValueError` → HTTP 400（互斥冲突返回结构化 code：conversation_busy / device_busy）
- 互斥两级：同一会话同时仅一个活动 Turn；同一 device_id 有活动/挂起 Turn 时其他会话被拒（无 device_id 的 Mock 会话豁免）
- Agent 调用方式保持稳定：`agent.act(input) -> AgentOutput`；每个 Turn 新建 agent 实例，跨轮上下文只经 `AgentInput.conversation_context` / `memory_text` 传递
- Agent 历史上下文（feature 908）：全量文本 trace——`[system, user(goal)] + (assistant(thought+action), user(回执))* + 当前 user`，append-only 不截断（MAX_STEPS 封顶）；仅实际执行的动作（含 WAIT）进 trace（skip/ASK/COMPLETE 步不进）；多模态输入仅当前一张；trace 为 agent 实例 turn 内自持状态；上一步回执由调用方经 `AgentInput.last_step_feedback` 注入（决策图 vs 稳定图比对的确定性短句）
- 动作 schema：CLICK / SCROLL / TYPE / OPEN / BACK / HOME / COMPLETE / ASK / WAIT（定义在 `backend/app/agent/schemas.py`；WAIT 为 feature 825 新增：加载期真实等待，engine 侧无设备操作但触发屏幕稳定等待，等待期间零 VLM 调用；CLICK 自 feature 908 起必填 `target`——目标元素可见文本，缺失 → missing_target skip，兼作风险关键词防线扫描源；ASK 可选 `reason_code`），扩展动作时旧动作必须仍兼容
- 坐标一律使用 `[0, 1000] x [0, 1000]` 归一化坐标，不向 Agent 暴露真实像素坐标
- 轮次执行使用引擎专用后台事件循环线程，不阻塞 REST 请求，不依赖请求循环存活
- 同一会话内用户明确拒绝过的高风险动作（同 action+parameters）再次出现时自动跳过，不再重复弹确认
- WebSocket 事件字段变更时，必须同步更新前端 `chatStore.ts` 和相关组件
- 截图路径：TurnStep 只存相对 `ARTIFACTS_DIR` 的 POSIX 路径，API 层拼 `/artifacts/...` URL
- 长期记忆：`data/memory/`（MEMORY.md 索引 + preferences.md + paths.md），读取不创建文件，写入经 threading.Lock 互斥
- 数据库模型变更时，需考虑现有 `data/app.db` 的兼容性（当前 init_db 会丢弃旧 tasks 表）
- `artifacts/`、`data/`、`.venv/`、`node_modules/` 属于运行或依赖产物，不纳入业务代码修改
- 后端测试是 TDD 契约：`backend/tests/README.md` 为权威规格，禁止修改测试迁就实现；测试命令 `cd backend && ../.venv/Scripts/python.exe -m pytest -q`
- 用户语言：所有对话和文档使用中文

## Approved Dependencies

后端（均已批准，新增依赖无特殊约束、新增前提醒即可）：
- fastapi、uvicorn[standard]、sqlalchemy、pydantic、pydantic-settings、pillow、python-multipart、websockets、aiofiles、openai
- 测试：pytest、pytest-asyncio

前端（均已批准）：
- 运行时：react、react-dom、react-router-dom、axios、zustand
- 构建/开发：typescript、vite、@vitejs/plugin-react、tailwindcss、postcss、autoprefixer、@types/react、@types/react-dom
