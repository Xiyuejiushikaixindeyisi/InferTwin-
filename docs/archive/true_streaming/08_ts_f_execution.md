# Batch TS-F 执行记录：Benchmark 与大 Trace 安全

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-F 为 true streaming path 补充 benchmark harness，用于观察大 trace 场景下的吞吐和内存行为。

目标统计项：

- `requests_per_second`
- `iterations_per_second`
- `cache_events_per_second`
- `peak_traced_memory_mb`
- `max_rss_mb`
- `total_elapsed_ms`

本批不改变 replay 语义，不改变旧 `capacity_sweep`，不把大规模 benchmark 加入默认 pytest。

## 2. 新增代码

```text
scripts/benchmark_streaming_replay.py
tests/integration/test_benchmark_streaming_replay_script.py
```

## 3. Benchmark 路径

新增脚本走完整 streaming sweep：

```text
synthetic trace CSV
-> StreamingCapacitySweepRunner
-> StreamingRequestShardBuilder
-> per-instance JSONL shards
-> StreamingBatchAwareReplayEngine
-> CapacitySweepStreamingMetricAggregator
-> write_capacity_sweep_report()
```

它不是只压测某个内部函数，因此能覆盖 true streaming 的实际大 trace 路径。

## 4. 使用方法

示例：

```bash
.venv/bin/python scripts/benchmark_streaming_replay.py \
  --requests 10000 \
  --instances 4 \
  --prompt-words 256 \
  --reuse-period 64 \
  --capacities 128,512 \
  --output-dir reports/streaming_benchmark \
  --output-json reports/streaming_benchmark/benchmark.json
```

默认不写 raw cache events，只统计 event stats。

如果需要验证 selected capacity raw event dump：

```bash
.venv/bin/python scripts/benchmark_streaming_replay.py \
  --requests 1000 \
  --instances 4 \
  --capacities 128,512 \
  --cache-event-capacities 128 \
  --output-dir reports/streaming_benchmark
```

## 5. 输出字段

核心字段：

- `request_count`：synthetic trace 中写入的请求数。
- `accepted_request_count`：tokenizer/build 后进入 replay 的请求数。
- `rejected_request_count`：tokenizer-stage rejected 请求数。
- `capacity_count`：本次 sweep 的 capacity 数量。
- `replayed_request_count`：`accepted_request_count * capacity_count`。
- `iteration_count`：所有 trace-scope rows 的 iteration 总数。
- `cache_event_count`：所有 trace-scope rows 的 cache event 总数。
- `streaming_run_ms`：StreamingCapacitySweepRunner.run() 耗时。
- `report_write_ms`：写 `capacity_sweep.csv` 和 `summary.md` 的耗时。
- `total_elapsed_ms`：synthetic trace 写入、streaming run、report 写入的总耗时。
- `requests_per_second`：`replayed_request_count / streaming_run_ms`。
- `iterations_per_second`：`iteration_count / streaming_run_ms`。
- `cache_events_per_second`：`cache_event_count / streaming_run_ms`。
- `end_to_end_requests_per_second`：`replayed_request_count / total_elapsed_ms`。
- `peak_traced_memory_mb`：`tracemalloc` 记录的 Python allocation 峰值。
- `max_rss_mb`：进程 `resource.getrusage().ru_maxrss` 峰值。

注意：

- `peak_traced_memory_mb` 反映 Python traced allocation，不等同于进程 RSS。
- `max_rss_mb` 更接近进程层面的峰值，但不同 OS 的口径可能不同。

## 6. 安全边界

- 大规模 benchmark 不进入默认 pytest。
- 默认不写 raw cache events，避免 `cache_events.csv` 在大 trace 下膨胀。
- raw cache event dump 只通过 `--cache-event-capacities` 对指定 capacity 打开。
- synthetic trace、shards、report 都写入 `--output-dir`。
- benchmark 只使用现有 `capacity_sweep_streaming` opt-in path，不改变旧 runner。

## 7. 新增测试

```text
tests/integration/test_benchmark_streaming_replay_script.py
```

覆盖：

- benchmark script 可运行。
- 可输出 JSON summary。
- 可写 `capacity_sweep.csv` 和 `summary.md`。
- 可对 selected capacity 写 raw `cache_events.csv`。
- 未选 capacity 不写 raw cache event dump。
- JSON summary 包含吞吐、cache event 速率、峰值内存和总耗时字段。

## 8. 直接 smoke 结果

命令：

```bash
.venv/bin/python scripts/benchmark_streaming_replay.py \
  --requests 8 \
  --instances 2 \
  --prompt-words 4 \
  --reuse-period 2 \
  --capacities 1,4 \
  --cache-event-capacities 1 \
  --output-dir /tmp/hitfloor_ts_f_benchmark \
  --output-json /tmp/hitfloor_ts_f_benchmark.json
```

输出摘要：

```text
request_count: 8
accepted_request_count: 8
rejected_request_count: 0
instance_count: 2
capacity_count: 2
replayed_request_count: 16
iteration_count: 16
cache_event_count: 66
streaming_run_ms: 9260.92150999466
report_write_ms: 0.7050940039334819
total_elapsed_ms: 9589.958033000585
requests_per_second: 1.727689839799671
iterations_per_second: 1.727689839799671
cache_events_per_second: 7.126720589173643
peak_traced_memory_mb: 134.73351001739502
max_rss_mb: 717.72265625
```

该小规模 smoke 中耗时主要受真实 tokenizer 加载影响，不代表大 trace 稳态吞吐。

## 9. 验证结果

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_benchmark_streaming_replay_script.py
1 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
135 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
182 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
182 passed
TOTAL 3581 statements, 254 missed, 93% coverage
```

diff 检查：

```text
git diff --check
passed
```

## 10. 收口结论

Batch TS-F 已完成。

当前 true streaming 已具备：

```text
streaming capacity sweep runner
standard report/export
benchmark harness for throughput and memory observation
```

下一批建议进入 Batch TS-G：

```text
收口与归档
```

Batch TS-G 应进行 true streaming 专项 review，更新主文档和全局记忆，并将 `docs/true_streaming/` 移入 `docs/archive/true_streaming/`。
