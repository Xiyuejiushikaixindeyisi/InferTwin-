# Step6 Technical Route and Code Plan

Step6 主题：

```text
HBM Cache Capacity Sweep Report
```

本文基于 `docs/archive/step6/01_product_shape.md`，定义 Step6 技术路线、代码结构、数据模型、算法逻辑和测试方案。Step6 v1 代码开发已按本文完成，并已通过功能验收。

## 1. 技术目标

Step6 在 Step5 单次 `batch_aware_hbm_lru` replay 基础上，新增 HBM capacity sweep：

```text
build SimulationRequest list once
for each hbm_capacity_blocks:
    run finite HBM LRU replay
    aggregate trace-level metrics
    aggregate per-instance metrics
return structured CapacitySweepResult
report/export layer writes capacity_sweep.csv
report/export layer writes summary.md
```

核心仿真器输出是结构化 `CapacitySweepResult` / `CapacitySweepRow`。
`capacity_sweep.csv` 是外围 report/export 能力，不属于 replay core。
标准导出形态是一张 long-format `capacity_sweep.csv`：

```text
hbm_capacity_blocks,scope,instance_uuid,kv_hit_rate,p90_ttft_ms,...
512,trace,,0.42,850.0,...
512,instance,instance-a,0.51,720.0,...
512,instance,instance-b,0.33,980.0,...
```

## 1.1 核心仿真器与外围能力

Step6 必须继续保持核心仿真器和外围能力分层。

核心仿真器包括：

- request build。
- scheduler replay。
- cache lookup / materialization / eviction。
- latency backend。
- request / iteration / sweep metrics 聚合。
- typed result，例如 `CapacitySweepResult`。

外围能力包括：

- HitFloor 表。
- `capacity_sweep.csv`。
- `summary.md`。
- CLI / scripts wrapper。
- 后续 dashboard、Notebook、batch job。
- 后续 P90 target matching / hit floor search。

本阶段的 `HBM Cache Capacity Sweep Report` 是基于核心仿真骨架实现的一个外围能力。
它不能反向污染 replay core；report/export 只能消费 typed result，不能重算、改写或隐藏核心 replay 语义。

## 2. 关键决策

已确认：

- Step6 主题是 `HBM Cache Capacity Sweep Report`。
- 第一版只 sweep `hbm_capacity_blocks`，不接受 GB 输入。
- 第一版只输出 capacity 与指标关系表，不做 P90 target matching。
- 标准用户导出是 long-format `capacity_sweep.csv`，用 `scope=trace/instance` 区分总 trace 和每实例指标。
- `capacity_sweep.csv` 是 report/export 层输出，`CapacitySweepRunner` 只返回结构化结果。
- 保留 `ddr_hit_tokens` / `ddr_hit_rate` 字段，但 Step6 恒为 0。
- sweep 默认关闭 cache event 明细。
- 只允许对指定 capacity 开启 event dump，避免 sweep 默认生成大量事件文件。
- request build once，capacity sweep 复用 requests，不做 true streaming build。
- 多实例并行 replay 先作为可选/后续项，不影响第一版 sweep。
- 单线程 sweep 稳定后，再新增 `ParallelCapacitySweepRunner` 或显式 execution backend。
- `cache_event_count` 第一版只在 trace row 表示本次 replay 的事件总数；instance row 填 0，明确表示 Step6 v1 不提供 instance-level event count。

## 3. 模块结构

### 3.1 新增模块

```text
src/hitfloor/experiment/sweep.py
src/hitfloor/report/sweep.py
configs/experiments/step6_capacity_sweep.yaml
tests/unit/experiment/test_sweep_metrics.py
tests/unit/report/test_sweep_summary.py
tests/integration/test_step6_capacity_sweep_runner.py
tests/integration/test_step6_capacity_sweep_cli.py
```

可选 wrapper：

```text
scripts/run_capacity_sweep.py
```

建议第一版新增 wrapper，保持与 `scripts/run_simulation.py` 一致的本地使用体验。

### 3.2 不修改或尽量少改的模块

```text
src/hitfloor/replay/event_loop.py
src/hitfloor/cache/hbm_lru.py
src/hitfloor/scheduler/vllm_like.py
src/hitfloor/latency/*
```

Step6 不改变 replay / scheduler / cache 语义。

### 3.3 需要轻量修改的模块

```text
src/hitfloor/experiment/runner.py
src/hitfloor/cli/main.py
src/hitfloor/report/tables.py
src/hitfloor/report/summary.py
```

