# Code Review: 824 AndroidWorld 测试框架搭建与基线跑分

## Summary

框架端到端已验证可用：单任务成功、18 任务冒烟无人值守跑完（7/18=38.9%）、report.py 对冒烟 checkpoint 实测出数、56 契约测试无回退。代码质量整体良好（坐标换算集中一处、异常不中断 run、轨迹落盘容错）。主要缺口是新增的 `disable_business_overrides` 开关无契约测试覆盖（项目以测试为 TDD 契约）；另有 app_name_map 部分包名错误、resume 路径推断脆弱等小问题。已知两大跑分失分 bug（中文 app 名、非法动作→COMPLETE）已转入 feature 826，不计入本次评审。**可归档，建议先完成小修。**

## Findings

### 🔴 Critical

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| (none) | | | | |

### 🟠 Major

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | `backend/app/agent/chat_agent.py:92,572` | Test Coverage | `disable_business_overrides` 开关（本 feature 对产品代码的唯一改动）无任何契约测试，默认路径与旁路路径均未锁定 | 补一条契约测试：默认时覆写生效、True 时短路（项目约定测试即规格） |

### 🟡 Minor

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | `backend/benchmark/app_name_map.py:29-32` | Data Correctness | "AndroidWorld custom APKs" 段包名未经验证且错误（`com.android.expense`/`me.zebao.recipe`/`com.kinandcasus.tasket` 实际为 `com.arduia.expense`/`com.flauschcode.broccoli`/`org.tasks`；`smsmessages` 键亦误），注释"confirmed during smoke runs"与事实不符；且缺 joplin/retro/opentracks | 以 `pm list packages` 实测为准修正或删除该段（该映射实际几乎不被触发，VLM 输出显示名而非包名；真正的修复在 826 别名层） |
| [ ] | `backend/benchmark/run_benchmark.py:141-143` | Robustness | `--resume` 用 `checkpoint_dir.parent.parent` 反推 run_dir，目录层级稍变即错 | 显式参数 `--run-dir` 或直接以 checkpoint 兄弟目录探测 `BENCHMARK_RUN_DIR` 标记文件 |
| [ ] | `backend/benchmark/run_benchmark.py:157-163` | Observability | 运行日志只打到 stdout（临时重定向到 /tmp），run_dir 内无持久日志，AC-02"日志可查"依赖易失文件 | runner 内部 tee 一份 `console.log` 到 run_dir |
| [ ] | `third_party/android_world/.venv313` vs `backend/.venv` | Dependency | 两 venv 的 openai 版本偏差（产品 >=1.51 实装版本未对齐 vs venv313 3.3.1），ChatGuiAgent 的 VLM 调用行为一致性未验证 | 在 venv313 内 pin 与产品 .venv 相同的 openai 版本，或在 impl-summary 记录偏差风险 |

### 🔵 Info / Suggestions

| Done | Location | Category | Problem | Suggestion |
|------|----------|----------|---------|------------|
| [ ] | `backend/benchmark/aw_adapter.py:109-112` | Observability | 动作执行异常被转为 "unknown" 动作但无日志输出，归因只能翻 action.json | 转换处加 `logging.warning` |
| [ ] | `backend/benchmark/report.py` | Verified | report.py 已对冒烟 checkpoint 实测可用（7/18=38.9%，报告在 /tmp） | 冒烟报告可落盘 `change/824-androidworld-framework/` 作为基线存档 |
| [ ] | `backend/app/agent/chat_agent.py:589-590` | Known Issue | 非法动作→COMPLETE 的降级 bug 与中文 app 名死循环（冒烟 4+2 任务失分根因） | 已立项 feature 826，不属本 feature diff |
| [ ] | `backend/benchmark/run_benchmark.py:51-63` | Duplication | 手写 `_load_dotenv` 与 pydantic-settings 的 .env 解析行为略有差异（引号/导出空白处理） | 可接受（避免跨 venv 依赖），已注释说明即可 |

## Acceptance Criteria Coverage

| AC | 验证方式 | Status |
|----|----------|--------|
| AC-01: 环境就绪+单任务跑通 | 冒烟 run 前 ContactsAddContact success=1.0（checkpoint 20260826-140457，9 步轨迹完整） | ✅ 集成验证通过 |
| AC-02: 冒烟无人值守跑完 | 18 任务 exit 0，61 分钟，轨迹+checkpoint 完整（20260826-141046）；日志持久化见 Minor#3 | ✅ 通过（小瑕疵） |
| AC-03: 冒烟 ≥1 成功 | 7/18 = 38.9% | ✅ 通过 |
| AC-04: 全量 116 基线 | — | ❌ 用户决策取消 |
| AC-05: 轨迹可查+≥3 类归因 | 轨迹目录逐任务逐 step 落盘；归因 3 类（中文 app 名死循环/非法动作→COMPLETE/agent 行为）见 impl-summary.md | ✅ 通过 |
| AC-06: 基线报告写入 docs/ | —（report.py 已验证可用） | ❌ 随全量取消 |
| AC-07: 既有契约测试通过 | 56 passed（chat_agent 改动后全量回归） | ✅ 通过 |

## Verdict

- [ ] ✅ Ready to merge
- [x] 🟡 Merge after minor fixes (no re-review needed)
- [ ] 🟠 Requires fixes and re-review
- [ ] 🔴 Do not merge — significant issues found
