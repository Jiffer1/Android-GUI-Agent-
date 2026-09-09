# AndroidWorld：Android GUI Agent 动态评测基准介绍

> 论文：[AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents](https://arxiv.org/abs/2405.14573)（Rawles et al., 2024，Google DeepMind / Google Research，ICLR 2025 Poster）
> 代码：[google-research/android_world](https://github.com/google-research/android_world)
> 主页：[google-research.github.io/android_world](https://google-research.github.io/android_world/)

## 1. 概述

AndroidWorld 是 Google 推出的一个 **开源、动态的 Android GUI Agent 评测环境**，用于在真实 Android 环境中端到端地测试自主智能体（autonomous agents）完成复杂任务的能力。它与 Google 此前的 AndroidEnv 共享部分基础设施，但定位是**基准评测**（benchmarking），而非通用的强化学习环境。

要理解它解决的核心问题，可以对比传统静态评测集的三大缺陷：

| 传统静态评测的问题 | AndroidWorld 的应对 |
|---|---|
| 任务固定，容易被记住/过拟合（benchmark contamination） | 任务参数**动态随机实例化**，同一任务模板可生成数百万变体 |
| 以最终截图/轨迹匹配作为判分依据，容易误判（crappy reward） | 从**真实系统状态**（数据库/文件/UI 状态）读取确定性的 reward 信号 |
| 合成 UI 环境与真实 App 差距大，成果难以迁移 | 运行 **20 个真实开源 App**，贴近真实用户场景 |

## 2. 核心特性

- **116 个手工精心设计的任务**，覆盖 20 个真实世界的 Android 应用（日历、短信、通讯录、地图、文件、浏览器、录音、笔记、VLC 播放器等），外加跨应用工作流。
- **动态任务实例化**：每个任务族（task family）带随机参数（联系人姓名、日期、金额等），通过 `n_task_combinations` 可查看每个模板的参数排列组合数，同一模板可生成数百万种变体，天然抗过拟合。
- **耐久的奖励信号**：任务完成判定不依赖文本/截图对比，而是读取 App 的真实系统状态（数据库、文件系统、设置项），结果确定性、可重复。
- **开放式环境**：不限制被测 App，任何 Android 应用或网站都可以加入评测，便于研究者自行扩展。
- **轻量**：模拟器运行只需约 2GB 内存、8GB 磁盘。
- **MiniWoB++ 集成**：可以把 Web 任务以原生 Android 控件形式渲染进模拟器，复用现有 Web Agent 评测任务。
- **可扩展**：自定义 task family 和自定义 agent 都有清晰的接口；2025-06 起实验性支持 Docker 运行。

## 3. 架构与运行机制

```
+---------------------------------------------------+
|                Host (Python, ≥3.11)               |
|  run.py  ──►  Suite / Task Family  ──►  Agent     |
|                     │ 读取系统状态、下发指令        |
|                     ▼                             |
|         AVD (Pixel 6, API 33 "AndroidWorldAvd")   |
|         emulator -grpc 8554 (无障碍转发)           |
+---------------------------------------------------+
```

关键组成：

- **模拟器**：标准 Android Emulator，需创建名为 `AndroidWorldAvd` 的 Pixel 6 / API 33 (Tiramisu) 虚拟设备，启动时加 `-grpc 8554` 用于无障碍（accessibility）信息转发。
- **AndroidEnv 基础设施**：复用 Google 的 AndroidEnv 与模拟器交互（截图、a11y 树、注入触控/手势/按键）。
- **任务族**：每个任务是一个 Python 类，定义了任务模板、随机参数生成器、以及从模拟器内部读取状态计算 reward 的方法（通过预置的代理 App/文件系统检查）。
- **Agent 接口**：自定义 agent 继承 `EnvironmentInteractingAgent`，实现 `step()` 返回 `AgentInteractionResult`，设置 `done=True` 即视为完成；框架本身不限制 agent 内部逻辑（可以是 VLM、LLM+工具、强化学习策略等）。
- **步数上限**：每个任务的上限约为人类平均完成时长的 2 倍（2024-11-18 更新），超出即判失败。

## 4. 评测流程（Quick Start）

```bash
# 1. 创建 conda 环境
conda create -n android_world python=3.11
conda activate android_world

# 2. 创建 AVD：Pixel 6 / API 33，命名必须是 AndroidWorldAvd
#    启动模拟器并开启 grpc 转发
emulator -avd AndroidWorldAvd -grpc 8554

# 3. 安装
git clone https://github.com/google-research/android_world.git
cd android_world
pip install -e .

# 4. 运行整个基准（以官方 M3A + GPT-4 为例）
python run.py --suite_family=android_world \
  --agent_name=m3a --perform_emulator_setup

# 5. 调试单个任务
python minimal_task_runner.py --task=ContactsAddContact

# 6. 断点续跑
python run.py --checkpoint_dir=/tmp/checkpoint
```

## 5. 基线成绩与排行榜

官方论文中的人类与基线水平（20 个应用、116 个任务，成功率）：

| Agent | 输入模态 | 成功率 |
|---|---|---|
| 人类 | — | ~80%+ |
| **M3A**（Multimodal Autonomous Agent，GPT-4V） | 截图 + a11y 树 | ~30.6% |
| **T3A**（Text-only） | 纯 a11y 树文本 | ~19.8% |
| SeeAct 适配版 | 截图 | 明显低于 M3A |

M3A 的核心循环是：观察（截图 + 无障碍树）→ 思考 → 输出结构化动作（tap/text/scroll 等）→ 检查是否完成。

Google 还维护了一个[公开排行榜](https://google-research.github.io/android_world/leaderboard.html)，支持第三方提交。社区近期（2025–2026）结果不断刷新，代表性方案包括 GUI-explorer（~47.4%）、DroidRun、Midscene、minitap 等；国内各手机厂商的系统级 Agent 也多以其为标准评测之一。整体趋势是：单步 grounding 能力已较强，**长程规划、跨应用工作流、记忆**仍是主要瓶颈。

## 6. 代表性任务示例

| 任务名 | 应用 | 说明 |
|---|---|---|
| `ContactsAddContact` | 通讯录 | 新建联系人（姓名/电话随机生成） |
| `SimpleCalendarAddOneEventRelativeDay` | 简单日历 | 在"相对日期"（如后天）添加事件 |
| `SimpleSmsSendReceivedAddress` | 简单短信 | 把刚收到的短信中的地址转发给指定联系人 |
| `ExpenseAddMultiple` | 记账 | 一次录入多条随机金额支出 |
| `OsmAndMarker` | OsmAnd 地图 | 在地图指定位置打标记 |
| `RecipeAddMultipleRecipes` | 食谱 | 批量添加多个食谱 |
| `VlcCreatePlaylist` | VLC | 用给定文件创建播放列表 |
| `TasksHighPriorityTasksDueOnDate` | 任务管理 | 筛选/处理某日到期的高优先级任务 |
| `SportsTrackerTotalDistanceForCategoryOverInterval` | OpenTracks | 查询某时间段某类运动的合计里程 |
| `AudioRecorderRecordAudioWithFileName` | 录音机 | 录音并以指定文件名保存 |

另有系统类任务（如飞行模式开关）、浏览器任务（百度/Google 搜索、维基百科查询）以及需要多个 App 协作的跨应用任务。

## 7. 对本项目（android-gui-agent-platform）的借鉴意义

结合我们现有的 VlmGuiAgent / ChatGuiAgent 平台，AndroidWorld 有几点特别值得借鉴：

1. **评测机制设计**：我们的 agent 目前缺少客观评测。AndroidWorld 的"任务模板 + 随机参数 + 从系统状态读 reward"三层设计，可以直接移植：在受控模拟器上为典型任务（发短信、加联系人、设闹钟）写少量 task family，就能得到可持续、防过拟合的回归测试。
2. **观察空间选择**：AndroidWorld 同时提供截图与 a11y 树两种观察，论文结论是二者结合（M3A）显著优于纯文本（T3A）。我们平台当前以截图为主，可考虑把 adb 的 a11y 树作为辅助输入，降低 grounding 错误。
3. **步数上限策略**：以"人类完成时长的 2 倍"作为上限，简单且有效，适合直接用于我们的会话式执行引擎防死循环。
4. **架构契合度高**：我们的平台同样基于 adb + 截图 + 动作注入的循环，接入 AndroidWorld 作为评测后端在工程上完全可行（其 agent 接口只需实现一个 `step()`），可作为后续演进方向。
5. **排行榜坐标系**：30%（GPT-4V 时代）→ 47%+（当前 SOTA）→ 80%+（人类）的梯度，为我们评估自研 agent 的水平提供了行业参照。

## 8. 参考资料

- GitHub 仓库：https://github.com/google-research/android_world
- 项目主页：https://google-research.github.io/android_world/
- 论文：AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents（arXiv:2405.14573）
- ICLR 2025 Poster 页面：https://iclr.cc/virtual/2025/poster/19729
- 排行榜：https://google-research.github.io/android_world/leaderboard.html