修改原则：

- `ExperimentRunner` 继续负责单次 replay modes。
- `CapacitySweepRunner` 负责 sweep，不把 sweep 逻辑塞进 `ExperimentRunner._run_batch_aware_hbm_lru()`。
- `CapacitySweepRunner` 不写 `capacity_sweep.csv` / `summary.md`，只返回 typed result。
- CLI 只解析参数、调用 runner、调用 report/export writer、打印输出路径。
- report/export 层只把 typed rows 写成 CSV / Markdown，不重新分析 replay 语义。

## 4. 数据模型

### 4.1 `CapacitySweepConfig`

建议放在：

```text
src/hitfloor/experiment/sweep.py
```

建议 schema：

```python
@dataclass(frozen=True, slots=True)
class CapacitySweepConfig:
    capacities: tuple[int, ...]
    cache_events: bool
    cache_event_capacities: tuple[int, ...]
```

职责：

- 保存已经校验过的 capacity candidates。
- 保存 sweep 级 output 控制。
- 指定哪些 capacity 需要输出 cache event 明细。

不负责：

- 解析完整 YAML。
- 构造 tokenizer。
- 跑 replay。
- 写 CSV / Markdown。

### 4.2 `CapacitySweepRow`

建议 schema：

```python
@dataclass(frozen=True, slots=True)
class CapacitySweepRow:
    hbm_capacity_blocks: int
    scope: str
    instance_uuid: str
    request_count: int
    iteration_count: int
    total_prompt_tokens: int
    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int
    total_hit_tokens: int
    kv_hit_rate: float
    hbm_hit_rate: float
    ddr_hit_rate: float
    p50_ttft_ms: float
    p90_ttft_ms: float
    p99_ttft_ms: float
    cache_event_count: int
```

`scope` 只允许：

```text
trace
instance
```

`instance_uuid`：

- `scope=trace` 时为空字符串。
- `scope=instance` 时为实际 instance uuid。

### 4.3 `CapacitySweepResult`

建议 schema：

```python
@dataclass(frozen=True, slots=True)
class CapacitySweepResult:
    rows: tuple[CapacitySweepRow, ...]
    config_details: Mapping[str, object]
    cache_event_paths: Mapping[int, Path]
```

`CapacitySweepResult` 是核心 runner 的返回值，不包含 `capacity_sweep_path` / `summary_path`。

report/export 层可以返回独立的路径对象：

```python
@dataclass(frozen=True, slots=True)
class CapacitySweepReportPaths:
    capacity_sweep_path: Path
    summary_path: Path
```

如果要沿用 `ExperimentResult`，也可以在 CLI/report 边界做薄 adapter，但核心 sweep runner 内部仍应使用 typed result / rows，避免所有信息都塞进 `dict[str, Any]`。

建议：

- 内部返回 `CapacitySweepResult`。
- report/export writer 接收 `CapacitySweepResult` 或 `rows` 后落盘。
- CLI 打印 report/export writer 返回的路径。
- `cache_event_paths` 只记录被显式开启 event dump 的 capacity。
- 如果需要与 `ExperimentResult` 对齐，技术实现时在外围做薄 adapter。

## 5. 配置解析

### 5.1 Runnable Config

新增：

```text
configs/experiments/step6_capacity_sweep.yaml
```

关键字段：

```yaml
simulation:
  mode: capacity_sweep

sweep:
  hbm_capacity_blocks: [512, 1024, 2048]

output:
  directory: reports/step6_capacity_sweep
  cache_events: false
  cache_event_capacities: []
```

### 5.2 Config Guard

必须校验：

- `simulation.mode == "capacity_sweep"`。
- `sweep` 是 mapping。
- `sweep.hbm_capacity_blocks` 是非空 list。
- 每个 capacity 是正整数。
- capacity candidates 不允许重复。
- `cache.eviction_policy` 缺省为 `lru`，如存在则必须为 `lru`。
- `output.cache_events` 缺省为 false，必须是 bool。
- `output.cache_event_capacities` 缺省为空 list，必须是 positive int list。
- `output.cache_events == true` 时，`output.cache_event_capacities` 必须非空。
- `output.cache_event_capacities` 必须是 `sweep.hbm_capacity_blocks` 的子集。
- 不接受 `targets` 作为 Step6 第一版输入。

重复 capacity 的处理建议：

```text
直接失败
```

原因：

