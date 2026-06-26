# Batch TS-C 执行记录：RequestSource 与 Streaming Replay Engine

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-C 实现 true streaming 的 per-instance replay 基础：

```text
per-instance request source
-> streaming replay state machine
-> metric sink
```

目标是把现有 `_run_instance(pending=list)` 中的 pending list 替换为 `RequestSource.peek()/pop()`，并将 request / iteration metrics 逐条写入 sink，而不是在 replay 内保留完整 metrics list。

本批不接入：

- streaming capacity sweep runner。
- CLI / report。
- streaming metric aggregator。
- 多实例并行 replay。

因此本批不会改变现有 `capacity_sweep`、`BatchAwareReplayEngine.run(list[SimulationRequest])` 或 `batch_aware_hbm_lru` replay 语义。

## 2. 新增代码

```text
src/hitfloor/streaming/source.py
src/hitfloor/streaming/metrics.py
src/hitfloor/streaming/replay.py
```

## 3. `source.py`

新增：

- `RequestSource`
- `ListRequestSource`
- `JsonlRequestSource`
- `UnsortedRequestSourceError`

职责：

- 提供 `peek()` / `pop()` request source 抽象。
- `ListRequestSource` 用于测试和小规模等价校验。
- `JsonlRequestSource` 从 TS-B 生成的 per-instance JSONL shard 逐行 decode request。
- 默认按 `(start_time_ms, request_id)` 做 source-level sorted guard。

不负责：

- build request shard。
- replay。
- metric aggregation。

## 4. `metrics.py`

新增：

- `ReplayMetricSink`
- `InMemoryReplayMetricSink`
- `StreamingReplayStats`

职责：

- 定义 streaming replay 的 request / iteration metric sink 接口。
- 提供测试和小 trace 等价校验用的 in-memory sink。
- 记录 `emitted_request_count`、`emitted_iteration_count`、`max_active_requests`、`final_active_requests`。

后续 Batch TS-D 会新增真正的 capacity sweep streaming aggregator。

## 5. `replay.py`

新增：

- `StreamingBatchAwareReplayEngine`

设计：

- 继承 `BatchAwareReplayEngine`。
- 复用现有 `_prepare_scheduler_frontier()`、`_apply_schedule_result()`、`_estimate_latency()`。
- 保持现有语义：
  - first-schedule-time lookup。
  - bounded waiting lookup frontier。
  - zero-miss fast finish。
  - empty schedule fail-fast。
  - `MaterializationPolicy`。
  - `ShapeMemo`。
  - HBM LRU / prefix cache accounting。

核心差异：

```text
old:
  pending list + pending_index
  request_metrics list
  iteration_metrics list

streaming:
  RequestSource.peek()/pop()
  ReplayMetricSink.on_request()
  ReplayMetricSink.on_iteration()
```

内存行为：

- replay 只保留当前 instance 的 active state。
- request finish 后释放：
  - `states_by_id[request_id]`
  - `requests_by_id[request_id]`
  - `lookup_by_id[request_id]`
- metrics 不在 engine 内长期保存。

当前限制：

- `StreamingBatchAwareReplayEngine.run_instance_stream()` 只处理单 instance source。
- 多 instance orchestration 留给 Batch TS-E 的 streaming sweep runner。

## 6. 新增测试

```text
tests/unit/streaming/test_source.py
tests/unit/streaming/test_streaming_replay.py
```

覆盖：

- `ListRequestSource.peek()` 不消费。
- `ListRequestSource.pop()` 消费。
- empty source pop fail-fast。
- source sorted guard。
- `JsonlRequestSource` 可读取 encoded request shard。
- invalid JSON line 带 line number 报错。
- streaming replay 与旧 list replay 在单 instance synthetic trace 上 request / iteration metrics 等价。
- zero-miss fast finish 保持不变。
- cache events 写入外部 sink。
- instance mismatch fail-fast。
- replay finish 后 `final_active_requests == 0`。

## 7. 验证结果

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming
23 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
130 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
175 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
175 passed
TOTAL 3403 statements, 240 missed, 93% coverage
```

新增 streaming 模块覆盖情况：

| 模块 | 覆盖率 |
| --- | ---: |
| `streaming/build.py` | 99% |
| `streaming/manifest.py` | 89% |
| `streaming/metrics.py` | 97% |
| `streaming/replay.py` | 92% |
| `streaming/request_codec.py` | 87% |
| `streaming/shard_store.py` | 97% |
| `streaming/source.py` | 97% |

## 8. 收口结论

Batch TS-C 已完成。

当前 true streaming 已具备：

```text
CSV row -> SimulationRequest -> per-instance JSONL shard
per-instance request source -> streaming replay -> metric sink
```

但还没有：

```text
streaming metrics aggregator
streaming capacity sweep runner
CLI / report integration
```

下一批建议进入 Batch TS-D：

```text
Streaming Metrics Aggregator
```

Batch TS-D 的核心目标是：

- 设计 capacity sweep 所需的 streaming metric aggregator。
- 支持 trace / instance scope rows。
- 与现有 `build_capacity_rows()` 在小样本上输出一致。
- 保持 exact percentile，不引入 approximate quantile。

