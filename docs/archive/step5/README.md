# Step5 Archive

Step5 归档时间：2026-06-25

Step5 主题：

```text
有限 HBM LRU
```

本归档保存 Step5 的调研、技术路线、代码开发方案、review follow-up 和工程收口方案。当前状态和轻量索引保留在：

```text
README.md
docs/global_memory.md
docs/core_simulator_technical_plan.md
```

历史开发状态已归档到：

```text
docs/archive/development_status.md
```

## 归档文件

- `01_vllm_block_management_study.md`：本地 vLLM block 管理方法调研。
- `02_offline_hbm_lru_design.md`：HitFloor offline HBM LRU 简化设计。
- `03_code_writing_plan.md`：Step5 初始代码编写方案。
- `04_review_followup_modification_plan.md`：streaming cache events、finish-time materialization 文档和 stateful eviction policy 修改方案。
- `05_engineering_closeout_plan.md`：Step1-Step5 核心仿真器工程收口方案。

## 最终状态

- Step5 A-D 已完成有限 HBM LRU cache、runner/report integration 和合成数据 E2E。
- Step5 E1-E4 已完成 streaming `cache_events.csv`、stateful eviction policy 和 finish-time materialization 边界文档。
- Step5 F1-F5 已完成 format/lint 基线、package CLI 正式入口、strict parser、config scope 收紧、stats 快照、stub 边界说明和最终验证。

最终验证：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 93 passed
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
```
