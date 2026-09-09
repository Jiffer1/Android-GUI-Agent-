# AndroidWorld 测试框架搭建与基线跑分

Implemented on: 2026-08-26

以 Adapter 模式（`backend/benchmark/aw_adapter.py`）将现有 ChatGuiAgent 接入 Google android_world 官方 harness（克隆于 `third_party/android_world`，独立 `.venv313`），独立入口 `run_benchmark.py` 零修改第三方库。冒烟 18 任务无人值守跑通，基线 **7/18 = 38.9%**（run `artifacts/benchmark/20260826-141046/`）；全量 116 与 docs 基线报告经用户决策取消。chat_agent.py 仅新增 `disable_business_overrides` 开关。环境实录（Python 3.13 FTS4、APK 手动下载清单 `apk_urls.txt`、权限/编码补丁）见 impl-summary.md；冒烟暴露的两个框架级失分 bug（中文 app 名死循环、非法动作→COMPLETE）已立项 feature 826 修复，动作空间对齐与 wait 语义归 825。
