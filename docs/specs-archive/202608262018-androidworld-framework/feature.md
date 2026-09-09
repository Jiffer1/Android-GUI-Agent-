# Feature: AndroidWorld 测试框架搭建与基线跑分

## Summary

以 AndroidWorld 基准成功率作为 agent 演进的量化北极星。本 feature 在 Windows 原生环境搭建 AndroidWorld 测试框架：Android Studio AVD（Pixel 6 / API 33 / `-grpc 8554`）+ android_world 官方 harness（独立 Python 3.11 venv，克隆至 `third_party/`），以 Adapter 模式将现有 ChatGuiAgent 原样接入官方 runner（继承 `EnvironmentInteractingAgent` 实现 `step()`，动作映射到官方动作空间，绕开 ConversationEngine 的 DB/WS/ASK 耦合），先以冒烟子集（每 family 1 个任务，约 20 个）验证全链路无人值守跑通，再全量 116 任务产出首个基线分数与逐任务报告。agent 内部逻辑零修改——基线反映当前真实水平；后续 agent 改动（如 825 agent loop 重构）以本基线为对照量化验证。

## User Stories

- 作为开发者，我希望有一条可重复的 AndroidWorld 跑分流水线，这样任何 agent 改动都能量化验证收益或回退。
- 作为开发者，我希望现有 ChatGuiAgent 不改内部逻辑即可接入跑分，这样基线反映当前真实水平而非改造后水平。
- 作为开发者，我希望逐任务的成功/失败明细与逐步轨迹（截图 + raw_output）落盘，这样失败任务可归因（VLM 误判 / 坐标偏差 / 动作映射错误 / 超步）。
- 作为开发者，我希望跑分过程完全无人值守（无 ASK/confirm/记忆等交互阻塞、单任务失败不中断 run），这样可以整夜跑全量。
- 作为用户，我希望拿到一份可对比的基线报告（分数 + family 分布 + 环境配置快照），这样后续提分有公认起点。

## Functional Requirements

### FR-01: Windows 原生环境搭建
- AVD：Pixel 6 / Tiramisu API 33 / 命名 `AndroidWorldAvd`；命令行启动并带 `-grpc 8554`（accessibility 转发必需）。
- android_world 克隆至 `third_party/android_world`（仓库根，与平台代码隔离），并建独立 Python 3.11+ venv（`python -m venv` + `python -m pip` 安装；不复用 backend .venv，避免依赖冲突）。
- 安装 `ffmpeg`。
- 首次运行 `--perform_emulator_setup` 完成 20 个 app 安装与授权（一次性）。
- Windows 原生 pip 安装失败时的回退预案为 WSL2（见 Open Questions，非本 feature 交付）。

### FR-02: ChatGuiAgent 适配器（Adapter 模式，agent 零修改）
- 新增 `AndroidWorldAdapter`（继承 android_world 的 `EnvironmentInteractingAgent`），实现 `step()`，每步：
  1. 组装现有 `AgentInput`（instruction=goal、当前截图、step_count、history_actions 由 adapter 自理）；
  2. 调用现有 `ChatGuiAgent.act()`（chat_agent.py 内部逻辑零修改）；
  3. 动作映射到官方动作空间并经 AndroidEnv 执行：CLICK→touch、SCROLL→swipe、TYPE→type、OPEN→open_app、BACK→navigate_back、HOME→navigate_home、COMPLETE→`AgentInteractionResult(done=True)`。
- 坐标换算：agent 的 `[0,1000]` 归一化坐标 → AndroidEnv 坐标体系，换算集中在 adapter 一处。
- ASK 处置：benchmark 无人值守，ASK 不等待回复——直接视为该任务终止（done=True，reward 自然判失败）。
- 业务硬编码绕过：通过注入开关（构造参数/环境变量）禁用 `_postprocess_action` 的「立即支付/立即呼叫/立即付款→强制 COMPLETE」与「桌面→强制 OPEN」覆写，避免打车/支付业务逻辑污染跑分；chat_agent.py 仅允许增加该开关，其余不动。
- 风险确认（waiting_confirm）、长期记忆、DB、WS、summarize_turn 全部不参与（adapter 直连 agent，不经 ConversationEngine）。
- 动作空间 API 细节（坐标系定义、open_app 参数、type 特殊字符支持）在 plan 阶段读 android_world 源码（action_space / m3a）确认。

### FR-03: 跑分入口与子集/断点机制
- 优先复用官方 `run.py` 原生能力：`--tasks` 子集、`--checkpoint_dir` 断点续跑、`--n_task_combinations=1` 控制成本；agent 注册方式（改 run.py `_get_agent` vs 最小 wrapper 注入）plan 阶段定。
- 冒烟子集：每 family 挑 1 个任务（约 20 个），清单固定落盘（具体任务 plan 阶段从 task_registry 确定）。
- 全量 116 任务：冒烟通过后一次跑完，支持中断续跑。

