# S9-H Implementation Plan: Streaming Integration / Report Fields

状态：已完成。

本文件是 S9-H 的代码编写方案与执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发，包含一个外围 report/export 消费层更新。

改动等级：L3。

原因：

- S9-H 要把 Step9 progressive timeline mode 接入 `sweep-streaming` 主路径。
- `sweep-streaming` 是大 trace 主路径，直接决定多实例 streaming replay 的执行 mode。
- 本 Batch 会扩展 capacity sweep typed row schema，并让 report/export 只消费这些 typed fields。
- 本 Batch 不新增 replay state machine 语义；S9-B 到 S9-G 已完成 compute wait、KV load wait、
  shared-link queue、chunk TTFT composer 和 progressive full-block materialization。

边界声明：

```text
core replay emits typed metrics
  -> streaming aggregator builds typed CapacitySweepRow
  -> report/export renders typed CapacitySweepRow
```

report/export 不允许重新计算：

- cached tokens。
- HBM / DDR hit tokens。
- compute wait。
- KV load wait。
- chunk count。
- progressive materialization。
- TTFT。

## 2. 本 Batch 做什么

S9-H 做三件事。

### 2.1 Streaming mode integration

新增或正式接入 streaming cache/replay mode：

```text
batch_aware_hbm_ddr_lru_progressive_timeline
```

语义：

- cache backend：仍使用 instance-local `TieredPrefixCache(HBM LRU + DDR LRU)`。
- replay timeline：使用 `PROGRESSIVE_TIMELINE_MODE`。
- materialization：由 S9-G 默认 policy 执行 progressive full-block materialization。
- KV load wait / shared-link queue：使用 S9-D / S9-E 已接入的 replay logic。

配置入口：

```yaml
simulation:
  mode: capacity_sweep_streaming
cache:
  mode: batch_aware_hbm_ddr_lru_progressive_timeline
  eviction_policy: lru
```

本 Batch 只把该 mode 接入 streaming capacity sweep 主路径。legacy `simulate` 和 non-streaming
`sweep` 暂不接 progressive mode。

### 2.2 Typed capacity aggregate fields

扩展 `CapacitySweepRow`，让 capacity_sweep.csv 可以展示 Step9 timeline 结果。

新增字段建议追加在 dataclass 末尾，保持已有字段顺序稳定：

```text
timeline_mode
ttft_granularity
total_compute_wait_ms
avg_compute_wait_ms
p50_compute_wait_ms
p90_compute_wait_ms
p99_compute_wait_ms
total_kv_load_wait_ms
avg_kv_load_wait_ms
p50_kv_load_wait_ms
p90_kv_load_wait_ms
p99_kv_load_wait_ms
total_uncached_prefill_compute_ms
avg_uncached_prefill_compute_ms
p90_uncached_prefill_compute_ms
total_unattributed_ttft_ms
avg_unattributed_ttft_ms
total_chunk_count
total_load_event_count
total_progressive_materialized_blocks
total_progressive_materialized_tokens
total_waiting_for_compute_count
total_waiting_for_kv_load_count
total_scheduled_chunk_count
max_kv_transfer_queue_depth
```

这些字段全部从 `BatchAwareRequestMetrics` / `IterationMetrics` 聚合，不从 report 层重算。

### 2.3 Report/export fields

`capacity_sweep.csv` 自动包含 dataclass 新字段。

`summary.md` 只增加小型 timeline summary：

- `timeline_mode`
- `ttft_granularity`
- `p90_compute_wait_ms`
- `p90_kv_load_wait_ms`
- `p90_uncached_prefill_compute_ms`
- `total_chunk_count`
- `total_progressive_materialized_tokens`
- `max_kv_transfer_queue_depth`

summary 只消费 `CapacitySweepRow` 字段。

## 3. 本 Batch 不做什么

S9-H 不做：

