# Batch TS-D 执行记录：Streaming Metrics Aggregator

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-D 在 TS-C 的 streaming replay metric sink 之上，新增 capacity sweep 所需的 streaming 聚合器：

```text
streaming replay request / iteration metric
-> streaming metric aggregator
-> capacity sweep trace / instance rows
```

目标是让 streaming path 不再需要保存完整 `request_metrics` / `iteration_metrics` list，也能输出与现有 batch path 一致的 `CapacitySweepRow`。

本批不接入：

- streaming capacity sweep runner。
- CLI / report。
- 多 capacity orchestration。
- 多实例并行 replay。

因此本批不会改变现有 `capacity_sweep`、`BatchAwareReplayEngine.run(list[SimulationRequest])` 或 `batch_aware_hbm_lru` replay 语义。

## 2. 新增代码

```text
src/hitfloor/streaming/metrics.py
tests/unit/streaming/test_metrics.py
```

## 3. `CapacitySweepStreamingMetricAggregator`

新增：

- `CapacitySweepStreamingMetricAggregator`

职责：

- 消费 `BatchAwareRequestMetrics`。
- 消费 `IterationMetrics`。
- 聚合 trace scope 指标。
- 聚合 instance scope 指标。
- 输出与 `build_capacity_rows()` 相同 schema 的 `CapacitySweepRow`。

不负责：

- 执行 replay。
- 管理 cache event raw 明细。
- 写 CSV / Markdown。
- 选择 capacity。
- 多实例并行调度。

## 4. 指标口径

聚合器保持 Step6 capacity sweep 的 long-format 输出口径：

```text
scope=trace
scope=instance
```

trace row：

- 汇总全部 request。
- 汇总全部 iteration。
- `cache_event_count` 使用本次 replay 的 `CacheEventStats.total_events`。

instance row：

- 只汇总对应 `instance_uuid` 的 request / iteration。
- 第一版 `cache_event_count=0`，含义是 v1 不提供 instance-level event count。
- 后续如果需要，可新增正式的 instance-level event stats，不复用当前字段偷偷改语义。

DDR 相关字段沿用现有 schema：

- `ddr_hit_tokens`
- `ddr_hit_rate`

当前 HBM-only replay 下它们仍由 request metric 输入决定，通常为 0。

## 5. 内存行为

聚合器不保存完整 request metric list，也不保存完整 iteration metric list。

保留状态：

- trace 累加器。
- 每个 instance 一个累加器。
- TTFT exact percentile 所需的 `ttft_ms` 数组。

第一版保持 exact percentile，与旧 path 完全一致，不引入近似 quantile。后续如果 11G trace 下 TTFT 数组成为瓶颈，应新增显式 approximate percentile policy，并在输出中标注口径，不能静默替换。

## 6. 不变量

每条 request metric 进入聚合器前检查：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens
```

每个 scope 输出 row 前再次检查：

```text
total_hit_tokens + miss_tokens == total_prompt_tokens
```

这些检查用于防止 streaming path 在没有完整 metrics list 的情况下静默产生不可信报告。

## 7. 新增测试

```text
tests/unit/streaming/test_metrics.py
```

覆盖：

- streaming aggregator 输出与 `build_capacity_rows()` 一致。
- request / iteration 可交错输入。
- instance rows 具有确定性排序。
- empty trace 仍输出 trace row。
- trace row 保留 `cache_event_count`。
- invalid token accounting fail-fast。

## 8. 验证结果

定向 streaming 测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming
26 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src/hitfloor/streaming tests/unit/streaming
All checks passed!

.venv/bin/python -m ruff format --check src/hitfloor/streaming tests/unit/streaming
14 files already formatted
```

全仓静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
131 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
178 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
178 passed
TOTAL 3469 statements, 242 missed, 93% coverage
```

diff 检查：

```text
git diff --check
passed
```

## 9. 收口结论

Batch TS-D 已完成。

当前 true streaming 已具备：

```text
CSV row -> SimulationRequest -> per-instance JSONL shard
per-instance request source -> streaming replay -> streaming metric aggregator
streaming metric aggregator -> capacity sweep rows
```

但还没有：

```text
streaming capacity sweep runner
CLI / report integration
multi-capacity orchestration over shards
```

下一批建议进入 Batch TS-E：

```text
Streaming Capacity Sweep Runner
```

Batch TS-E 的核心目标是把 shard build、per-instance streaming replay 和 TS-D aggregator 串成 opt-in 的 streaming capacity sweep path，并在同一份 synthetic trace 上验证它与现有 batch capacity sweep 输出一致。
