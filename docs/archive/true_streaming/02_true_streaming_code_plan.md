# True Streaming 技术路线与代码编写方案

状态：待评审。

任务类型：核心仿真器架构任务。

核心目标：新增 opt-in true streaming path，在不破坏现有 replay 能力的前提下，支持大 trace 低内存 replay。

## 1. 当前问题

当前主链路：

```text
read_trace_csv()
-> build_simulation_request()
-> RequestBuildResult(requests=tuple(...))
-> CapacitySweepRunner.run()
-> requests = list(build_result.requests)
-> BatchAwareReplayEngine.run(requests)
-> _group_by_instance(requests)
-> _run_instance(pending=list)
```

问题：

- accepted `SimulationRequest` 全量常驻内存。
- 每个 request 保存完整 prefix block hash chain。
- `BatchAwareReplayEngine.run()` 先按 instance 分组，形成每个实例的 pending list。
- capacity sweep 对多个 capacity 复用的是内存 list。
- request metrics / iteration metrics 当前也以 tuple 形式返回，外围 report 再聚合。

对 11G CSV、数万条请求、单请求 32K 到 200K tokens 的 trace，内存风险仍然存在。

## 2. 产品边界

新增能力建议命名：

```text
capacity_sweep_streaming
```

它是核心仿真器的新执行路径，不是外围报告能力。

保留旧能力：

```text
capacity_sweep
```

旧路径继续用于：

- 小 trace。
- 单元测试 / golden test。
- 快速开发调试。
- 与 streaming path 做等价性校验。

## 3. True Streaming 语义

第一版 true streaming 必须满足：

- 不构造全量 accepted request list。
- 不构造全量 per-instance pending list。
- request build 逐行处理 CSV。
- accepted request 写入 per-instance shard。
- rejected request 流式写入 sidecar。
- replay 从 per-instance shard 顺序读取。
- replay 只保留 active state。
- capacity sweep 复用 shard，不复用内存 request list。
- 输出的 `capacity_sweep.csv` schema 与现有 report 兼容。

第一版允许：

- 单条 request tokenizer 阶段短暂持有 token ids。
- 单条 request 的 prefix block hash chain 在构造时短暂驻留内存，然后写入 shard。
- 每个 capacity 串行 replay。
- 每个 instance 串行 replay。
- shard 存储在本地磁盘 output directory。

第一版不做：

- tokenizer true streaming。
- external sort。
- parallel instance replay。
- progressive block visibility。
- decode / TPOT。
- DDR / SSD / multi-tier cache。
- gateway routing simulation。

## 4. 输入排序约束

第一版建议采用严格排序 guard，而不是隐式 external sort。

要求：

```text
trace rows must be monotonic by service_start_time.
```

如果同一 timestamp 内有多条请求，稳定 tie-break 使用：

```text
(service_start_time, instance_uuid, request_id)
```

原因：

- offline CSV 如果无序，true streaming replay 无法只靠一个 next-request buffer 保证 arrival order。
- external sort 会引入更大的工程面，包括临时文件、merge sort、错误恢复、磁盘配额和排序稳定性。
- 当前任务核心是移除全量 request materialization，不应把 external sort 混入第一版。

失败行为：

- 如果 `streaming.require_sorted_trace = true` 且发现倒序，直接失败。
- 错误中输出上一条 key、当前 key、line number。
- 不自动重排，不静默继续。

未来扩展：

- 新增 `ExternalSortedTraceBuilder`，使用 chunk sort + merge sort。
- 这是独立大 trace ingestion 任务，不属于 true streaming 第一版。

## 5. 数据模型

### 5.1 ShardManifest

建议文件：

```text
src/hitfloor/streaming/manifest.py
```

核心类型：

```python
@dataclass(frozen=True, slots=True)
class RequestShard:
    instance_uuid: str
    path: Path
    request_count: int
    min_start_time_ms: float | None
    max_start_time_ms: float | None

@dataclass(frozen=True, slots=True)
class StreamingBuildManifest:
    schema_version: str
    trace_path: Path
    shard_root: Path
    shards: tuple[RequestShard, ...]
    accepted_count: int
    rejected_count: int
    require_sorted_trace: bool
```

### 5.2 SerializedSimulationRequest

建议文件：

```text
src/hitfloor/streaming/request_codec.py
```

第一版用 JSONL，不使用 pickle。

原因：

- 可检查、可 diff、可被其他工具消费。
- schema version 明确。
- 不把 Python object layout 固化为长期格式。

每行保存：