- 重复 capacity 会让 long table 出现重复行，影响用户理解。
- 这属于配置错误，不应静默去重。

### 5.3 不接受 `targets`

如果 config 中出现：

```yaml
targets:
  p90_ttft_ms: [...]
```

建议直接失败：

```text
Step6 capacity_sweep does not support targets; use hbm_capacity_blocks sweep and inspect capacity_sweep.csv.
```

原因：

- 用户已明确 Step6 不做 P90 target matching。
- 避免旧 future template 误用。

## 6. Replay 复用策略

### 6.1 Build Once

`CapacitySweepRunner.run()` 应先构造一次 requests：

```text
requests = build_simulation_requests(...)
```

然后对每个 capacity 复用：

```text
for capacity in capacities:
    replay_result = run_capacity(capacity, requests)
```

注意：

- `SimulationRequest` 是 frozen dataclass，可复用。
- replay 内部会为每次 run 新建 `RequestState`，不会修改 `SimulationRequest`。
- 每个 capacity 必须新建 cache factory，避免 cache state 串扰。

### 6.2 每个 Capacity 的 Replay

每个 capacity 使用：

```python
BatchAwareReplayEngine(
    scheduler=VllmLikeBatchScheduler(_build_scheduler_config(config)),
    latency_backend=build_batch_latency_backend(config),
    cache_factory=lambda _instance_uuid: HBMCache(
        capacity_blocks=capacity,
        evictor=LRUEvictor(),
    ),
).run(requests, cache_event_sink=sink)
```

建议每个 capacity 新建：

- scheduler。
- latency backend 或至少 new `BatchAwareReplayEngine`。
- cache factory。
- cache event sink。

这样避免 shape memo 或内部状态跨 capacity 产生难以解释的影响。

`ShapeMemo` 跨 capacity 复用不是必要的。第一版建议不跨 capacity 共享 memo，保持结果路径简单。

### 6.3 Cache Events

第一版默认不输出 event 明细，但仍需要记录 trace-level `cache_event_count`。
因此 Step6 不应使用纯 `NullCacheEventSink` 作为默认 sink，而应使用 stats-only sink：

```text
output.cache_events: false
    -> StatsOnlyCacheEventSink

output.cache_events: true
    -> selected capacities use CsvCacheEventWriter
    -> non-selected capacities use StatsOnlyCacheEventSink
```

如果当前代码中没有 stats-only sink，Step6 应新增：

```text
src/hitfloor/cache/event_sink.py::StatsOnlyCacheEventSink
```

职责：

- 只维护 `CacheEventStats`。
- 不保存 `CacheEvent` 列表。
- 不写文件。
- 用于 sweep 默认路径，避免大文件和内存膨胀。

只允许对指定 capacity 开启 event dump：

```yaml
output:
  cache_events: true
  cache_event_capacities: [512]
```

输出路径：

```text
reports/step6_capacity_sweep/capacity_512/cache_events.csv
```

Config guard：

- `cache_events: true` 且 `cache_event_capacities` 为空：失败。
- `cache_event_capacities` 包含不在 sweep candidate 中的 capacity：失败。
- `cache_events: false` 且 `cache_event_capacities` 非空：失败。

原因：

- sweep 默认关注 capacity 与指标关系，不关注逐事件审计。
- 指定 capacity event dump 可以保留调试能力。
- 明确指定 capacity 可以避免 `trace_size * capacity_count` 级别的输出膨胀。

## 7. Aggregation Algorithm

### 7.1 Trace-Level Row

输入：

```text
request_metrics: tuple[BatchAwareRequestMetrics, ...]
iteration_metrics: tuple[IterationMetrics, ...]
cache_event_stats: CacheEventStats
capacity: int
```

聚合：

```text
request_count = len(request_metrics)
iteration_count = len(iteration_metrics)
total_prompt_tokens = sum(prompt_tokens)
hbm_hit_tokens = sum(hbm_hit_tokens)
ddr_hit_tokens = sum(ddr_hit_tokens)
miss_tokens = sum(miss_tokens)
total_hit_tokens = hbm_hit_tokens + ddr_hit_tokens
kv_hit_rate = total_hit_tokens / total_prompt_tokens
hbm_hit_rate = hbm_hit_tokens / total_prompt_tokens
ddr_hit_rate = ddr_hit_tokens / total_prompt_tokens
p50/p90/p99 = percentile(ttft_ms)
cache_event_count = cache_event_stats.total_events
```

