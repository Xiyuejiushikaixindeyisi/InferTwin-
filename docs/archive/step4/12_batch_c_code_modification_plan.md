# Batch C Code Modification Plan

## 当前状态

用户已同意 pre-Batch-C code review 结论。本方案已通过用户 review。

当前 Batch C 代码开发已完成，等待用户 code review。

## 冻结核心语义

核心语义已经写入 `README.md` 的 `Core Semantics (Frozen)` 章节。

后续开发必须遵守：

```text
如果核心语义发生变化，不允许直接改旧字段含义。
必须新增 Python 类型、数据结构、adapter 或 interface。
```

当前冻结语义包括：

- `batch_size` 是一个 scheduler iteration 内的 request slice 数。
- `max_num_batched_tokens` 是 token budget，不是 batch size。
- `max_num_seqs` 是组批请求上限，不是业务最大支持并发。
- `BatchShape` 是 scheduler output，不是外部 simulator input。
- `ScheduledSlice` 表示一个请求在一个 iteration 中的 prefill work。
- `cached_prefix_tokens` 来自 prefix cache hit。
- `previous_chunk_tokens` 来自同一请求前序 chunk 已完成 token。
- `computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens`。
- 100% prefix-hit 请求不产生 `ScheduledSlice`，走 zero-miss fast-finish。
- cache lookup 发生在 first-schedule-time，不是 trace arrival。
- materialization 只在 request prefill finish 后可见。
- `ttft_ms = finish_time_ms - arrival_time_ms`。

## 代码修改目标

本轮代码修改目标分两部分：

1. Batch C 前置修正：
   - 修正 `BatchShape` docstring。
   - 为 bounded waiting lookup 提供共享 planning helper，避免 replay engine 复制 scheduler token 选择逻辑。

2. Batch C 本体：
   - 新增 `BatchAwareReplayEngine`。
   - 实现 fixed-routing, per-instance isolated infinite-HBM batch-aware replay。
   - 处理 zero-miss/full-prefix-hit 请求。
   - 实现 first-schedule-time cache lookup。
   - 实现 finish-time materialization。
   - 生成 request metrics 和 iteration metrics。

不接入 runner/report，不实现外部 simulator adapter。

## 修改范围

### 1. Scheduler 层

修改：

```text
src/hitfloor/scheduler/batch_shape.py
src/hitfloor/scheduler/vllm_like.py
```

可能新增：

```text
src/hitfloor/scheduler/planning.py
```

目的：

- 明确 `BatchShape` 是 scheduler output。
- 抽出共享 token selection helper，供 scheduler 和 Batch C bounded lookup helper 共用。

建议 helper：

```text
planned_prefill_tokens(config, request, token_budget) -> int
```

职责：

- 输入已完成 cache lookup 的 `RequestState`。
- 根据 remaining tokens、chunked prefill、token budget 返回本轮可调度 tokens。
- 不修改 request 状态。
- 不访问 cache。
- 不调用 latency backend。

`VllmLikeBatchScheduler._tokens_for_request()` 可改为调用该 helper。

### 2. Replay 层

新增：

```text
src/hitfloor/replay/
  __init__.py
  event_loop.py
  metrics.py
```

#### metrics.py

新增：

```text
BatchAwareReplayResult
BatchAwareRequestMetrics
IterationMetrics
```

字段建议：

```text
BatchAwareReplayResult:
  request_metrics
  iteration_metrics

BatchAwareRequestMetrics:
  request_id
  tenant_id
  instance_uuid
  model
  tokenizer_profile
  arrival_time_ms
  first_scheduled_time_ms
  finish_time_ms
  scheduler_wait_ms
  ttft_ms
  prompt_tokens
  prompt_blocks
  hbm_hit_tokens
  ddr_hit_tokens
  miss_tokens
  effective_hit_rate
  scheduled_iteration_count

IterationMetrics:
  instance_uuid
  iteration_id
  start_time_ms
  finish_time_ms
  duration_ms
  batch_size
  scheduled_prefill_tokens
  scheduled_decode_tokens
  max_query_len
  total_context_tokens
  backend
  shape_key
  memoized
  request_ids
```

#### event_loop.py

新增：

```text
BatchAwareReplayEngine
```

构造参数：

```text
scheduler: VllmLikeBatchScheduler
latency_backend: BatchLatencyBackend
shape_memo: ShapeMemo | None = None
```

主方法：

```text
run(requests: list[SimulationRequest]) -> BatchAwareReplayResult
```

约束：

- 按 `instance_uuid` 分组 replay。
- 每个实例独立 `InfiniteHBMCache`。
- 每个实例独立 iteration counter。
- 不跨实例共享 cache。
- 不修改 Phase1 `InfiniteHBMReplayEngine`。

## Batch C 核心算法

### 1. 单个实例内部 event loop