```text
schema_version
request_id
tenant_id
instance_uuid
model
service_start_time_iso
start_time_ms
tokenizer_profile
prompt_tokens
prompt_blocks[]
kv_bytes_per_token
requested_block_size
runtime_block_size
effective_block_size
block_conversion_result
```

`prompt_blocks[]` 只保存 hash-only metadata：

```text
block_key
content_hash
block_index
token_count
size_bytes
```

不保存：

- token ids。
- messages。
- raw request JSON。
- 真实 KV tensor。

### 5.3 StreamingBuildResult

建议文件：

```text
src/hitfloor/streaming/build.py
```

```python
@dataclass(frozen=True, slots=True)
class StreamingBuildResult:
    manifest: StreamingBuildManifest
    rejected_path: Path | None
```

## 6. 模块结构

建议新增 package：

```text
src/hitfloor/streaming/
  __init__.py
  manifest.py              # shard manifest schema
  request_codec.py         # SimulationRequest <-> JSONL dict
  shard_store.py           # per-instance shard writer/reader
  build.py                 # CSV -> shard streaming request build
  source.py                # RequestSource / peekable stream abstraction
  replay.py                # streaming replay engine
  metrics.py               # streaming metric sinks and aggregators
  sweep.py                 # StreamingCapacitySweepRunner
```

不建议放在 `report/` 或 `scripts/`：

- request build / replay 是核心仿真器，不是外围 report。
- scripts 只能作为 wrapper。

CLI 可后续新增：

```text
hitfloor sweep --config ...            # old capacity_sweep
hitfloor sweep-streaming --config ...  # explicit streaming path
```

或在 config 中使用：

```yaml
simulation:
  mode: capacity_sweep_streaming
```

## 7. Replay 设计

### 7.1 RequestSource

建议文件：

```text
src/hitfloor/streaming/source.py
```

接口：

```python
class RequestSource(Protocol):
    def peek(self) -> SimulationRequest | None: ...
    def pop(self) -> SimulationRequest: ...
```

实现：

- `JsonlRequestSource`：从单个 instance shard 逐行读取。
- `ListRequestSource`：测试用，把 list 包成 streaming source。

约束：

- source 必须按 `(start_time_ms, request_id)` 有序。
- reader 只保留一个 decoded next request buffer。

### 7.2 StreamingBatchAwareReplayEngine

建议文件：

```text
src/hitfloor/streaming/replay.py
```

不要直接大改 `BatchAwareReplayEngine.run()`。

第一版新增独立 engine：

```python
class StreamingBatchAwareReplayEngine:
    def run_instance_stream(
        self,
        *,
        instance_uuid: str,
        request_source: RequestSource,
        cache: PrefixCache,
        metric_sink: ReplayMetricSink,
        cache_event_sink: CacheEventSink,
    ) -> None:
        ...
```

状态机与现有 `_run_instance()` 对齐：

```text
next request source
-> move arrivals whose start_time_ms <= now_ms into WaitingQueue
-> prepare running frontier
-> prepare waiting frontier
-> scheduler.schedule()
-> latency backend estimate
-> apply scheduled slice
-> materialize finished request
-> emit request metric to sink
-> emit iteration metric to sink
-> delete finished request state
```

关键改动：

- `pending list + pending_index` 改为 `RequestSource.peek()/pop()`。
- `request_metrics` 不 append 到 list，而是 `metric_sink.on_request(metric)`。
- `iteration_metrics` 不 append 到 list，而是 `metric_sink.on_iteration(metric)`。
- request finish 后删除：
  - `states_by_id[request_id]`
  - `requests_by_id[request_id]`
  - `lookup_by_id[request_id]`

这样内存随 active request 数增长，而不是随 trace 总请求数增长。

### 7.3 保持语义不变

Streaming engine 必须复用或等价实现：

- first-schedule-time lookup。
- bounded waiting lookup frontier。
- zero-miss fast finish。
- empty schedule fail-fast。
- `MaterializationPolicy`。
- `ShapeMemo`。
- `ServingLatencyProfile` / fitted latency backend。
- HBM LRU cache。
- stats-only / CSV cache event sink。

streaming path 不修改：

- `BatchShape` 语义。
- `batch_size` 语义。
- cached_tokens accounting。
- `batch_aware_hbm_lru` 默认 finish-time materialization。

## 8. Capacity Sweep Streaming

建议文件：

```text
src/hitfloor/streaming/sweep.py
```

流程：

```text
StreamingCapacitySweepRunner.run()
  -> StreamingRequestShardBuilder.build_once()
  -> for capacity in capacities:
       for shard in manifest.shards:
         source = JsonlRequestSource(shard.path)
         cache = HBMCache(capacity)
         engine.run_instance_stream(...)
       aggregator.emit_capacity_rows()
  -> CapacitySweepResult-compatible object
```

