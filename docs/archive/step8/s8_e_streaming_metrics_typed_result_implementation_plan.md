# S8-E 实施方案：Streaming Metrics / Typed Result

状态：已完成代码开发，待用户代码评审。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-E：Streaming Metrics / Typed Result。

前置条件：

- S8-A 已完成 `ScheduledSlice` / `BatchShape` / `ShapeKey` 的 KV load shape 字段。
- S8-B 已完成 `KVLoadLatencyComponent` 与显式 `KVLoadLatencyProfile` schema。
- S8-C 已完成 instance/model resolver 到 `ServingLatencyProfile` 的接入。
- S8-D 已完成 replay integration：DDR hit request 会在首次被 scheduler 选中时产生 KV load latency，HBM-only zero-miss 仍 immediate finish。

## 1. 类型与改动等级

本 Batch 属于核心仿真器。

改动等级：L3。

原因：

- 本 Batch 修改核心 replay typed result 的 schema，包括 request metrics、iteration metrics 和 capacity sweep typed rows。
- 本 Batch 需要在 replay event loop 中记录 request 级 latency attribution，但不改变 replay 状态转移。
- true streaming 主路径会消费这些 typed result，因此本 Batch 会影响大 trace 输出字段和聚合口径。

边界：

- S8-E 只沉淀 Step8 已经生效的 KV load latency 结果。
- S8-E 不重新计算 cache hit，不重新计算 latency，不让 report/export 承担核心分析逻辑。

## 2. 本 Batch 做什么

S8-E 只做 typed metrics 与 streaming 聚合：

1. 在 `BatchAwareRequestMetrics` 中输出 request 级 KV load 形状和 latency attribution：

```text
kv_load_tokens
kv_load_bytes
kv_load_ms
prefill_compute_ms
queue_ms
```

2. 在 `IterationMetrics` 中输出 iteration 级 KV load 形状和 latency component：

```text
kv_load_tokens
kv_load_bytes
kv_load_request_count
kv_load_ms
prefill_compute_ms
queue_ms
```

3. 在 replay event loop 中把 iteration latency component 稳定归因到 request state，供 request typed metrics 使用。

4. 在 streaming metrics accumulator 中聚合 request 级 `kv_load_ms`，并输出到 capacity sweep typed row。

5. 在 `CapacitySweepRow` 中新增 KV load 聚合字段：

```text
total_kv_load_ms
avg_kv_load_ms
p50_kv_load_ms
p90_kv_load_ms
p99_kv_load_ms
```

6. 更新 CSV / summary report，使外围 report 只消费 typed result，不重算 replay 语义。

7. 保持 legacy / HBM-only 场景兼容：新增字段全部输出 0。

## 3. 本 Batch 不做什么

S8-E 不做：

- 不改变 `cached_tokens`。
- 不改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`。
- 不改变 finish-time materialization。
- 不改变 HBM / DDR lookup 顺序。
- 不改变 HBM / DDR eviction。
- 不新增 cache event。
- 不改变 `finish_time` / `ttft_ms` 的计算。
- 不改变 Step8 v1 的 `iteration_duration = compute + kv_load`。
- 不新增 DDR promotion。
- 不新增 load completion event。
- 不新增 load queue / backpressure。
- 不做 compute/load overlap。
- 不做 layerwise、chunkwise 或 request 内多批次 KV load 拆分。
- 不接 Ramulator2 / Mooncake online replay。
- 不修改 tokenizer / chat template / prefix hash。
- 不新增外部 report 的独立分析逻辑。

如果开发中发现必须修改上述内容，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/scheduler/state.py`

职责：

- 保存 request 在 scheduler/replay 中的生命周期状态。

计划修改：

- 新增 request 级 latency attribution 累计字段：

```python
prefill_compute_ms: float = 0.0
kv_load_ms: float = 0.0
queue_ms: float = 0.0
```

- 新增小型 helper：

```python
def record_latency_contribution(
    self,
    *,
    prefill_compute_ms: float,
    kv_load_ms: float,
    queue_ms: float,
) -> None:
    ...
```

职责边界：