### 7.2 Instance-Level Rows

按 `instance_uuid` 分组 request metrics：

```text
for instance_uuid in sorted(instance_uuid):
    aggregate rows for that instance
```

iteration_count：

- 对 instance row，统计 `iteration_metrics` 中同 instance 的数量。

cache_event_count：

- `CacheEventStats` 当前是 replay-level stats，不含 per-instance stats。
- Step6 第一版不输出每实例 `cache_event_count` 细分，instance row 的 `cache_event_count` 固定为 0。

建议采用：

```text
trace row: cache_event_count = replay_result.cache_event_stats.total_events
instance row: cache_event_count = 0
```

并在 summary 中说明：

- trace row 的 `cache_event_count` 是本 capacity replay 的事件总数。
- instance row 的 `cache_event_count = 0` 表示 Step6 v1 不提供 instance-level event count，不表示该 instance 没有 cache event。
- 后续如需要，可以新增 `instance_cache_event_count` 或正式支持 per-instance event stats。

如果必须 per-instance cache event count，需要升级 event stats 为 per-instance stats；这不是 Step6 第一版必要项。

### 7.3 Percentile

沿用当前 summary 中的 nearest-rank percentile：

```text
rank = ceil((percentile / 100) * len(values))
index = min(max(rank - 1, 0), len(values) - 1)
```

建议把 percentile helper 放到 `experiment/sweep.py` 或 `report/summary.py` 内部，不引入 numpy/pandas。

### 7.4 不变量

每行必须满足：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens
total_hit_tokens == hbm_hit_tokens + ddr_hit_tokens
kv_hit_rate == total_hit_tokens / total_prompt_tokens
hbm_hit_rate == hbm_hit_tokens / total_prompt_tokens
ddr_hit_rate == ddr_hit_tokens / total_prompt_tokens
```

如果 `total_prompt_tokens == 0`：

- hit rates 输出 0.0。
- 不抛错。

## 8. Report / Export 输出

`capacity_sweep.csv` 和 `summary.md` 是外围 report/export 能力。
`CapacitySweepRunner` 不直接写文件。

建议调用关系：

```text
result = CapacitySweepRunner(config).run()
paths = write_capacity_sweep_report(result, output_dir)
```

这样后续 dashboard、Notebook、批处理或服务化接口可以直接复用 `CapacitySweepResult`，不必从 CSV 反解析。

### 8.1 `capacity_sweep.csv`

新增 writer 可以放在：

```text
src/hitfloor/report/sweep.py
```

脚本入口可放在：

```text
scripts/run_capacity_sweep.py
```

writer 可以直接复用：

```python
write_csv_table(capacity_sweep_path, _dataclass_rows(rows))
```

注意：

- long-format schema 固定。
- 行排序必须 deterministic：

```text
(hbm_capacity_blocks, scope_order, instance_uuid)
```

其中：

```text
scope_order(trace)=0
scope_order(instance)=1
```

### 8.2 `summary.md`

新增：

```text
src/hitfloor/report/sweep.py
```

建议函数：

```python
def write_capacity_sweep_summary(
    path: str | Path,
    *,
    rows: Sequence[CapacitySweepRow],
    config_details: Mapping[str, object],
) -> None:
    ...
```

summary 内容：

- 标题：`# HitFloor Capacity Sweep Summary`
- assumptions：
  - fixed-routing, multi-instance isolated replay。
  - finite HBM LRU。
  - no DDR / SSD。
  - cache events detail disabled by default。
- capacities。
- trace-level table。
- per-instance note。
- latency backend details。
- cache event dump capacities。

第一版可以用 Markdown bullet/table，不需要复杂渲染。

## 9. CLI

### 9.1 Package CLI

修改：

```text
src/hitfloor/cli/main.py
```

新增子命令：

```bash
hitfloor sweep --config configs/experiments/step6_capacity_sweep.yaml
```

行为：

- 加载 YAML。
- 调用 `CapacitySweepRunner(config).run()`。
- 调用 `write_capacity_sweep_report(...)`。
- 打印：
  - phase。
  - output directory。
  - capacity sweep path。
  - summary path。

CLI 不做：

- 解析业务 config。
- 聚合 metrics。
- 重新分析 replay 语义。

### 9.2 Script Wrapper

新增：

```text
scripts/run_capacity_sweep.py
```

行为类似：

```python
return cli_main(["sweep", *args])
```