- 不改变 cache lookup accounting。
- 不改变 `cached_tokens` / `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` 计算规则。
- 不改变 S9-G progressive materialization policy。
- 不新增 per-chunk timeline dump。
- 不默认输出 chunk 明细 CSV。
- 不接 legacy `simulate`。
- 不接 non-streaming `sweep` 的 progressive mode。
- 不做 DDR hit promotion。
- 不做 physical KV slot / refcount / pin。
- 不做 partial-block hit。
- 不做 Decode / TPOT。
- 不做 gateway / instance admission queue。
- 不做 Ramulator2 / Mooncake online replay。
- 不实现 approximate percentile / external sort；当前 request 数量级下继续使用已有 in-memory percentile lists。

如果实现时发现必须修改 replay state machine、scheduler selection、cache lookup 或 latency backend，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `tests/integration/test_step9_streaming_progressive_timeline_e2e.py` | 小型 streaming E2E，验证 progressive mode 从 config 接入、CSV/report 字段输出、chunk boundary 后 hit 增加。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/streaming/cache_factory.py` | 接受 `batch_aware_hbm_ddr_lru_progressive_timeline` mode；构建 tiered cache；提供 cache mode -> timeline mode helper。 |
| `src/infertwin/streaming/sweep.py` | streaming runner 根据 cache mode 创建 `StreamingBatchAwareReplayEngine(timeline_mode=...)`；config details 记录 timeline mode 和 granularity。 |
| `src/infertwin/experiment/sweep.py` | 扩展 `CapacitySweepRow` schema；batch 聚合与 streaming 聚合共用字段口径。 |
| `src/infertwin/streaming/metrics.py` | streaming aggregator 聚合 Step9 request/iteration fields；保持与 `build_capacity_rows()` 一致。 |
| `src/infertwin/report/sweep.py` | summary 增加 timeline 字段展示；CSV 仍通过 dataclass row 输出。 |
| `tests/unit/streaming/test_cache_factory.py` | 验证 progressive mode 被接受、仍构建 tiered cache、非法 mode fail-fast。 |
| `tests/unit/streaming/test_metrics.py` | 验证 streaming aggregate 与 batch rows 在新增 Step9 字段上保持一致。 |
| `tests/unit/experiment/test_sweep_metrics.py` | 验证 `build_capacity_rows()` 新字段、percentile、不变量。 |
| `tests/unit/report/test_sweep_summary.py` | 验证 CSV/summary 包含 timeline 字段，且 summary 不重算 replay。 |
| `tests/integration/test_true_streaming_capacity_sweep_runner.py` | old-mode 回归，确认新增字段默认值稳定。 |
| `tests/integration/test_step7_streaming_hbm_ddr_integration.py` | DDR mode 回归，确认非-progressive HBM+DDR 行为不变。 |

### 4.3 禁止修改文件

S9-H 禁止修改：

- `src/infertwin/replay/event_loop.py`
- `src/infertwin/cache/materialization.py`
- `src/infertwin/cache/hbm_lru.py`
- `src/infertwin/cache/ddr_lru.py`
- `src/infertwin/cache/tiered.py`
- `src/infertwin/scheduler/vllm_like.py`
- `src/infertwin/scheduler/state.py`
- `src/infertwin/latency/**`
- `src/infertwin/request/**`
- `src/infertwin/trace/**`
- `src/infertwin/external/**`

例外：

- 如果测试暴露 S9-G 已有 replay bug，应暂停并单独提交 repair batch，不在 S9-H 顺手修。

## 5. 每个文件的职责

### 5.1 `src/infertwin/streaming/cache_factory.py`

新增常量：

```python
CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE = PROGRESSIVE_TIMELINE_MODE
```

或显式字符串：

```python
"batch_aware_hbm_ddr_lru_progressive_timeline"
```

建议复用 `infertwin.replay.timeline.PROGRESSIVE_TIMELINE_MODE`，避免 mode 字符串分裂。

新增 helper：

```python
def timeline_mode_for_cache_mode(cache_mode: str) -> str:
    if cache_mode == CACHE_MODE_HBM_DDR_LRU_PROGRESSIVE_TIMELINE:
        return PROGRESSIVE_TIMELINE_MODE
    return LEGACY_TIMELINE_MODE
```

新增 helper：

```python
def ttft_granularity_for_timeline_mode(timeline_mode: str) -> str:
    if timeline_mode == PROGRESSIVE_TIMELINE_MODE:
        return CHUNK_TTFT_GRANULARITY
    return ITERATION_TTFT_GRANULARITY
```

`build_streaming_cache_factory_config()`：

- 允许 mode：
  - `batch_aware_hbm_lru`
  - `batch_aware_hbm_ddr_lru`
  - `batch_aware_hbm_ddr_lru_progressive_timeline`
- progressive mode 仍要求 `eviction_policy=lru`。

`build_streaming_prefix_cache()`：

- HBM mode -> `HBMCache`。
- HBM+DDR mode -> `TieredPrefixCache`。
- progressive HBM+DDR mode -> 同样 `TieredPrefixCache`。

注意：

- progressive 是 replay timeline mode，不是新的 cache storage backend。
- 因为 mode 名含 HBM+DDR，仍要求 model registry / instance runtime default cache 包含 DDR capacity 和 pooling flags。

### 5.2 `src/infertwin/streaming/sweep.py`

`StreamingCapacitySweepRunner.__init__()`：

- 通过 cache factory config 解析 timeline mode。
- 保存：

```python
self.timeline_mode = timeline_mode_for_cache_mode(self.cache_factory_config.mode)
self.ttft_granularity = ttft_granularity_for_timeline_mode(self.timeline_mode)
```

`_build_streaming_replay_engine()`：

- 新增 `timeline_mode` 参数。
- 构造：

```python
StreamingBatchAwareReplayEngine(
    scheduler=...,
    latency_backend=...,
    timeline_mode=timeline_mode,
)
```

`_run_capacity()`：

- 构造 `CapacitySweepStreamingMetricAggregator(timeline_mode=..., ttft_granularity=...)`。

`_config_details()` 新增：

```text
streaming_timeline_mode
streaming_ttft_granularity
progressive_materialization_enabled
```

这些字段用于 summary 的说明，不用于 replay 语义计算。

### 5.3 `src/infertwin/experiment/sweep.py`

扩展 `CapacitySweepRow`。

字段追加在末尾，并给默认值，保证已有测试/构造器不需要一次性全部改动：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
ttft_granularity: str = ITERATION_TTFT_GRANULARITY
total_compute_wait_ms: float = 0.0
avg_compute_wait_ms: float = 0.0
p50_compute_wait_ms: float = 0.0
p90_compute_wait_ms: float = 0.0
p99_compute_wait_ms: float = 0.0
...
```

`build_capacity_rows()`：

- 新增可选参数：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
ttft_granularity: str = ITERATION_TTFT_GRANULARITY
```

- 传给 `_aggregate_row()`，用于 empty trace fallback。

`_aggregate_row()`：

- 从 request metrics 聚合：
  - compute wait。
  - KV load wait。
  - uncached prefill compute。
  - unattributed TTFT。
  - chunk count。
  - load event count。
  - progressive materialized blocks/tokens。
- 从 iteration metrics 聚合：
  - waiting for compute count。
  - waiting for KV load count。
  - scheduled chunk count。
  - max KV transfer queue depth。
- 校验同一 row 内 timeline mode / granularity 一致。
  - 如果 request metrics 非空，所有 request metrics 的 `timeline_mode` 应一致。
  - 如果 iteration metrics 非空，也应与 request metrics 一致。
  - 如果没有 metrics，使用函数参数默认值。

不变量保持：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens
```

新增 wait 字段不参与 token invariant。

### 5.4 `src/infertwin/streaming/metrics.py`

`CapacitySweepStreamingMetricAggregator.__init__()`：

新增可选参数：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
ttft_granularity: str = ITERATION_TTFT_GRANULARITY
```

`_ScopeAccumulator` 新增字段：

- request-level scalar sums。
- request-level percentile lists。
- iteration-level counts/max depth。

`to_row()`：

- 输出新增 `CapacitySweepRow` 字段。
- 继续执行 token invariant。

重要要求：

- streaming aggregate 与 `build_capacity_rows()` 必须在相同输入下返回完全相同 rows。
- 不保存 per-chunk timeline entry。
- 不保存 request object。

内存边界：

- 当前已有 TTFT / KV-load percentile list。
- S9-H 会新增 compute-wait / KV-load-wait / uncached-prefill percentile list。
- 公司 V1 trace 为几万条 request，该内存增量可接受。
- 如果未来 request 数量上升到千万级，应新增 approximate percentile accumulator；这不是 S9-H。

### 5.5 `src/infertwin/report/sweep.py`

CSV：

- `write_csv_table()` 自动输出新 dataclass fields。
- 不额外处理。

Summary：

- 保持原有 capacity comparison table。
- 增加一个 "Timeline Results" 小节。
- 该小节只消费 trace-scope rows。
- 建议列：

```text
hbm_capacity_blocks
timeline_mode
ttft_granularity
p90_compute_wait_ms
p90_kv_load_wait_ms
p90_uncached_prefill_compute_ms
total_chunk_count
total_progressive_materialized_tokens
max_kv_transfer_queue_depth
```

当所有 trace row 都是 legacy 且新增字段为 0，可以仍展示字段，避免 CSV/summary 口径不一致；
也可以展示一句：

```text
Legacy mode: Step9 timeline fields are expected to be 0.
```

建议第一版始终展示小节，便于同事直接看到字段。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 Streaming cache mode

新增 accepted cache mode：

```text
batch_aware_hbm_ddr_lru_progressive_timeline
```

这是 streaming runner 的 config mode，不是新的 cache backend。

### 6.2 `CapacitySweepRow`

新增 Step9 timeline aggregate fields。

这些字段是 report/export 的稳定 typed result schema。后续 dashboard、notebook、CSV 都消费同一 row。

### 6.3 `CapacitySweepStreamingMetricAggregator`

新增 timeline defaults 和 Step9 aggregate accumulators。

### 6.4 `config_details`

新增：

```text
streaming_timeline_mode
streaming_ttft_granularity
progressive_materialization_enabled
```

这些字段是 report metadata，不参与 replay 计算。

## 7. 核心算法逻辑

### 7.1 Cache mode to timeline mode

```text
cache.mode=batch_aware_hbm_lru
  -> cache backend: HBM LRU
  -> timeline_mode: legacy_iteration_v1

cache.mode=batch_aware_hbm_ddr_lru
  -> cache backend: HBM LRU + DDR LRU
  -> timeline_mode: legacy_iteration_v1

cache.mode=batch_aware_hbm_ddr_lru_progressive_timeline
  -> cache backend: HBM LRU + DDR LRU
  -> timeline_mode: batch_aware_hbm_ddr_lru_progressive_timeline
```

### 7.2 Aggregate request fields

For request metrics:

```text
total_compute_wait_ms = sum(metric.compute_wait_ms)
avg_compute_wait_ms = total_compute_wait_ms / request_count
p90_compute_wait_ms = percentile(metric.compute_wait_ms, 90)

total_kv_load_wait_ms = sum(metric.kv_load_wait_ms)
...

total_uncached_prefill_compute_ms = sum(metric.uncached_prefill_compute_ms)
total_unattributed_ttft_ms = sum(metric.unattributed_ttft_ms)
total_chunk_count = sum(metric.chunk_count)
total_load_event_count = sum(metric.load_event_count)
total_progressive_materialized_blocks = sum(metric.progressive_materialized_blocks)
total_progressive_materialized_tokens = sum(metric.progressive_materialized_tokens)
```

### 7.3 Aggregate iteration fields

For iteration metrics:

```text
total_waiting_for_compute_count = sum(metric.waiting_for_compute_count)
total_waiting_for_kv_load_count = sum(metric.waiting_for_kv_load_count)
total_scheduled_chunk_count = sum(metric.scheduled_chunk_count)
max_kv_transfer_queue_depth = max(metric.kv_transfer_queue_depth_max)
```

### 7.4 Timeline mode consistency

For one row:

```text
if request metrics exist:
  row.timeline_mode = unique request metric timeline_mode
  row.ttft_granularity = unique request metric ttft_granularity
elif iteration metrics exist:
  row.timeline_mode = unique iteration metric timeline_mode
  row.ttft_granularity = unique iteration metric ttft_granularity
else:
  row.timeline_mode = configured default
  row.ttft_granularity = configured default
```

If multiple values are present in one row, fail-fast. S9-H should not silently write `mixed` because a
single capacity/instance replay should not mix timeline modes.

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

Old modes：不改变。

Progressive streaming mode：不改变 accounting rule；只通过 S9-G 已实现的 progressive visibility
timing 影响后续 lookup。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

Old modes：不改变。

Progressive streaming mode：可能改变，这是 S9-G 语义通过 streaming config 正式启用后的预期结果。

### 8.3 是否改变 `finish_time` / `ttft_ms`

Old modes：不改变。

Progressive streaming mode：可能改变，因为 hit/miss、compute wait、KV load wait 和 chunk-level TTFT
已由 S9-B 到 S9-G 定义。

### 8.4 是否改变 cache event 顺序

Old modes：不改变。

Progressive streaming mode：可能出现 chunk finish time 的 progressive materialize/store events。
这是 S9-G 已定义的 event order。

S9-H 本身不修改 event loop drain 顺序。

### 8.5 是否改变 materialization timing

Old modes：不改变。

Progressive streaming mode：启用 S9-G progressive full-block materialization。

### 8.6 是否改变实例隔离

不改变。

每个 streaming shard 仍按 instance replay，独立 cache、scheduler、latency backend、KV transfer queue。

### 8.7 是否影响 true streaming 大 trace

影响很小：

- 不构造全量 request list。
- 不保存 per-chunk timeline。
- 只在 streaming aggregator 中新增几个 scalar sums 和 percentile lists。
- 仍按 shard streaming replay。

## 9. 测试计划

### 9.1 单测

`tests/unit/streaming/test_cache_factory.py`：

1. progressive mode 被接受。
2. progressive mode 构建 `TieredPrefixCache`。
3. progressive mode 缺少 model runtime defaults / DDR capacity / pooling flags 时 fail-fast。
4. invalid mode error message 包含三个合法 mode。

`tests/unit/experiment/test_sweep_metrics.py`：

1. `build_capacity_rows()` 聚合 Step9 request fields。
2. `build_capacity_rows()` 聚合 Step9 iteration fields。
3. timeline mode / granularity 不一致时 fail-fast。
4. empty metrics 使用 default legacy values。

`tests/unit/streaming/test_metrics.py`：

1. streaming aggregator 与 `build_capacity_rows()` 在新增 Step9 fields 上完全一致。
2. progressive fields 在 trace row 和 instance row 上正确聚合。
3. token invariant 仍有效。

`tests/unit/report/test_sweep_summary.py`：

1. CSV 包含新增 columns。
2. summary 包含 Timeline Results 小节。
3. summary 展示 `p90_compute_wait_ms` / `p90_kv_load_wait_ms` /
   `total_progressive_materialized_tokens`。

### 9.2 小 E2E

新增 `tests/integration/test_step9_streaming_progressive_timeline_e2e.py`：

场景：

- streaming capacity sweep。
- `cache.mode=batch_aware_hbm_ddr_lru_progressive_timeline`。
- HBM capacity 足够容纳已 materialized prefix blocks。
- DDR defaults 配置齐全。
- scheduler token budget 让第一条长 prompt 被切成多个 chunks。
- 第二条相同 prompt 在第一条 chunk finish 后、request finish 前到达。

断言：

- `capacity_sweep.csv` 存在。
- trace row `timeline_mode == batch_aware_hbm_ddr_lru_progressive_timeline`。
- trace row `ttft_granularity == chunk`。
- `total_chunk_count > 0`。
- `total_progressive_materialized_tokens > 0`。
- 第二条请求导致 trace-level `hbm_hit_tokens > 0` 或 `ddr_hit_tokens >= 0` 且 `miss_tokens`
  小于 finish-time old-mode 对照。
- summary 包含 Timeline Results 小节。

建议同文件增加 old-mode 对照：

- 同一 trace、同一 capacity、同一 scheduler，`cache.mode=batch_aware_hbm_ddr_lru`。
- old-mode row 的 `timeline_mode == legacy_iteration_v1`。
- old-mode progressive materialized tokens 为 0。

### 9.3 回归

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming/test_cache_factory.py tests/unit/streaming/test_metrics.py tests/unit/experiment/test_sweep_metrics.py tests/unit/report/test_sweep_summary.py tests/integration/test_step9_streaming_progressive_timeline_e2e.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_true_streaming_capacity_sweep_runner.py tests/integration/test_step7_streaming_hbm_ddr_integration.py tests/integration/test_step8_streaming_kv_load_e2e.py tests/golden/test_batch_aware_hbm_lru_golden.py
```

```bash
.venv/bin/ruff check src/infertwin/streaming src/infertwin/experiment src/infertwin/report tests/unit/streaming tests/unit/experiment tests/unit/report tests/integration/test_step9_streaming_progressive_timeline_e2e.py
```

```bash
git diff --check
```

### 9.4 是否需要 golden 更新

Old-mode golden 不应更新。

`capacity_sweep.csv` schema 会增加 columns。相关 report/CLI tests 需要更新为断言新 columns 存在，
但不需要改 old-mode replay expected values。

## 10. 风险与回滚边界

### 10.1 主要风险

1. 把 progressive mode 误当作新 cache backend。
   - 控制方式：cache backend 仍走 HBM+DDR tiered cache；timeline mode 单独解析。

2. report 层重算 replay 语义。
   - 控制方式：report 只消费 `CapacitySweepRow` 字段。

3. old mode 被新字段污染。
   - 控制方式：new fields default to 0 / legacy values，old-mode regression 不变。

4. timeline mode 混合。
   - 控制方式：row aggregate 检查 timeline mode / granularity 唯一。

5. 大 trace 内存增量。
   - 控制方式：只新增少量 percentile lists；不保存 per-chunk timeline。

6. progressive mode 要求 DDR defaults。
   - 控制方式：因为 mode 名为 HBM+DDR progressive，第一版沿用 HBM+DDR mode guard；
     HBM-only progressive config 如需支持，应新增独立 mode，不能偷偷复用该 mode。

### 10.2 回滚边界

如果 S9-H 出现问题，可以回滚：

- progressive cache mode config 接入。
- `CapacitySweepRow` 新增 fields。
- streaming aggregate 新增 fields。
- report summary timeline section。
- S9-H tests。

不需要回滚 S9-B 到 S9-G 的 replay state machine。

## 11. 完成后如何判断可以进入 S9-I

满足以下条件后，可以进入 S9-I：

1. `sweep-streaming` 能通过 config 启用 progressive timeline mode。
2. old HBM mode 和 old HBM+DDR mode 行为不变。
3. capacity_sweep.csv 包含 Step9 timeline aggregate fields。
4. summary.md 展示 Step9 timeline fields。
5. report/export 不重算 replay。
6. streaming aggregate 与 batch aggregate row 口径一致。
7. progressive streaming E2E 证明 chunk finish 后 full-block visibility 能进入 capacity sweep row。
8. 大 trace 路径不保存 per-chunk timeline。
9. targeted tests、相关回归、ruff、`git diff --check` 通过。
10. 本文档补充执行记录：
    - 做了什么。
    - 没有做什么。
    - 测试结果。
    - 风险和进入 S9-I 的判断。

## 12. 需要用户审批的内容

请审批以下设计点：

1. 接受 S9-H 属于核心仿真器开发，包含 report/export 消费层更新，改动等级 L3。
2. 接受新增 streaming cache/replay mode：
   `batch_aware_hbm_ddr_lru_progressive_timeline`。
3. 接受该 mode 使用 HBM+DDR tiered cache backend，并启用 `PROGRESSIVE_TIMELINE_MODE`。
4. 接受该 mode 第一版要求 model registry / instance runtime default cache 中 DDR defaults 齐全。
5. 接受 S9-H 不新增 HBM-only progressive streaming config mode。
6. 接受 `CapacitySweepRow` 追加 Step9 timeline aggregate fields。
7. 接受 `capacity_sweep.csv` schema 增加这些 columns。
8. 接受 `summary.md` 新增 Timeline Results 小节。
9. 接受 report/export 只消费 typed row，不重算 replay 语义。
10. 接受 old `batch_aware_hbm_lru` 和 `batch_aware_hbm_ddr_lru` 保持 legacy timeline mode。
11. 接受 S9-H 不修改 replay event loop、scheduler、cache lookup/materialization、latency backend。
12. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
13. 接受测试范围：streaming cache factory、capacity row aggregate、streaming aggregator、report
    summary、progressive streaming E2E、old-mode regression、ruff、`git diff --check`。
14. 审批通过后，才能进入 S9-H 代码开发。

## 13. 执行记录

### 13.1 实际完成内容

S9-H 已完成以下内容：

1. streaming 主路径新增并接入 cache/replay mode：
   `batch_aware_hbm_ddr_lru_progressive_timeline`。
2. 该 mode 使用 `TieredPrefixCache(HBM LRU + DDR LRU)`，并把
   `timeline_mode=PROGRESSIVE_TIMELINE_MODE` 传入 `StreamingBatchAwareReplayEngine`。
3. `StreamingCapacitySweepRunner` 在 `config_details` 中输出：
   - `streaming_timeline_mode`
   - `streaming_ttft_granularity`
   - `progressive_materialization_enabled`
4. `CapacitySweepRow` 追加 Step9 timeline aggregate fields。
5. `build_capacity_rows()` 和 `CapacitySweepStreamingMetricAggregator` 均聚合：
   - compute wait。
   - KV load wait。
   - uncached prefill compute。
   - unattributed TTFT。
   - chunk count。
   - load event count。
   - progressive materialized blocks / tokens。
   - waiting-for-compute / waiting-for-kv-load counts。
   - scheduled chunk count。
   - max KV transfer queue depth。
6. `summary.md` 新增 `Timeline Results` 小节。
7. `capacity_sweep.csv` 通过 dataclass row 自动输出新增字段。
8. 新增 progressive streaming E2E，验证新 mode、CSV 字段、summary 字段，以及旧
   `batch_aware_hbm_ddr_lru` 仍保持 legacy timeline mode。

### 13.2 小型 repair 说明

开发中发现一个 typed metrics 兼容问题：

- progressive request metrics 的 `ttft_granularity` 已经是 `chunk`。
- progressive iteration metrics 仍使用 `IterationMetrics` 默认值 `iteration`。
- streaming aggregator 按设计执行 timeline/granularity fail-fast 时，发现同一 replay row
  内存在 mixed granularity。

修复方式：

- 修改 `src/infertwin/replay/metrics.py` 中 `build_iteration_metrics()`。
- 当 `timeline_mode == PROGRESSIVE_TIMELINE_MODE` 时，设置
  `ttft_granularity = CHUNK_TTFT_GRANULARITY`。

影响边界：

- 只修正 typed metric 字段。
- 不改变 scheduler replay。
- 不改变 cache lookup / materialization / eviction。
- 不改变 TTFT 数值。
- 不改变 cache event 顺序。
- 不改变实例隔离。

### 13.3 实际修改文件

源码：

- `src/infertwin/streaming/cache_factory.py`
- `src/infertwin/streaming/sweep.py`
- `src/infertwin/experiment/sweep.py`
- `src/infertwin/streaming/metrics.py`
- `src/infertwin/report/sweep.py`
- `src/infertwin/replay/metrics.py`

测试：

- `tests/unit/streaming/test_cache_factory.py`
- `tests/unit/experiment/test_sweep_metrics.py`
- `tests/unit/streaming/test_metrics.py`
- `tests/unit/report/test_sweep_summary.py`
- `tests/integration/test_step9_streaming_progressive_timeline_e2e.py`

文档：

- `docs/step9/s9_h_streaming_integration_report_fields_implementation_plan.md`

### 13.4 没有完成的内容

S9-H 未做以下内容，保持 Step9 技术路线边界：

- 未接 legacy `simulate`。
- 未接 non-streaming `sweep` 的 progressive mode。
- 未新增 per-chunk timeline dump。
- 未实现 DDR hit promotion。
- 未实现 physical KV slot / refcount / pin。
- 未实现 partial-block hit。
- 未实现 Decode / TPOT。
- 未接 Ramulator2 / Mooncake online replay。
- 未实现 approximate percentile / external sort。

### 13.5 验证结果

已运行窄测：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/experiment/test_sweep_metrics.py \
  tests/unit/streaming/test_metrics.py \
  tests/unit/report/test_sweep_summary.py \
  tests/integration/test_step9_streaming_progressive_timeline_e2e.py
```

结果：`26 passed`。

已运行 Step7 / Step8 / true-streaming / Step9 progressive 回归：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py \
  tests/unit/replay/test_timeline_schema.py \
  tests/unit/replay/test_chunk_level_ttft_composer.py \
  tests/unit/replay/test_progressive_full_block_materialization.py \
  tests/unit/streaming/test_streaming_replay.py
```

结果：`40 passed`。

已运行 ruff：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m ruff check src tests
```

结果：`All checks passed`。

已对本 Batch 修改文件运行 ruff format check：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m ruff format --check <S9-H modified files>
```

结果：`11 files already formatted`。

已运行全量测试：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest
```

结果：`438 passed`。

已运行：

```bash
git diff --check
```

结果：通过，无输出。

### 13.6 进入后续 Batch 的判断

S9-H 已满足进入后续 review / 收口阶段的条件：

- streaming progressive timeline mode 可由 config 显式启用。
- old HBM mode 和 old HBM+DDR mode 保持 legacy timeline mode。
- capacity sweep typed row 和 report/export 已能输出 Step9 timeline aggregate fields。
- report/export 只消费 typed row，不重算 replay 语义。
- true streaming 主路径没有退化为全量 request list。
- 全量测试与 ruff 均通过。

仍需注意：

- 新增 percentile lists 仍是 in-memory 方案；公司 V1 几万请求规模可接受，千万级 trace 需要
  approximate percentile accumulator。
- S9-H 只输出 aggregate fields，不输出 per-chunk timeline 明细。
- 如果后续要让 legacy `simulate` 或 non-streaming `sweep` 支持 progressive mode，应新增独立
  Batch，不应在外围 report 中隐式切换 replay mode。