- 只记录已经由 replay latency backend 产生的 component。
- 不估算 latency。
- 不修改 `computed_tokens`、`finish_time`、cache lookup 或 materialization。

### 4.2 `src/infertwin/replay/metrics.py`

职责：

- 将 replay state 转成 typed metrics。
- 维护 request / iteration metrics schema。

计划修改：

- 扩展 `BatchAwareRequestMetrics`：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
kv_load_ms: float = 0.0
prefill_compute_ms: float = 0.0
queue_ms: float = 0.0
```

- 扩展 `IterationMetrics`：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
kv_load_request_count: int = 0
kv_load_ms: float = 0.0
prefill_compute_ms: float = 0.0
queue_ms: float = 0.0
```

- 新增 latency component 解析 helper，例如：

```python
@dataclass(frozen=True)
class IterationLatencyBreakdown:
    prefill_compute_ms: float
    kv_load_ms: float
    queue_ms: float


def latency_breakdown_from_result(latency: LatencyResult) -> IterationLatencyBreakdown:
    ...
```

解析规则：

```text
prefill_compute_ms:
  优先使用 latency.details["ttft_ms"]
  如果不存在，兼容 legacy backend，使用 latency.duration_ms

kv_load_ms:
  使用 latency.details["kv_load_ms"]，缺失时为 0

queue_ms:
  使用 latency.details["queue_ms"]，缺失时为 0
```

- 新增 request attribution helper，例如：

```python
def split_iteration_latency_contributions(
    *,
    shape: BatchShape,
    latency: LatencyResult,
) -> dict[str, IterationLatencyBreakdown]:
    ...
```

归因口径：

```text
prefill_compute_ms:
  按本轮 scheduled_prefill_tokens 占比分摊。
  load-only slice 的 prefill_compute_ms 为 0。

kv_load_ms:
  如果本轮 total kv_load_bytes > 0，按 kv_load_bytes 占比分摊。
  否则如果 total kv_load_tokens > 0，按 kv_load_tokens 占比分摊。
  否则为 0。

queue_ms:
  当前 Step8 v1 没有真实 queue latency，默认通常为 0。
  如果 future backend 返回非 0 queue_ms，本 Batch 建议按本轮 slice 数均分。
```

不变量：

```text
sum(request.prefill_compute_ms contribution) == iteration.prefill_compute_ms
sum(request.kv_load_ms contribution) == iteration.kv_load_ms
sum(request.queue_ms contribution) == iteration.queue_ms
```

在浮点误差下，测试使用近似断言。

### 4.3 `src/infertwin/replay/event_loop.py`

职责：

- 执行 batch-aware replay event loop。
- 调用 scheduler、latency backend、completion/materialization。

计划修改：

- 在每个 iteration 得到 `BatchShape` 和 `LatencyResult` 后，调用 `split_iteration_latency_contributions(...)`。
- 对本轮每个 scheduled request 的 `RequestState.record_latency_contribution(...)` 写入 contribution。
- `IterationMetrics` 继续由 `build_iteration_metrics(...)` 构造，字段来自 `BatchShape` 和 `LatencyResult`。

边界：

- 不改变 event 时间推进。
- 不改变 request finish 条件。
- 不改变 heap/event 顺序。
- 不改变 cache materialization timing。

### 4.4 `src/infertwin/streaming/metrics.py`

职责：

- true streaming replay 的 scope 级指标聚合。

计划修改：

- `_ScopeAccumulator` 新增：

```python
total_kv_load_ms: float
kv_load_values: list[float]
```

- `add_request(...)` 从 `BatchAwareRequestMetrics.kv_load_ms` 聚合。
- `finish(...)` 输出新增 capacity row 字段。

true streaming 边界：

- 不缓存 request 对象。
- 不缓存 cache events。
- 新增 `kv_load_values` 与现有 `ttft_values` 一样，只用于 percentile。
- 如果未来超大 trace percentile 内存压力明显，应单独引入 streaming percentile sketch；S8-E 不在本 Batch 中改变 percentile 算法。

### 4.5 `src/infertwin/experiment/sweep.py`

职责：

- 定义 capacity sweep typed row。
- 小 trace / non-streaming sweep 的 typed row 构造。