不承载核心逻辑。
如果后续希望 CSV 导出只通过 scripts 暴露，也应保持脚本只调用 package 内的 runner/report API，不把 aggregation 写进脚本。

## 10. Experiment Runner 边界

建议新增：

```text
src/hitfloor/experiment/sweep.py
```

而不是把 sweep 逻辑放进 `ExperimentRunner`。

原因：

- `ExperimentRunner` 已负责单次 replay modes。
- sweep 是多次 replay orchestration。
- 后续 DDR / hit solver / parallel replay 也会继续扩展，多塞进 `ExperimentRunner` 会降低可维护性。

`CapacitySweepRunner` 可以复用 `ExperimentRunner._build_requests()` 的逻辑吗？

建议第一版：

- 不直接调用 private method。
- 在 `experiment/runner.py` 中抽出 public helper 或新模块：

```text
src/hitfloor/experiment/requests.py
```

但为了控制改动，也可以在 `sweep.py` 复制非常少量 request build 逻辑吗？

建议不复制。更好的边界：

```text
src/hitfloor/experiment/request_builder.py
```

提供：

```python
def build_requests_from_config(config: Mapping[str, Any]) -> list[SimulationRequest]:
    ...
```

然后：

- `ExperimentRunner._build_requests()` 调用该 helper。
- `CapacitySweepRunner.run()` 调用该 helper。

这能消除重复逻辑，也符合模块职责单一。

## 11. 代码开发批次

### Batch S1: Request builder helper + sweep metrics schema

实现：

- 新增 `src/hitfloor/experiment/request_builder.py`。
- `ExperimentRunner._build_requests()` 改为调用 helper。
- 新增 `StatsOnlyCacheEventSink`，用于 sweep 默认路径计数但不落明细。
- 新增 `src/hitfloor/experiment/sweep.py` 中的 dataclasses：
  - `CapacitySweepRow`
  - `CapacitySweepResult`
- 新增 cache event dump capacity 解析结构。
- 新增 aggregation helpers。

测试：

- request builder 与现有 sample trace 行为一致。
- trace-level aggregation 不变量。
- instance-level aggregation 不变量。
- percentile helper。
- stats-only sink 只计数不保留 event payload。

建议测试：

```text
tests/unit/experiment/test_request_builder.py
tests/unit/experiment/test_sweep_metrics.py
```

### Batch S2: CapacitySweepRunner

实现：

- `CapacitySweepRunner(config).run()`。
- config guard。
- build requests once。
- per-capacity replay。
- collect rows。
- 默认使用 stats-only cache event sink。
- 指定 capacity 使用 streaming `CsvCacheEventWriter` 输出明细。
- 返回 `CapacitySweepResult`，不写 `capacity_sweep.csv` / `summary.md`。

测试：

```text
tests/integration/test_step6_capacity_sweep_runner.py
```

覆盖：

- 2 个 capacity。
- 2 个 instance。
- 输出 trace row + instance rows。
- capacity rows deterministic。
- `ddr_hit_tokens == 0`。
- token count invariant。
- cache_events 默认不写明细但 trace row 有 `cache_event_count`。
- 指定 capacity 可写 `capacity_<N>/cache_events.csv`。
- 未指定 capacity 不写 event 明细。

### Batch S3: Report summary

实现：

- 新增 `src/hitfloor/report/sweep.py`。
- 新增 `write_capacity_sweep_report()`。
- `write_capacity_sweep_summary()`。
- `capacity_sweep.csv` writer。

测试：

```text
tests/unit/report/test_sweep_summary.py
```

覆盖：

- summary 包含 capacities。
- summary 包含 trace-level P90。
- summary 明确 Step6 不建 DDR/SSD。
- summary 明确 search 不做 P90 target matching。
- summary 明确 event dump capacities。

### Batch S4: CLI + config + wrapper

实现：

- `src/hitfloor/cli/main.py` 新增 `sweep`。
- 新增 `scripts/run_capacity_sweep.py`。
- 新增 `configs/experiments/step6_capacity_sweep.yaml`。

测试：

```text
tests/integration/test_step6_capacity_sweep_cli.py
```

覆盖：

- package CLI 可运行。
- wrapper 可运行。
- 输出文件存在。
- config 中出现 `targets` 时失败。
- `cache_events: true` 但未指定 `cache_event_capacities` 时失败。
- `parallel_instances: true` 时失败并提示 reserved。

### Batch S5: Final validation and docs

实现：

