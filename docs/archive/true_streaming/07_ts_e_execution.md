# Batch TS-E 执行记录：Streaming Capacity Sweep Runner

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-E 将 TS-B / TS-C / TS-D 串成 opt-in 的 streaming capacity sweep path：

```text
CSV trace
-> streaming request shard build
-> per-instance JsonlRequestSource
-> StreamingBatchAwareReplayEngine
-> CapacitySweepStreamingMetricAggregator
-> CapacitySweepResult
```

目标是让大 trace path 不再构造全量 accepted `SimulationRequest` list，也不构造 per-instance pending list，同时保持与旧 `CapacitySweepRunner` 相同的核心指标口径。

本批仍不改变旧路径：

```text
hitfloor sweep
-> CapacitySweepRunner
-> BatchAwareReplayEngine.run(list[SimulationRequest])
```

## 2. 新增和修改代码

新增：

```text
src/hitfloor/streaming/sweep.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

修改：

```text
src/hitfloor/streaming/__init__.py
src/hitfloor/cli/main.py
src/hitfloor/experiment/sweep.py
```

## 3. 新增 Runner

新增：

- `StreamingCapacitySweepRunner`
- `StreamingCapacitySweepConfig`
- `STREAMING_CAPACITY_SWEEP_MODE = "capacity_sweep_streaming"`
- `build_streaming_capacity_sweep_config()`

`StreamingCapacitySweepRunner.run()` 返回现有 `CapacitySweepResult`，因此 report/export 层可以继续使用：

```text
write_capacity_sweep_report(result, output_dir)
```

职责：

- 逐行 build request shard。
- 对每个 `hbm_capacity_blocks` 重放全部 instance shard。
- 每个 instance 使用独立 HBM cache，保持固定路由、多实例隔离 replay。
- 使用 streaming aggregator 生成 trace / instance rows。
- 只对指定 capacity 打开 raw cache event dump。

不负责：

- 修改旧 `CapacitySweepRunner`。
- 写 `capacity_sweep.csv` / `summary.md` 的具体格式。
- 多实例并行 replay。
- approximate percentile。
- progressive block visibility。
- DDR / SSD / multi-tier cache。

## 4. Opt-In 入口

新增 package CLI 子命令：

```text
hitfloor sweep-streaming --config <config.yaml>
```

对应 config mode：

```yaml
simulation:
  mode: capacity_sweep_streaming
```

旧入口保持不变：

```text
hitfloor sweep --config <config.yaml>
```

旧入口仍要求：

```yaml
simulation:
  mode: capacity_sweep
```

因此 streaming path 是显式 opt-in，不会让旧 capacity sweep 静默变语义。

## 5. Streaming 配置

新增可选配置：

```yaml
streaming:
  shard_root: reports/streaming_shards
  rejected_path: reports/rejected_requests.csv
  require_sorted_trace: true
```

默认值：

- `shard_root = output.directory / "streaming_shards"`
- `rejected_path = output.directory / "rejected_requests.csv"`
- `require_sorted_trace = true`

`rejected_path` 用于 tokenizer-stage long request rejection sidecar。没有 rejected request 时不会生成有效 rejected 输出路径。

## 6. 等价性与安全检查

本批新增的 runner 会检查：

- 每个 shard replay emitted request count 等于 shard manifest request count。
- 每个 instance replay 完成后 `final_active_requests == 0`。
- streaming aggregator 内部继续检查 token accounting invariant。

与旧 runner 的等价性测试覆盖：

```text
CapacitySweepRunner
StreamingCapacitySweepRunner
```

在同一份 synthetic trace、同一组 capacity、同一 latency backend 和同一 cache/scheduler 配置下，两者输出的 `CapacitySweepRow` 完全一致。

## 7. 新增测试

```text
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

覆盖：

- streaming runner 与旧 batch runner rows 完全一致。
- selected capacity raw cache events 正常写出。
- 未选 capacity 不写 raw cache events。
- streaming result 可被现有 `write_capacity_sweep_report()` 消费。
- package CLI `run_streaming_capacity_sweep()` 可写出 `capacity_sweep.csv` 和 `summary.md`。
- streaming shard directory 会生成。

## 8. 验证结果

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_true_streaming_capacity_sweep_runner.py tests/unit/streaming
29 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
133 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
181 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
181 passed
TOTAL 3581 statements, 256 missed, 93% coverage
```

diff 检查：

```text
git diff --check
passed
```

## 9. 收口结论

Batch TS-E 已完成。

当前 true streaming 已具备端到端 opt-in sweep：

```text
hitfloor sweep-streaming
-> streaming request shard build
-> per-instance streaming replay
-> streaming metric aggregation
-> standard capacity sweep report
```

旧路径仍保持：

```text
hitfloor sweep
-> in-memory request build
-> list replay
-> standard capacity sweep report
```

下一批建议进入 Batch TS-F：

```text
Benchmark 与大 trace 安全
```

Batch TS-F 的重点是补 `scripts/benchmark_streaming_replay.py`，输出 requests/s、iterations/s、cache_events/s、峰值内存和总耗时。大规模 benchmark 不进入默认 pytest。