### 8.1 为什么仍然 build once

如果每个 capacity 都重新读 CSV + tokenizer：

- CPU 成本高。
- tokenizer 行为如果依赖外部文件或版本，重复运行增加漂移风险。
- rejected request 统计需要重复生成。

因此第一版采用：

```text
CSV -> shard once -> capacity sweep reads shard many times
```

这是真正的大 trace streaming sweep：复用磁盘 shard，不复用内存 request list。

### 8.2 Metrics Aggregation

建议文件：

```text
src/hitfloor/streaming/metrics.py
```

接口：

```python
class ReplayMetricSink(Protocol):
    def on_request(self, metric: BatchAwareRequestMetrics) -> None: ...
    def on_iteration(self, metric: IterationMetrics) -> None: ...
```

实现：

- `InMemoryReplayMetricSink`：测试和旧等价校验使用。
- `CapacitySweepMetricAggregator`：只维护 capacity rows 所需统计。

Aggregator 需要维护：

- trace-level request count。
- per-instance request count。
- total prompt tokens。
- hbm hit tokens。
- ddr hit tokens。
- miss tokens。
- total hit tokens。
- TTFT quantile estimator。
- iteration count。

第一版 P50 / P90 / P99 建议仍使用 exact list per scope。

原因：

- 几万条 request 的 TTFT float list 内存可以接受。
- 这比引入 approximate quantile 更稳定、更容易与旧 runner 对齐。

未来如果 request 数达到百万级，再新增 quantile sketch，并新增结果口径字段，不静默替换 exact percentile。

## 9. 错误处理

### 9.1 prompt too long

行为：

- tokenizer 阶段捕获 `PromptTooLongError`。
- 流式写入 `rejected_requests.csv`。
- 不写入 request shard。
- `StreamingBuildManifest.rejected_count += 1`。

### 9.2 request parse error

行为建议：

- 默认 fail-fast。
- 不静默跳过。
- 未来如需要容错，新增显式 config，例如 `trace.on_parse_error: reject`。

### 9.3 unsorted trace

行为：

- fail-fast。
- 错误包含 line number、previous key、current key。

### 9.4 duplicate request id

行为：

- 同一 instance 内 duplicate request id 默认 fail-fast。
- 不跨全 trace 建 set，因为这会重新引入全量内存。
- 第一版可在 per-instance streaming replay active state 中检测 active duplicate。
- 如果需要全 trace duplicate 检测，后续用 disk-backed bloom / SQLite index，单独设计。

## 10. 测试计划

### TS-A tests：codec / manifest

新增：

```text
tests/unit/streaming/test_request_codec.py
tests/unit/streaming/test_manifest.py
```

覆盖：

- `SimulationRequest` JSONL roundtrip。
- `PrefixBlock` roundtrip。
- schema version 不匹配时报错。
- 缺字段时报错。

### TS-B tests：streaming request build

新增：

```text
tests/unit/streaming/test_build.py
```

覆盖：

- sorted trace 生成 per-instance shard。
- prompt too long 写 rejected sidecar。
- unsorted trace fail-fast。
- request schema parse error fail-fast。
- manifest counts 正确。

### TS-C tests：request source

新增：

```text
tests/unit/streaming/test_source.py
```

覆盖：

- `peek()` 不消费。
- `pop()` 消费。
- EOF 返回 None。
- source sorted guard。

### TS-D tests：streaming replay equivalence

新增：

```text
tests/unit/streaming/test_streaming_replay.py
```

覆盖：

- 同一 synthetic request list，`BatchAwareReplayEngine.run(list)` 与 `StreamingBatchAwareReplayEngine.run_instance_stream(ListRequestSource)` 输出 request metrics 相同。
- zero-miss fast finish 相同。
- cache event stats 相同。
- materialization 后才可见的语义保持不变。

### TS-E tests：streaming capacity sweep

新增：

```text
tests/integration/test_streaming_capacity_sweep_runner.py
tests/integration/test_streaming_capacity_sweep_cli.py
```

覆盖：

- streaming runner 与旧 `CapacitySweepRunner` 在小合成 trace 上输出相同 trace / instance rows。
- 多 capacity 复用 shard。
- cache events 只对指定 capacity dump。
- rejected count 写入 config details。

### TS-F benchmark

新增脚本：

```text
scripts/benchmark_streaming_replay.py
```

目的：