计划修改：

- 扩展 `CapacitySweepRow`：

```python
total_kv_load_ms: float = 0.0
avg_kv_load_ms: float = 0.0
p50_kv_load_ms: float = 0.0
p90_kv_load_ms: float = 0.0
p99_kv_load_ms: float = 0.0
```

- 更新 `_aggregate_row(...)` / `build_capacity_rows(...)`，只从 request typed metrics 聚合，不从 iteration metrics 反推 request 指标。

原因：

- capacity sweep 是外围 report/export 能力，但它消费核心 typed result。
- report 不应该重新分析 replay。

### 4.6 `src/infertwin/report/sweep.py`

职责：

- 将 `CapacitySweepRow` 渲染为 CSV 和 summary markdown。

计划修改：

- CSV 通过 dataclass 字段自动包含新增列。
- summary markdown 增加 KV load 聚合说明。
- 原 Step7 口径中“DDR KV load latency not modeled”的描述更新为：

```text
KV load latency is modeled when configured by Step8 KVLoadLatencyProfile.
HBM-only or zero KV load profile produces zero KV load metrics.
```

边界：

- 不从 cache events 或 raw request 重新计算 KV load。
- 不修改 capacity sweep 的 cache hit 统计口径。

### 4.7 `src/infertwin/report/summary.py`

职责：

- 小 trace `simulate` / batch-aware report summary 渲染。

计划修改：

- 在 summary 中增加 KV load latency total / p90 / p99。
- 更新 Step7 时代“KV load time not modeled”的文字。

边界：

- 只消费 `ExperimentResult.request_metrics` / `iteration_metrics`。
- 不重算 latency。

### 4.8 测试文件

计划新增或修改：

```text
tests/unit/replay/test_step8_latency_contribution_metrics.py
tests/unit/streaming/test_metrics.py
tests/unit/experiment/test_sweep_metrics.py
tests/unit/report/test_sweep_summary.py
tests/integration/test_step8_streaming_kv_load_e2e.py
```

可选修改：

```text
tests/integration/test_step7_report_metrics_e2e.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

修改原则：

- 如果测试只断言行为，不需要改。
- 如果测试断言 exact CSV/schema/golden，需要加入新增字段的 0 值。
- golden 更新只能反映 typed schema 扩展，不能掩盖 replay 行为变化。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 Request typed result

`BatchAwareRequestMetrics` 新增字段语义：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `kv_load_tokens` | int | 本 request 由于 DDR hit 需要 load 的 accounted token 数 |
| `kv_load_bytes` | int | 本 request 由于 DDR hit 需要 load 的 accounted KV bytes |
| `kv_load_ms` | float | 本 request 被归因到的 KV load latency |
| `prefill_compute_ms` | float | 本 request 被归因到的 prefill compute latency |
| `queue_ms` | float | serving latency profile 返回的 queue component，当前通常为 0 |

说明：

- `queue_ms` 不是 `scheduler_wait_ms`。
- `scheduler_wait_ms = first_scheduled_time - arrival_time`，来自 replay scheduler。
- `queue_ms` 是 latency profile 的 component，Step8 v1 默认不建模真实入口排队。

### 5.2 Iteration typed result

`IterationMetrics` 新增字段语义：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `kv_load_tokens` | int | 本 iteration 聚合后的 KV load tokens |
| `kv_load_bytes` | int | 本 iteration 聚合后的 KV load bytes |
| `kv_load_request_count` | int | 本 iteration 中携带 KV load 的 request slice 数 |
| `kv_load_ms` | float | latency backend 返回的本 iteration KV load latency |
| `prefill_compute_ms` | float | latency backend 返回的本 iteration prefill compute latency |
| `queue_ms` | float | latency backend 返回的本 iteration queue component |

### 5.3 Capacity sweep typed row

`CapacitySweepRow` 新增字段语义：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `total_kv_load_ms` | float | scope 内 request 级 KV load latency 总和 |
| `avg_kv_load_ms` | float | scope 内 request 平均 KV load latency |
| `p50_kv_load_ms` | float | scope 内 request KV load latency P50 |
| `p90_kv_load_ms` | float | scope 内 request KV load latency P90 |
| `p99_kv_load_ms` | float | scope 内 request KV load latency P99 |

不新增字段：

- S8-E v1 不在 capacity sweep row 中新增 `total_prefill_compute_ms`、`p90_prefill_compute_ms` 或 `queue_ms` 聚合字段，避免 report scope 膨胀。
- 如果后续需要完整 latency decomposition 表，应新增独立 report/export，而不是继续堆字段。

## 6. 核心算法逻辑

### 6.1 Iteration component 提取

输入：

```text
BatchShape
LatencyResult
```

输出：

```text
IterationLatencyBreakdown(
  prefill_compute_ms,
  kv_load_ms,
  queue_ms,
)
```

规则：

```text
if latency.details contains "ttft_ms":
  prefill_compute_ms = details["ttft_ms"]