- 更新 `docs/development_status.md`。
- 更新 `docs/global_memory.md`。
- 更新 `docs/archive/step6/README.md`。

验证：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff format --check src tests scripts
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
```

## 12. Test Matrix

Unit tests:

- request builder config parsing。
- capacity list config guard。
- duplicate capacity failure。
- targets unsupported failure。
- cache event dump capacity guard。
- trace-level aggregation。
- instance-level aggregation。
- percentile。
- output row ordering。
- summary rendering。
- stats-only cache event sink。

Integration tests:

- runner returns `CapacitySweepResult` without writing `capacity_sweep.csv` and `summary.md`。
- report/export writes `capacity_sweep.csv` and `summary.md`。
- package CLI works。
- wrapper works。
- selected capacity event dump works。
- existing Step5 `batch_aware_hbm_lru` behavior unchanged。

Regression:

- full pytest。
- Step5 config E2E。

## 13. 已确认的保守边界

### 13.1 `output.cache_events: true`

第一版支持 `output.cache_events: true`，但必须只对指定 capacity 开启 event dump。

合法配置：

```yaml
output:
  cache_events: true
  cache_event_capacities: [512]
```

输出：

```text
reports/step6_capacity_sweep/capacity_512/cache_events.csv
```

非法配置：

- `cache_events: true` 但 `cache_event_capacities` 为空。
- `cache_event_capacities` 包含未参与 sweep 的 capacity。
- `cache_events: false` 但 `cache_event_capacities` 非空。

原因：

- 当前产品目标明确 sweep 默认关闭明细。
- 指定 capacity event dump 保留调试能力。
- 避免 capacity sweep 默认生成大量事件文件。

### 13.2 `parallel_instances`

第一版不实现并行 replay。

如果 config 出现：

```yaml
sweep:
  parallel_instances: true
```

建议直接失败：

```text
parallel_instances is reserved but not implemented in Step6 v1
```

原因：

- 不影响核心 capacity sweep。
- 并行 replay 需要单独设计 deterministic merge。
- 单线程 sweep 稳定后，再新增 `ParallelCapacitySweepRunner` 或在 runner 内新增显式 execution backend。

### 13.3 `save_replay_details`

建议第一版不实现。

如果后续需要，可新增：

```text
capacity_512/request_metrics.csv
capacity_512/iteration_metrics.csv
```

但默认不写，避免输出膨胀。

## 14. 验收标准

功能验收：

- `capacity_sweep.csv` long-format schema 稳定。
- 每个 capacity 有 1 行 trace row。
- 每个 capacity 有 N 行 instance rows。
- trace row 和 instance rows 的 token count invariant 成立。
- `ddr_hit_tokens` / `ddr_hit_rate` 字段存在，Step6 恒为 0。
- `summary.md` 生成。
- search 不接受 targets。
- cache events 明细默认关闭。
- 指定 capacity event dump 可生成 `capacity_<N>/cache_events.csv`。
- `capacity_sweep.csv` 的 trace row 记录 replay-level `cache_event_count`，instance row 固定为 0。

质量验收：

- `ExperimentRunner` 不承载 sweep 主逻辑。
- `CapacitySweepRunner` 不写 CSV / Markdown，只返回 typed result。
- CLI 不承载核心业务逻辑。
- report 不重新分析 replay 语义。
- 新功能有 unit + integration tests。
- full pytest / ruff 通过。

## 15. 审批状态

已接受：

1. 新增 `CapacitySweepRunner`，不把 sweep 主逻辑塞进 `ExperimentRunner`。
2. `capacity_sweep.csv` 是 report/export 外围能力，不属于核心 runner。
3. 新增 `experiment/request_builder.py` 复用 request build 逻辑。
4. 只对指定 capacity 开启 event dump。
5. `parallel_instances` 作为后续项；单线程稳定后再新增 `ParallelCapacitySweepRunner` 或 execution backend。
6. `capacity_sweep.csv` 的 trace row 记录 replay-level `cache_event_count`，instance row 固定为 0。
7. Batch S1-S5 的代码开发顺序。

## 16. 实现状态

已完成：

- S1: request builder、stats-only sink、sweep schema 和 aggregation。
- S2: `CapacitySweepRunner` 和指定 capacity event dump。
- S3: `capacity_sweep.csv` / `summary.md` report/export。
- S4: package CLI、script wrapper 和 runnable config。
- S5: 单元测试、集成测试、E2E 验证和文档更新。

验证：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 115 passed
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml: passed
```
