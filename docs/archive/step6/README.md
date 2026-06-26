# Step6 Workspace

Step6 v1 已完成并通过功能验收。

主题：

```text
HBM Cache Capacity Sweep Report
```

当前文档：

```text
docs/archive/step6/01_product_shape.md
docs/archive/step6/02_technical_route_and_code_plan.md
docs/archive/step6/03_acceptance_e2e.md
```

本目录是 Step6 归档文档。Step6 完成后，活跃状态已回写到主 README、`docs/global_memory.md` 和 `docs/core_simulator_technical_plan.md`。历史开发状态已归档到 `docs/archive/development_status.md`。

已确认边界：

- 第一版只 sweep `hbm_capacity_blocks`，不接受 GB 输入。
- 第一版输出 capacity 与指标关系表，不做 P90 target matching。
- 核心 runner 返回结构化 sweep result；`capacity_sweep.csv` 是 report/export 外围能力。
- request build once，capacity sweep 复用 requests。
- cache events 默认不落明细；只允许对指定 capacity dump `cache_events.csv`。
- 多实例并行 replay 作为后续项，单线程稳定后再设计 `ParallelCapacitySweepRunner` 或 execution backend。

已实现入口：

```bash
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
python scripts/run_capacity_sweep.py --config configs/experiments/step6_capacity_sweep.yaml
```

标准产物：

```text
reports/step6_capacity_sweep/capacity_sweep.csv
reports/step6_capacity_sweep/summary.md
```

最近验证：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 115 passed
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml: passed
```

功能验收：

```text
hbm_capacity_blocks=3 -> kv_hit_rate=0.000000, p90_ttft_ms=15.0
hbm_capacity_blocks=4 -> kv_hit_rate=0.876667, p90_ttft_ms=11.0
hbm_capacity_blocks=8 -> kv_hit_rate=0.913333, p90_ttft_ms=0.0
```

验收结论：

- Step1-Step5 核心链路正常。
- cache event 信号符合预期。
- 外围 report/export 能力没有反向污染核心 replay 语义。
