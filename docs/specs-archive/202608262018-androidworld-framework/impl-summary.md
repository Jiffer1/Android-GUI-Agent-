# impl-summary: 824 AndroidWorld 测试框架搭建与基线跑分

## Implementation Complete

### 基线结果
- 冒烟 18 任务（run `artifacts/benchmark/20260826-141046/`）：**7/18 = 38.9%**，61 分钟无人值守
- 成功：AudioRecorder / Clock / Contacts / Markor / Recipe / SimpleSms / TurnOnWifiAndOpenApp
- 全量 116 取消（用户决策 2026-08-26）

### Files Created
- `backend/benchmark/__init__.py` — benchmark 包
- `backend/benchmark/aw_adapter.py` — AndroidWorldAdapter（截图→act→动作映射→执行→轨迹落盘）
- `backend/benchmark/app_name_map.py` — 包名→android_world app 名映射
- `backend/benchmark/run_benchmark.py` — 独立跑分入口（零修改第三方库，--tasks/--smoke/--resume 等）
- `backend/benchmark/smoke_tasks.txt` — 18 任务冒烟清单（每 app family 1 个）
- `backend/benchmark/report.py` — checkpoint 聚合报告（须在 android_world venv 下运行）
- `.gitignore`（仓库根）— third_party/ 与 artifacts/benchmark/

### Files Modified
- `backend/app/agent/chat_agent.py` — `__init__` 新增 `disable_business_overrides` 开关，短路「立即支付/呼叫→COMPLETE」「桌面→强制 OPEN」两处业务覆写（benchmark 链路传 True）

### Acceptance Criteria
- [x] AC-01: 环境就绪 + 单任务跑通 — ContactsAddContact success=1.0（9 步、152s，轨迹 OPEN→FAB→填表→保存→COMPLETE）
- [x] AC-02: 冒烟无人值守跑完 — 18 任务 exit 0，checkpoint + 轨迹完整
- [x] AC-03: 冒烟 ≥1 任务成功 — 7/18
- [~] AC-04: 全量 116 基线 — **取消**（用户决策），冒烟结果作为基线
- [~] AC-05: 失败轨迹可查 + 归因 — 轨迹可查✅；归因完成于对话（A1 中文 app 名死循环 / A2 非法动作→COMPLETE / B 类 agent 行为），修复方案转入 feature 826，未单独落盘报告
- [~] AC-06: 基线报告落盘 docs/ — **取消**（随全量取消；report.py 就绪可随时生成）
- [x] AC-07: 既有契约测试通过 — 56 passed

### Notes（偏差与环境实录）
- **venv 换 3.13**：Python 3.12 的 sqlite 无 FTS4（Joplin setup 崩溃）→ 改用 `D:\Python`（3.13.7）建 `.venv313`；pysqlite3-binary 无 Windows wheel（死路）。依赖调整：numpy>=2.1 / pandas>=2.2.3（实装 3.0.5）/ 删 matplotlib 钉死 / 补 `audioop-lts`（3.13 移除 audioop，pydub 依赖）/ `pip install -e . --no-deps`（setup.py 钉死 pandas==2.1.4 无 cp313 wheel）
- **APK 手动下载**：GCS（storage.googleapis.com）直连 ~11KB/s，18 个 APK 由用户手动下载放入 `%TEMP%\android_world\app_data\` 缓存（清单存 `change/824-androidworld-framework/apk_urls.txt`）
- **运行时补丁**：contacts 通知权限弹窗用 `adb shell pm grant com.google.android.contacts android.permission.POST_NOTIFICATIONS` 授掉；`PYTHONUTF8=1` 解决 GBK 控制台打印 ✅/❌ 崩溃（否则 checkpoint 落盘前崩溃）
- **ffmpeg**：winget 安装，运行时显式加入 PATH（pydub 音频任务需要）
- **发现的框架级 bug → feature 826**：① 中文 app 名致 OPEN 死循环（4 任务）② 非法动作（如 VLM 输出官方空间存在的 WAIT）被静默转 COMPLETE 提前终止（2 任务）
- AndroidWorld 官方动作空间 14 个（含 wait/answer/long_press 等），我方 8 个子集经 adapter 映射——动作空间对齐留给 825