### FR-04: 结果产物与归因
- 每任务产物落盘 `artifacts/benchmark/<run_id>/`：成功/失败、步数、耗时、逐步截图与 raw_output 轨迹。
- 汇总报告：总成功率（主指标）、按 family 成功率、失败粗分类（超步 / VLM 输出非法 / 执行异常 / 逻辑错误）。
- 基线报告写入 `docs/`：分数、环境快照（AVD 配置、android_world commit、模型 ID、VLM 配置、n_task_combinations、日期）。

### FR-05: 不改动项（影响面收敛）
- `chat_agent.py` 主逻辑、ConversationEngine、前端、DB schema、既有动作 schema 均不修改；仅新增 benchmark 代码与 FR-02 的绕过开关。
- backend 既有 56 个契约测试全部保持通过。
- MockGuiAgent / USE_MOCK_AGENT 链路不变。

## Acceptance Criteria

- [x] AC-01: 环境就绪——`--perform_emulator_setup` 成功；任选一个 agent（官方 M3A 或我们的 adapter）通过 `minimal_task_runner.py` 或 run.py 完整跑通单任务（如 ContactsAddContact）。
- [x] AC-02: adapter 冒烟子集（每 family 1 个）无人值守跑完：无 ASK/confirm 挂起、单任务失败不中断整 run；VLM 调用与动作执行有日志可查。
- [x] AC-03: 冒烟子集中 ≥1 个任务 reward 为成功（全链路正确性证明：截图→VLM→动作映射→执行→任务判定）。
- [ ] AC-04: 全量 116 任务基线跑完（允许断点续跑累计），产出总成功率、逐 family 成功率、逐任务明细（CSV/Markdown）。（取消：用户决策 2026-08-26 不跑全量）
- [x] AC-05: 失败任务轨迹可查（截图 + raw_output），并给出至少 3 类失败归因实例。
- [ ] AC-06: 基线报告（分数 + 环境配置快照）写入 docs/。（取消：随 AC-04 取消；report.py 已对冒烟 checkpoint 验证可用）
- [x] AC-07: backend 既有契约测试全部通过（无回退）。

## Technical Scope

### Affected Modules
- 新增 `backend/benchmark/`（最终位置 plan 阶段定）：AndroidWorldAdapter、冒烟子集清单、报告聚合脚本
- 新增 `third_party/android_world/`（官方仓库克隆 + 独立 venv，加入 gitignore）
- `backend/app/agent/chat_agent.py` — 仅新增业务覆写绕过开关
- `docs/` — 基线报告

### New Components Required
- `AndroidWorldAdapter`（`EnvironmentInteractingAgent` 子类）
- 跑分入口/注册脚本（支持 `--tasks` 子集与 `--checkpoint_dir` 续跑）
- 报告聚合脚本（官方结果 → Markdown 报告 + 失败归因）

### Integration Points
- android_world harness：AndroidEnv（截图/动作执行）、run.py（任务调度/reward/checkpoint）、task_registry（family 与任务清单）
- 火山方舟 VLM：沿用现有 `VLM_API_URL` / `VLM_API_KEY` / `VLM_MODEL_ID` 环境变量与重试逻辑

## Non-Functional Requirements

- 可重复性：同配置重跑可复现（任务参数随机化的固有波动除外）；环境与配置全量快照进报告。
- 成本控制：冒烟子集先行；`n_task_combinations=1`；全量跑前给出 VLM 调用量与费用预估。
- 无人值守：全程无交互阻塞；单任务异常捕获记为该任务失败，不中断整体 run。
- 性能：跑分吞吐受 VLM 延迟主导，adapter 自身开销可忽略。

## Out of Scope

- agent loop 重构（单循环、统一 schema、thought 等）——feature 825，以本基线为对照
- 任何提分改动（prompt 优化、模型更换、few-shot、a11y 树输入等）
- MiniWoB++ suite、Docker 方案、WSL2 回退实施
- 产品链路（ConversationEngine / 前端 / DB / WS / 记忆）变更

## Open Questions

- 动作映射 API 细节：AndroidEnv 动作坐标系（归一化浮点？）、open_app 参数（包名 vs app 名）、type 对特殊字符/中文的支持——plan 阶段读源码确认
- adapter 历史注入：`AgentInput.history_actions` 的组装粒度（复用 agent 现有期望格式）
- agent 注册方式：改 run.py `_get_agent`（侵入第三方库）vs wrapper 脚本（维护成本）——plan 阶段定
- 冒烟子集具体任务清单（每 family 挑哪个）——plan 阶段从 task_registry 定
- Windows 原生安装风险点（`python setup.py install` 兼容性、ffmpeg PATH）——实施时验证，失败再评估 WSL2
- 每步截图来源：AndroidEnv 提供的当前帧 vs 复用 `AndroidAdbController.screenshot()`——plan 阶段定（倾向官方帧，避免双通道不一致）
