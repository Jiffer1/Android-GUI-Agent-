# Implementation Plan: AndroidWorld 测试框架搭建与基线跑分

## Overview

六步实施：① Windows 环境搭建（AVD + android_world venv）→ ② chat_agent 业务覆写绕过开关 → ③ `AndroidWorldAdapter`（Adapter 模式接入官方 harness）→ ④ 独立跑分入口脚本 → ⑤ 冒烟子集验证与修复 → ⑥ 全量 116 任务基线 + 报告聚合。所有源码级 API 细节已在 `third_party/android_world`（已克隆）调研确认，映射设计见下。

## Architecture Decisions

1. **独立入口脚本，零修改 android_world 仓库**（替代改 run.py `_get_agent`）：
   `benchmark/run_benchmark.py` 复刻 run.py 主流程（约 50 行）。理由：run.py 的 `_find_adb_directory()` 只认 macOS/Linux 路径，Windows 必挂（自写入口显式传 adb.exe 路径）；且第三方库保持原样便于后续 git pull 更新。
2. **Adapter 直连 ChatGuiAgent，不经 ConversationEngine**：无 DB / WS / ASK / confirm / 记忆参与；每任务新建 agent 实例。
3. **动作映射（源码已确认：`env/json_action.py`、`env/actuation.py`）**：

   | 我方动作 | JSONAction | 细节 |
   |---|---|---|
   | CLICK(point) | `click(x=px, y=py)` | `[0,1000]` → 像素：`px = point/1000 * logical_screen_size`，换算集中于 adapter 一处 |
   | SCROLL(start,end) | `scroll(direction=...)` | 由 start→end 坐标 delta 推断 up/down/left/right；无 index（滚整屏） |
   | TYPE(text) | `input_text(text=text)` | 不带坐标（actuation 不重复聚焦）；注意 actuation.py:104-105 会自动 press_enter——与 M3A 行为一致，公平对照 |
   | OPEN(pkg) | `open_app(app_name)` | 包名 → app 名静态映射表（约 20 项，数据源：apps 安装脚本） |
   | BACK | `navigate_back` | |
   | HOME | `navigate_home` | |
   | COMPLETE | `status(goal_status="complete")` | done=True，不执行设备动作 |
   | ASK | `status(goal_status="infeasible")` | done=True；benchmark 无人值守语义 |

4. **step() 契约（`agents/base_agent.py` 已确认）**：签名 `step(self, goal: str) -> AgentInteractionResult(done, data)`；截图经 `self.get_post_transition_state().pixels`（np.ndarray，RGB）；动作经 `self.env.execute_action(JSONAction)`。不复用 `AndroidAdbController`（官方单通道，避免不一致）。
5. **历史注入**：adapter 自理 `history_actions`（`[{action, parameters}]`，与引擎组装格式一致），每步 append；`conversation_context` / `memory_text` / `ask_reply` 留空。
6. **轨迹落盘**：adapter 每步写 `artifacts/benchmark/<run_id>/<task_name>/step_N/`（before 截图 PNG、`raw_output`、映射后 JSONAction、done 标记）。
7. **绕过开关**：`ChatGuiAgent.__init__` 新增 `disable_business_overrides=False`；`_postprocess_action` 的「立即支付/呼叫→COMPLETE」「桌面→强制 OPEN」覆写被其短路。chat_agent.py 仅此一处改动（FR-05）。
8. **报告脚本运行环境**：checkpointer 按 task 存 **gzip pickle**（Episode 对象，`checkpointer.py:103`），反序列化需 android_world 包 → `report.py` 在 third_party venv 下运行，输出 Markdown 到 `docs/`。

## Implementation Steps