else:
  prefill_compute_ms = latency.duration_ms

kv_load_ms = details.get("kv_load_ms", 0)
queue_ms = details.get("queue_ms", 0)
```

说明：

- 对 `ServingLatencyProfile`，`duration_ms = ttft_ms + kv_load_ms + queue_ms`。
- 对 legacy fitted/formula backend，缺少 component details 时视为纯 prefill compute，保持兼容。

### 6.2 Request 级 latency attribution

对每个 iteration：

1. 从 `BatchShape.slices` 取所有 scheduled slice。
2. 计算：

```text
total_prefill_tokens = sum(slice.scheduled_prefill_tokens)
total_kv_load_bytes = sum(slice.kv_load_bytes)
total_kv_load_tokens = sum(slice.kv_load_tokens)
```

3. 分摊 prefill compute：

```text
slice_prefill_ms =
  iteration_prefill_compute_ms * slice.scheduled_prefill_tokens / total_prefill_tokens
```

如果 `total_prefill_tokens == 0`，所有 slice 的 `slice_prefill_ms = 0`。

4. 分摊 KV load：

```text
if total_kv_load_bytes > 0:
  slice_kv_load_ms = iteration_kv_load_ms * slice.kv_load_bytes / total_kv_load_bytes
elif total_kv_load_tokens > 0:
  slice_kv_load_ms = iteration_kv_load_ms * slice.kv_load_tokens / total_kv_load_tokens
else:
  slice_kv_load_ms = 0
```

5. 分摊 queue component：

```text
if iteration_queue_ms > 0 and batch_size > 0:
  slice_queue_ms = iteration_queue_ms / batch_size
else:
  slice_queue_ms = 0
```

6. 写入对应 `RequestState`：

```text
request_state.record_latency_contribution(...)
```

### 6.3 Streaming 聚合

每条 request 完成后：

```text
scope.total_kv_load_ms += request_metrics.kv_load_ms
scope.kv_load_values.append(request_metrics.kv_load_ms)
```

scope finish 时：

```text
avg_kv_load_ms = total_kv_load_ms / request_count
p50/p90/p99 = percentile(kv_load_values)
```

trace row 和 instance row 都使用同一聚合逻辑。

## 7. 对核心 replay 语义的影响

| 问题 | S8-E 影响 |
| --- | --- |
| 是否改变 `cached_tokens` | 不改变 |
| 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` | 不改变 |
| 是否改变 `finish_time` / `ttft_ms` | 不改变，只输出已产生的 latency component |
| 是否改变 cache event 顺序 | 不改变 |
| 是否改变 materialization timing | 不改变，仍为 finish-time materialization |
| 是否改变实例隔离 | 不改变，每个实例仍独立 replay、独立 latency backend、独立 cache |
| 是否影响 true streaming 大 trace | 影响输出 schema 和 streaming accumulator；不改变 request streaming build / shard replay 语义 |

大 trace 风险：

- 当前已有 `ttft_values` 用于 percentile，S8-E 新增 `kv_load_values` 会增加少量内存。
- 这不是 true streaming 架构破坏，因为不缓存 request / event / block。
- 如果后续 trace 更大且 percentile 内存成为瓶颈，应在 V2 引入 streaming percentile sketch。

## 8. 测试计划

### 8.1 单测