- 压测 shard build throughput。
- 压测 replay throughput。
- 输出 requests/s、iterations/s、cache_events/s、peak memory、elapsed time。

不进入默认 pytest。

## 11. Batch 开发顺序

### Batch TS-A：文档、schema、codec

产出：

- `src/hitfloor/streaming/__init__.py`
- `manifest.py`
- `request_codec.py`
- unit tests。

验收：

- roundtrip 测试通过。
- 不接入 runner。
- 旧 pytest 全绿。

### Batch TS-B：Streaming Request Shard Builder

产出：

- `shard_store.py`
- `build.py`
- rejected writer。
- sorted trace guard。

验收：

- 小 CSV 能生成 manifest 和 per-instance JSONL shard。
- prompt too long 被拒绝。
- unsorted trace fail-fast。
- 不修改现有 `build_request_build_result_from_config()`。

### Batch TS-C：RequestSource 与 Streaming Replay Engine

产出：

- `source.py`
- `replay.py`
- `ReplayMetricSink` 初版。

验收：

- synthetic list source 与现有 list replay request metrics 等价。
- active state 在 request finish 后释放。
- 不接 runner/report。

### Batch TS-D：Streaming Metrics Aggregator

产出：

- `metrics.py`
- exact percentile aggregation。
- per-trace / per-instance rows builder。

验收：

- 与 `build_capacity_rows()` 小样本输出一致。
- ddr 字段保持 0。
- cache event count 口径与 Step6 一致。

### Batch TS-E：StreamingCapacitySweepRunner

产出：

- `sweep.py`
- config validation。
- package CLI opt-in。
- script wrapper 可选。

验收：

- `capacity_sweep_streaming` 与旧 `capacity_sweep` 在合成数据上核心指标一致。
- old `capacity_sweep` 不受影响。
- cache event dump 仍只允许指定 capacity。

### Batch TS-F：Benchmark 与大 trace 安全

产出：

- `scripts/benchmark_streaming_replay.py`
- benchmark 文档。

验收：

- 输出 requests/s、iterations/s、cache_events/s、peak memory、elapsed time。
- 大规模 benchmark 不进入默认 pytest。

### Batch TS-G：收口与归档

产出：

- 更新 `docs/global_memory.md`。
- 更新 `docs/core_simulator_technical_plan.md`。
- 将 `docs/true_streaming/` 移入 `docs/archive/true_streaming/`。
- 新增 review 文档到 `docs/reviews/`。

验收：

- ruff check / format / pytest / coverage 通过。
- streaming runner 和 old runner 等价性测试通过。
- 文档说明 true streaming 与 old replay path 的关系。

## 12. 验收标准

代码完成后必须满足：

```text
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
PYTHONPATH=src .venv/bin/python -m pytest
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
```

功能验收：

- old `capacity_sweep` 行为不变。
- new `capacity_sweep_streaming` 在小合成 trace 上与 old runner 输出相同核心指标。
- streaming path 不构造全量 `SimulationRequest` list。
- streaming path 不构造 per-instance pending list。
- request finish 后 active state 被释放。
- rejected request 可追踪。
- unsorted trace fail-fast。

性能验收：

- benchmark 输出峰值内存。
- 对合成大 trace，内存随 active request 数和 cache metadata 增长，而不是随 total request 数线性增长。

## 13. 风险与取舍

### 13.1 JSONL shard 磁盘体积

风险：

- 每个 request 的 prefix block hash chain 会写入 JSONL。
- 200K tokens prompt 在 block size 128 时约 1563 个 blocks，单行较大。

取舍：

- JSONL 可维护、可调试、无新增依赖。
- 如果磁盘体积成为瓶颈，再新增 binary shard codec 或 zstd/gzip option。
- 不在第一版引入压缩，避免 CPU 成本和测试复杂度混在一起。

### 13.2 Exact percentile 仍保存 TTFT list

风险：

- 百万级 request 时 TTFT list 也会产生内存。

取舍：

- 当前公司内目标是几万条请求，exact percentile 更可复现。
- 未来新增 quantile sketch 时必须新增字段或明确口径，不能静默替换。

### 13.3 Trace sorted guard

风险：

- 如果真实 CSV 无序，第一版会失败。

取舍：

- 失败比静默重排更可信。
- external sort 是独立任务。

### 13.4 与 progressive block visibility 的关系

true streaming 和 progressive block visibility 是两个独立任务。

- true streaming 解决输入和 replay 内存。
- progressive block visibility 解决长 prefill 期间 block reuse 低估。

实现 true streaming 时必须保留 `MaterializationPolicy` 边界，避免后续 progressive mode 难以接入。