### Step 1: Windows 环境搭建（FR-01，AC-01 前置）
- [x] Android Studio 创建 AVD：Pixel 6 / Tiramisu API 33 / 命名 `AndroidWorldAvd`
- [x] 命令行启动验证：`emulator.exe -avd AndroidWorldAvd -no-snapshot -grpc 8554`（Windows SDK 路径）
- [x] `third_party/android_world/.venv`：`python -m venv`（3.11+）+ `python -m pip install -r requirements.txt` + `python setup.py install`
- [x] 安装 ffmpeg 并入 PATH
- [x] `adb devices` 见 `emulator-5554`；venv 内 `import android_world` 成功
- Files: `third_party/android_world/`（venv 加入 .gitignore）
- 备注：requirements 无 tensorflow 类重依赖（android_env 1.2.3 / grpcio-tools / opencv 等均有 Windows wheel），原生安装可行性高
- 实际执行：Python 3.12 的 sqlite 无 FTS4（Joplin 崩溃）→ 改用 `D:\Python`（3.13.7，自带 FTS4）建 `.venv313`；依赖版本调整 numpy>=2.1 / pandas>=2.2.3（pandas 实装 3.0.5）+ `audioop-lts`（3.13 移除 audioop，pydub 需要）+ `--no-deps` 安装本体（setup.py 钉死 pandas==2.1.4 无 cp313 wheel）；APK 下载（storage.googleapis.com 直连仅 ~11KB/s）由用户手动下载放入 `%TEMP%\android_world\app_data\` 缓存解决

### Step 2: chat_agent 业务覆写绕过开关（FR-02，AC-07）
- [x] `ChatGuiAgent.__init__` 增加 `disable_business_overrides: bool = False` 并存为实例属性
- [x] `_postprocess_action` 中两处业务覆写（关键词→强制 COMPLETE、桌面→强制 OPEN）以开关短路
- [x] 回归：默认路径行为不变，既有 56 契约测试通过（56 passed）
- Files: `backend/app/agent/chat_agent.py`

### Step 3: benchmark 包与 AndroidWorldAdapter（FR-02 核心）
- [x] `backend/benchmark/__init__.py`
- [x] `backend/benchmark/app_name_map.py`：包名→app 名映射（约 20 项）
- [x] `backend/benchmark/aw_adapter.py`：`AndroidWorldAdapter(EnvironmentInteractingAgent)`
  - `__init__(env, name="chat_gui_agent", disable_business_overrides=True)`：内部构造 `ChatGuiAgent`（OpenAI SDK + 环境变量，无 DB 依赖）；初始化 `history_actions`
  - `step(goal)`：state.pixels → PIL → `AgentInput` → `act()` → 轨迹落盘 → 动作映射（含 CLICK-UI 校验 skip 时不执行、仅记轨迹）→ `execute_action`（status 类不执行）→ `AgentInteractionResult(done= action in {COMPLETE, ASK}, data=step_data)`
  - 同步阻塞调用（android_world 为同步 harness，无 asyncio 冲突）
- Files: `backend/benchmark/*`
- 验证：py_compile 通过（真实验证在 Step 5）

### Step 4: 跑分入口与冒烟清单（FR-03）
- [x] `backend/benchmark/run_benchmark.py`：
  - `sys.path` 注入 `backend/`；`env_launcher.load_and_setup_env(console_port=5554, emulator_setup=args.setup, adb_path=<Windows adb.exe 显式路径>)`
  - `create_suite(registry, n_task_combinations=1, seed=30, tasks=args.tasks)`；`suite_utils.run(suite, agent, checkpointer=IncrementalCheckpointer(out_dir))`
  - 输出目录 `artifacts/benchmark/<run_id>/`；CLI：`--tasks ...` / `--smoke` / `--all` / `--resume <dir>` / `--perform-emulator-setup`
- [x] `backend/benchmark/smoke_tasks.txt`：冒烟清单落盘（18 项，每 app family 1 个）
  （IR family 1 项：全量 `--all` 时自动含 IR registry；冒烟暂不含）
- Files: `backend/benchmark/run_benchmark.py`、`smoke_tasks.txt`

### Step 5: 冒烟验证与修复（AC-01/02/03）
- [x] 首次 `--perform-emulator-setup`（装 20 app + 授权）
- [x] 单任务 `--tasks=ContactsAddContact` 全链路跑通（截图→VLM→映射→执行→reward 判定）
- [x] 坐标换算校验：对已知按钮位置 tap 前后截图对比（logical vs physical size 偏差排查）
- [x] `--smoke` 全清单无人值守跑完；修复暴露的映射问题（app 名、type 回车副作用、scroll 方向等）
- [x] 冒烟结果 ≥1 任务 successful
- 验证物：checkpoint 目录 + `artifacts/benchmark/<run_id>/` 轨迹
- 实际执行：单任务成功（9 步、152s、success=1.0，轨迹 OPEN→CLICK FAB→填表→保存→COMPLETE，坐标/映射全对）；过程中修复：contacts 通知权限弹窗用 `adb pm grant POST_NOTIFICATIONS` 授掉；`PYTHONUTF8=1` 修 GBK 控制台打印 emoji 崩溃（否则 checkpoint 落盘前崩溃）
- 冒烟结果（run 20260826-141046）：18 任务全部无人值守跑完（61 分钟），**7/18 = 38.9%**；成功：AudioRecorder/Clock/Contacts/Markor/Recipe/SimpleSms/TurnOnWifiAndOpenApp；OsmAnd、Vlc 2 步即终（疑似 ASK→infeasible），失败归因留给 Step 6 报告

### Step 6: 全量基线与报告（FR-04，AC-04/05/06）——**已取消（用户决策 2026-08-26：不跑全量）**
> 冒烟 7/18 即为基线结果；`report.py` 已就绪，可对任意 checkpoint（含冒烟）出报告。失败归因已转入 feature 826。以下条目保留原计划存档：
- [ ] `backend/benchmark/report.py`（third_party venv 下运行）：遍历 checkpoint gzip pickle → 总成功率、按 family 成功率、失败粗分类（超步 / VLM 输出非法 / 执行异常 / 逻辑错误）→ Markdown + CSV
- [ ] `--all` 全量 116 任务（中断用 `--resume` 续跑）
- [ ] `docs/baseline-androidworld.md`：分数、环境快照（AVD 配置、android_world commit hash、`VLM_MODEL_ID`、n_task_combinations=1、seed=30、日期）、≥3 类失败归因实例（引用轨迹目录）
- [ ] 全量跑前给出 VLM 调用量/费用预估（任务数 × 平均步数 × 每步 1 次）
- [ ] 既有 56 契约测试通过（AC-07）
- Files: `backend/benchmark/report.py`、`docs/baseline-androidworld.md`

## Acceptance Criteria Mapping

| AC | Verified By |
|----|-------------|
| AC-01: 环境就绪 + 单任务跑通 | Step 5 单任务验证（checkpoint 记录 + 轨迹目录） |
| AC-02: 冒烟无人值守跑完 | Step 5 `--smoke` 完整 checkpoint 与日志 |
| AC-03: 冒烟 ≥1 任务成功 | Step 5 冒烟结果中 successful ≥1 |
| AC-04: 全量 116 基线 + 明细 | Step 6 全量 checkpoint + `report.py` 输出 |
| AC-05: 失败轨迹可查 + 3 类归因 | Step 6 轨迹目录 + 基线报告归因章节 |
| AC-06: 基线报告落盘 docs/ | `docs/baseline-androidworld.md` 存在且含配置快照 |
| AC-07: 既有契约测试通过 | `cd backend && ../.venv/Scripts/python.exe -m pytest -q` |

## Risks & Mitigations

- **Windows pip 安装失败**（android_env / grpcio-tools 编译）→ 用 Python 3.11（与官方 conda 版本一致）最大化 wheel 覆盖；失败换 3.12/3.10；终极回退 WSL2（需另行评估，非本 feature 内）
- **env_launcher 对 Windows 的隐性假设**（emulator 检测/路径）→ Step 5 前先单独验证 `load_and_setup_env` 连上已启动的模拟器；自写入口已绕过 run.py 的 adb 路径问题
- **input_text 自动回车副作用**（表单提前提交）→ 与 M3A 原生行为一致（公平对照）；冒烟若暴露严重问题，退化为「TYPE 前必须 CLICK 定位」策略
- **坐标换算偏差**（[0,1000]→logical pixels）→ Step 5 显式校验步骤；Pixel 6 AVD 默认无缩放，logical≈physical
- **VLM 中文 prompt 处理英文任务** → 基线如实记录，不做提分改动（Out of Scope，留给 825+）
- **超步**：harness 按任务设 step budget（约 2×人类完成时间）；agent 卡死→强制 ASK→映射 infeasible 终止，机制自洽
- **doubao 拒答/非法 JSON** → agent 已有 3 次重试 + 降级路径（既有行为，不动）

## Estimated Complexity

Medium-High —— 新增代码量不大（adapter ~200 行、入口 ~150 行、报告 ~100 行），但 Windows 原生环境搭建与跨库集成调试（AVD/grpc/a11y 转发 app/模拟器稳定性）不可控因素多；冒烟阶段预期需 1-2 轮映射修复迭代。