新增 `tests/unit/replay/test_step8_latency_contribution_metrics.py`：

- legacy latency result 无 details 时，`prefill_compute_ms == duration_ms`，`kv_load_ms == 0`。
- `ServingLatencyProfile` details 中的 `ttft_ms` / `kv_load_ms` / `queue_ms` 能正确写入 `IterationMetrics`。
- prefill compute 按 scheduled prefill tokens 分摊。
- KV load 优先按 bytes 分摊。
- bytes 缺失但 tokens 存在时，KV load 按 tokens 分摊。
- load-only iteration 中 `prefill_compute_ms == 0`，`kv_load_ms > 0`。
- 分摊和 iteration component 总和一致。

修改 `tests/unit/streaming/test_metrics.py`：

- `_ScopeAccumulator` 正确聚合 `total_kv_load_ms`、avg、p50/p90/p99。
- trace row 和 instance row 都输出 KV load 字段。
- HBM-only 请求输出 0。

修改 `tests/unit/experiment/test_sweep_metrics.py`：

- `CapacitySweepRow` 从 request metrics 聚合 KV load 字段。
- 不从 iteration metrics 反推 request KV load。

修改 `tests/unit/report/test_sweep_summary.py`：

- summary markdown 展示 KV load 聚合字段。
- Step8 文案不再说 DDR KV load latency 未建模。

### 8.2 集成测试

修改 `tests/integration/test_step8_streaming_kv_load_e2e.py`：

- 合成数据包含：
  - 第一次请求 miss materialize 到 DDR。
  - 第二次请求 DDR hit 产生 KV load。
  - HBM-only zero-miss 请求 KV load 为 0。
- 断言：
  - request metrics 中 `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms` 正确。
  - iteration metrics 中 `kv_load_ms` 正确。
  - capacity sweep row 中 `p90_kv_load_ms` / `total_kv_load_ms` 正确。
  - `ttft_ms` 仍等于 `finish_time - arrival_time`。

### 8.3 小 E2E

使用现有合成 trace 跑 streaming capacity sweep：

```text
capacity A: DDR hit 少，kv_load_ms 低
capacity B: DDR hit 多，kv_load_ms 高
HBM-only path: kv_load_ms 为 0
```

验证输出：

```text
capacity_sweep.csv
request_metrics.csv
iteration_metrics.csv
summary.md
```

### 8.4 Golden 更新

可能需要 golden 更新。

原则：

- 如果 golden 只检查旧字段行为，不更新。
- 如果 golden 检查完整 CSV header / dataclass schema，需要加入新增字段。
- HBM-only 或 legacy path 的新增字段必须为 0。
- golden 更新不能改变旧字段数值。

## 9. 风险与回滚边界

风险 1：request 级 latency attribution 被误解为真实硬件逐 request 计费。

控制：

- 文档和字段说明明确：request 级 `kv_load_ms` 是从 iteration shared-link latency 分摊得到的 report attribution。
- 核心 replay 仍以 iteration duration 推进。

风险 2：`queue_ms` 与 `scheduler_wait_ms` 混淆。

控制：

- `scheduler_wait_ms` 保持 replay scheduler wait。
- `queue_ms` 只表示 latency backend component，Step8 v1 默认通常为 0。

风险 3：report/export 重新计算 replay 语义。

控制：

- report 只能消费 typed result。
- capacity sweep row 从 request metrics 聚合，不从 cache events 或 raw trace 反推。

风险 4：新增字段导致已有 CSV consumer 不兼容。

控制：

- 字段只追加，不删除/重命名旧字段。
- HBM-only path 输出 0。

回滚边界：

- 可回滚 `RequestState` latency attribution 字段、metrics dataclass 字段、streaming accumulator 字段和 report 文案。
- 不涉及 scheduler/cache/materialization 语义回滚。
- 如果开发中需要修改 cache lookup、eviction、materialization 或 latency profile schema，应暂停并重新评审。

## 10. 完成后如何判断可以进入下一个 Batch

S8-E 完成条件：