```text
now_ms = first arrival time
pending = sorted instance requests
waiting = []
running = []
iteration_id = 0

while pending or waiting or running:
    move arrivals with start_time_ms <= now_ms into waiting

    if waiting and/or running:
        lookup running if needed
        fast-finish zero-miss running if any
        bounded lookup waiting frontier
        fast-finish zero-miss waiting requests

    if waiting/running empty:
        now_ms = next pending arrival
        continue

    schedule_result = scheduler.schedule(...)

    if schedule_result.is_empty:
        fail fast with ValueError

    latency = estimate_with_shape_memo(schedule_result.shape)
    finish_ms = now_ms + latency.duration_ms

    apply scheduled slices at finish_ms
    materialize finished request miss blocks at finish_ms
    emit request metrics for finished requests
    emit iteration metrics
    remove finished requests from running
    now_ms = finish_ms
    iteration_id += 1
```

### 2. Bounded waiting lookup helper

目的：

```text
只 lookup scheduler 本轮可能考虑的 waiting 队首请求，不提前 lookup 整个 waiting queue。
```

建议流程：

1. 确保 running 请求已 lookup。
2. 用共享 planning helper 预估 running 将消耗的 token budget 和 seq slots。
3. 从 waiting 队首开始逐个 lookup。
4. 如果 lookup 后 `miss_tokens == 0`，立即 zero-miss fast-finish，不消耗 token budget 和 seq slot，然后继续看下一个 waiting 队首。
5. 如果 lookup 后 request 可被本轮调度，则扣减预估 token budget 和 seq slot，继续看下一个队首。
6. 如果队首无法调度，停止；不能越过队首 lookup 后续请求。

该 helper 不调用 latency backend，不 materialize 非当前请求。

### 3. Zero-miss fast-finish

触发条件：

```text
state.cache_lookup_done
state.remaining_prefill_tokens() == 0
```

行为：

```text
first_scheduled_time_ms = now_ms
finish_time_ms = now_ms
scheduler_wait_ms = now_ms - arrival_time_ms
ttft_ms = finish_time_ms - arrival_time_ms
scheduled_iteration_count = 0
no ScheduledSlice
no miss block materialization
emit request metrics
```

说明：

- Formula backend 阶段 zero-miss compute duration 为 0。
- 后续 HBM/DDR load backend 可新增数据结构或路径来表达 KV load latency。

### 4. Cache lookup timing

lookup 发生在：

```text
request first becomes eligible to be considered by scheduler
```

不是：

```text
trace arrival time
```

lookup 结果只记录一次，不重复 lookup。

### 5. Materialization timing

request 只有在：

```text
num_computed_tokens == prompt_tokens
```

时 finish。

finish 后：

```text
cache.materialize(miss_blocks, now_ms=finish_time_ms)
```

同一 iteration 内不能边调度边 materialize。

## 测试计划

新增：

```text
tests/unit/replay/test_batch_aware_replay.py
tests/integration/test_step4_batch_aware_replay.py
```

### Unit tests

必须覆盖：

1. 单请求可完成，并生成 request metrics 与 iteration metrics。
2. chunked prefill 长请求跨多个 iteration 完成。
3. 100% prefix-hit 请求走 zero-miss fast-finish。
4. r2 到达早于 r1 finish，但首次被 scheduler 考虑时 r1 已 finish，因此 r2 命中。
5. 同一 iteration 内两个相同 prompt 不能互相命中。
6. 跨实例相同 prompt 不共享 cache。
7. chunked prefill 关闭且队首请求超过 token budget 时，empty schedule fail fast。
8. `sum(iteration.scheduled_prefill_tokens) == sum(request.miss_tokens)`，但 zero-miss request 不贡献 scheduled tokens。

### Existing tests

完整 pytest 必须通过。

`ruff` 当前 `.venv` 未安装，可记录为未运行；不临时安装依赖。

## 验收标准

Batch C 代码完成后必须满足：

- [x] 完整 pytest 通过。
- [x] Phase1 runner 默认行为不变。
- [x] `BatchAwareReplayEngine.run()` 可被测试直接调用。
- [x] request metrics 中 `ttft_ms`、`scheduler_wait_ms`、hit/miss tokens 口径正确。
- [x] iteration metrics 可被 Batch D 直接写 CSV。
- [x] no external simulator adapter。
- [x] no runner/report integration。

## 明确不做

本轮不做：

- AIConfigurator adapter。
- MkSim adapter。
- Ramulator2 / DDR KV load。
- finite HBM LRU。
- DDR / SSD 多级缓存。
- Mooncake pooling / cross-instance cache。
- PD ratio search。
- Decode TPOT / MTP / graph capture。
- runner/report integration。

## 待用户审批

请 review 并确认：

1. 是否同意将 README 中的 `Core Semantics (Frozen)` 作为后续开发不变语义。
2. 是否同意先新增 `scheduler/planning.py`，让 bounded waiting lookup 与 scheduler 共用 token selection helper。
3. 是否同意 Batch C 实现 zero-miss fast-finish，且 zero-miss 在 Formula backend 阶段 compute duration 为 0。
4. 是否同意 Batch C 只新增 `BatchAwareReplayEngine.run()` 和测试，不接 runner/report。