1. request metrics 能输出 request 级 `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms`。
2. iteration metrics 能输出 iteration 级 `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms`。
3. streaming capacity sweep row 能输出 KV load 聚合字段。
4. HBM-only / legacy backend 的新增字段稳定为 0。
5. DDR hit synthetic E2E 中，KV load latency 已进入 `ttft_ms`，且 typed metrics 与 latency backend details 一致。
6. report/export 只消费 typed result，不重算 replay。
7. 相关单测、集成测试、小 E2E 全部通过。
8. 未修改 cache lookup / materialization / eviction 语义。

## 11. 执行记录

本轮已完成：

- 扩展 `BatchAwareRequestMetrics`，新增 request 级 `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms`、`prefill_compute_ms`、`queue_ms`。
- 扩展 `IterationMetrics`，新增 iteration 级 `kv_load_tokens`、`kv_load_bytes`、`kv_load_request_count`、`kv_load_ms`、`prefill_compute_ms`、`queue_ms`。
- 新增 latency component extraction 与 deterministic request attribution helper。
- 在 replay event loop 中记录 request 级 latency contribution。
- 扩展 streaming metrics accumulator 与 `CapacitySweepRow` KV load 聚合字段。
- 更新 capacity sweep summary 与 batch-aware summary 的 Step8 KV load 文案。
- 增加 / 更新 S8-E 相关单测、streaming E2E、report 测试。

本轮没有做：

- 没有改变 `cached_tokens`、`hbm_hit_tokens`、`ddr_hit_tokens`、`miss_tokens`。
- 没有改变 `finish_time` / `ttft_ms` 计算语义。
- 没有改变 cache lookup / materialization / eviction。
- 没有新增 cache event。
- 没有引入 DDR promotion、load queue、backpressure、overlap、layerwise 或 chunkwise load。
- 没有接入 Ramulator2 / Mooncake online replay。

验证结果：

```text
23 passed:
tests/unit/replay/test_step8_latency_contribution_metrics.py
tests/unit/replay/test_step8_kv_load_replay.py
tests/unit/streaming/test_metrics.py
tests/unit/experiment/test_sweep_metrics.py
tests/unit/report/test_sweep_summary.py
tests/integration/test_step8_streaming_kv_load_e2e.py
tests/integration/test_batch_d_runner.py
tests/integration/test_step7_report_metrics_e2e.py

11 passed:
tests/integration/test_true_streaming_capacity_sweep_runner.py
tests/integration/test_step6_capacity_sweep_cli.py
tests/integration/test_step6_capacity_sweep_runner.py

ruff check: passed
git diff --check: passed
```

能否进入下一个 Batch：

- 从 S8-E 自身看，已满足进入后续 Step8 batch 的技术条件。
- 进入下一 batch 前仍建议用户先 review request 级 `kv_load_ms` attribution 口径，尤其确认“按 bytes 优先、tokens fallback”的报表归因不会被误解为真实硬件逐 request 计费。

## 12. 已审批的决定

用户已审批后进入代码开发：

1. 是否接受 S8-E 属于核心仿真器，改动等级为 L3，但不改变 replay 状态转移。
2. 是否接受 request 级 `kv_load_ms` 是 iteration shared-link latency 的 deterministic attribution，而不是独立硬件测量值。
3. 是否接受 `prefill_compute_ms` 按 scheduled prefill tokens 分摊。
4. 是否接受 `kv_load_ms` 优先按 bytes 分摊，bytes 缺失时按 tokens 分摊。
5. 是否接受 future non-zero `queue_ms` 在 request 级按 slice 数均分；当前 Step8 v1 通常为 0。
6. 是否接受 `queue_ms` 与 `scheduler_wait_ms` 同时存在，并在文档中明确二者不同。
7. 是否接受 `CapacitySweepRow` 新增 KV load 聚合字段，并让 `capacity_sweep.csv` 追加这些列。
8. 是否接受 S8-E 不新增 cache event，也不修改 cache event 顺序。
9. 是否接受 S8-E 不新增 prefill/queue 的 capacity sweep 聚合字段，避免 report 字段膨胀。
10. 是否接受如果 exact CSV/golden 测试受新增字段影响，只做 schema 追加和 0 值 golden 更新。
