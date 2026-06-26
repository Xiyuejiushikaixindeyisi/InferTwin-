# Batch C Execution Plan: BatchAwareReplayEngine

## 当前前提

Batch A + Batch B 已通过代码审核：

- scheduler/latency schema 已就绪。
- `FormulaLatencyBackend.estimate_iteration(BatchShape)` 已就绪。
- `ShapeMemo` 已就绪。
- `VllmLikeBatchScheduler` 已就绪。

Batch C 的目标是把这些基础模块串成真正的 batch-aware replay。

## 当前状态

Batch C 的整体路线已通过初步认可，但代码开发暂停。

暂停原因：

- latency backend 接口设计需要结合 AIConfigurator 和 Markov-Infer-sim 手册重审。
- `batch size` 与 chunked prefill 的含义需要在两个仿真器口径下重新确认。
- 当前 `BatchShape` 是否足以作为 simulator input 尚未最终确认。

在完成 `docs/step4/08_simulator_interface_review_plan.md` 中定义的 manual review 前，不进入 Batch C 代码开发。

两份 simulator manual 已完成沉淀后，新增接口重审结论见：

```text
docs/step4/09_latency_backend_interface_design.md
```

Batch C 开发前应按该文档修正 `ScheduledSlice` 的 context 字段，并确认 `BatchShape` 是 scheduler output，不是外部 simulator input。

## Batch C 目标

实现：

```text
SimulationRequest[]
-> per-instance event loop
-> waiting/running queues
-> first-schedule-time prefix cache lookup
-> VllmLikeBatchScheduler emits BatchShape
-> FormulaLatencyBackend estimates iteration duration
-> finish_time advances
-> apply scheduled tokens
-> request finish
-> finish-time cache materialization
-> request metrics + iteration metrics
```

Batch C 完成后，HitFloor 具备完整的固定路由、多实例隔离、每实例无限 HBM、batch-aware replay 核心能力，但暂不接入 runner/report。

## 不做范围

Batch C 不做：

- 修改 `scripts/run_simulation.py`。
- 修改现有 Phase1 runner 默认行为。
- 输出 `iteration_metrics.csv`。
- 扩展 `summary.md`。
- 有限 HBM LRU / DDR LRU。
- 外部 simulator adapter。
- decode TPOT 干扰。

这些留到 Batch D 或后续阶段。

## 建议新增文件

```text
src/hitfloor/replay/
  __init__.py
  event_loop.py
  metrics.py

tests/unit/replay/
  test_batch_aware_replay.py

tests/integration/
  test_step4_batch_aware_replay.py
```

如果 `tests/unit/replay/` 目录不存在，需要新增。

## 数据模型设计

### BatchAwareReplayResult

文件：`src/hitfloor/replay/metrics.py`

职责：承载 replay 输出，不做计算。

字段建议：

```text
request_metrics: tuple[BatchAwareRequestMetrics, ...]
iteration_metrics: tuple[IterationMetrics, ...]
```

### BatchAwareRequestMetrics

职责：记录单请求最终指标。

字段建议：

```text
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
```

说明：

- `ttft_ms = finish_time_ms - arrival_time_ms`。
- `scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms`。
- 第一版无限 HBM，因此 `ddr_hit_tokens = 0`。

### IterationMetrics

职责：记录每个 scheduler iteration 的 batch shape 和 latency。

字段建议：

```text
instance_uuid
iteration_id
start_time_ms
finish_time_ms
duration_ms
batch_size
scheduled_prefill_tokens
scheduled_decode_tokens
active_request_count
backend
shape_key
memoized
request_ids
```

说明：

- `active_request_count` 第一版等于本轮 shape 的 `batch_size`。
- `request_ids` 用 tuple 保存，便于测试和后续 CSV 渲染。

## Replay Engine 接口

文件：`src/hitfloor/replay/event_loop.py`

建议类：

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

- `run()` 内部按 `instance_uuid` 分组。
- 每个实例拥有独立 `InfiniteHBMCache`。
- 每个实例独立 iteration counter。
- 不共享跨实例 cache。

## 核心算法

### 1. 请求分实例

输入 `SimulationRequest[]` 后：

1. 按 `instance_uuid` 分组。
2. 每组内按 `(start_time_ms, request_id)` 稳定排序。
3. 每组单独 replay。
4. 最终 request metrics 按 `(arrival_time_ms, request_id)` 排序输出。

### 2. SimulationRequest -> RequestState

到达时创建 `RequestState`：

```text
request_id
tenant_id
instance_uuid
arrival_time_ms = request.start_time_ms
prompt_tokens
prompt_blocks
model
tokenizer_profile
arrival_seq
```

此时不做 cache lookup。

### 3. Event loop

单个实例内部 event loop 伪代码：

```text
now_ms = first_arrival_ms
pending = sorted requests
waiting = []
running = []
iteration_id = 0

while pending or waiting or running:
    move arrivals with start_time_ms <= now_ms into waiting

    if not waiting and not running:
        now_ms = next pending arrival
        continue

    ensure_cache_lookup_for_schedulable_requests(waiting, running, now_ms)

    result = scheduler.schedule(
        instance_uuid=instance_uuid,
        iteration_id=iteration_id,
        start_time_ms=now_ms,
        waiting=waiting,
        running=running,
    )

    if result.is_empty:
        handle_empty_schedule()
        continue

    latency = estimate_with_memo(result.shape)
    finish_ms = now_ms + latency.duration_ms

    for slice in result.shape.request_slices:
        state = states_by_id[slice.request_id]
        state.apply_scheduled_tokens(slice.scheduled_prefill_tokens, finish_ms)
        if state.status == FINISHED:
            materialize state miss blocks at finish_ms
            remove state from running
            emit request metrics

    emit iteration metrics
    iteration_id += 1
    now_ms = finish_ms
```

### 4. Cache lookup timing

Batch C 最关键语义：

```text
cache lookup 发生在请求首次可能被调度前，而不是到达时。
```

实现建议：

- 对 running 中 `cache_lookup_done=False` 的请求执行 lookup。
- 对 waiting 队首开始、可能被本轮 scheduler admission 的请求执行 lookup。
- 不要提前 lookup 整个 waiting 队列，否则队尾请求会过早锁定 cache 结果。
- lookup 前不需要单独 flush event，因为 Batch C materialization 会在 finish_ms 同步写入 cache。
- 对请求执行一次 lookup 后，结果固定，不重复 lookup。

说明：

- 这会让“到达后但调度前已经完成的前序请求”对该请求可见。
- 同一 iteration 内刚完成的 block 不会被本 iteration 其他请求看到，因为 materialization 发生在 iteration finish。

### 5. Miss blocks materialization

需要在 engine 内记录每个 request 的 `miss_blocks`。

建议内部结构：

```text
request_miss_blocks: dict[str, tuple[PrefixBlock, ...]]
```

当请求首次 lookup：

- `hbm_hit_tokens = lookup.hbm_hit_tokens`
- `miss_tokens = lookup.miss_tokens`
- `miss_blocks = lookup.miss_blocks`
- 调用 `state.set_cache_lookup(hbm_hit_tokens, miss_tokens)`

当请求 finish：

- `cache.materialize(miss_blocks, now_ms=finish_ms)`
- 生成 request metrics。

### 6. Empty schedule 处理

可能场景：

- chunked prefill 关闭。
- waiting 队首请求 `miss_tokens > max_num_batched_tokens`。
- running 没有可调度 token。

第一版建议：

- 如果 `result.is_empty` 且 `waiting/running` 非空，直接抛出 `ValueError`。
- 错误信息说明可能是 scheduler config 不可满足，例如 no-chunked + request larger than token budget。

不要 silent skip，也不要临时跳过队首请求，否则 FCFS 语义会变脏。

## 测试计划

### Unit tests

文件：`tests/unit/replay/test_batch_aware_replay.py`

建议测试：

1. `test_replay_finishes_single_request`
   - 一个请求。
   - 无 cache hit。
   - 产生一个或多个 iteration。
   - `ttft_ms = finish_time_ms - arrival_time_ms`。

2. `test_cache_lookup_happens_on_first_schedule_not_arrival`
   - r1 和 r2 相同 prompt。
   - r2 在 r1 运行中到达，但因为预算限制未被调度。
   - r1 finish 后 materialize。
   - r2 首次调度时命中 r1 的 block。

3. `test_materialization_not_visible_within_same_iteration`
   - 两个相同 prompt 同时到达。
   - 被同一个 iteration 调度。
   - r2 不能命中 r1，因为 r1 还没 finish。

4. `test_instances_do_not_share_cache`
   - 相同 prompt 分别进入 instance-a / instance-b。
   - instance-b 不命中 instance-a。

5. `test_empty_schedule_fails_fast`
   - chunked prefill disabled。
   - request miss tokens 大于 token budget。
   - 抛出清晰 `ValueError`。

### Integration test

文件：`tests/integration/test_step4_batch_aware_replay.py`

建议测试：

- 构造少量 `SimulationRequest`，使用真实 block hasher。
- `FormulaLatencyBackend` + `VllmLikeBatchScheduler`。
- 验证 request metrics 和 iteration metrics 都有输出。
- 验证总 scheduled prefill tokens 等于总 miss tokens。

## 验收标准

Batch C 通过条件：

- 完整 pytest 通过。
- 新增 unit/integration tests 覆盖 replay event loop。
- cache lookup timing 被测试覆盖。
- finish-time materialization 被测试覆盖。
- request metrics 中 `ttft_ms` 和 `scheduler_wait_ms` 口径正确。
- iteration metrics 可用于 Batch D 输出 CSV。
- 没有修改现有 Phase1 runner 默认行为。

## 风险点

### 1. 首次调度前 lookup 的粒度

如果对 waiting 队列所有请求都提前 lookup，会让队尾请求过早锁定 cache 结果。  

Batch C 需要采用更保守的做法：

- running 请求如果还未 lookup，必须 lookup。
- waiting 请求只在 scheduler 准备考虑它时 lookup。
- 如果 waiting 队首无法被调度，后面的 waiting 请求不能越过队首做 lookup。

实现上建议把 cache lookup 注入 scheduler 之前的一层 helper：

```text
ensure_lookup_for_running(running)
ensure_lookup_for_waiting_head_until_budget_or_seq_limit(waiting)
```

这样 replay engine 不需要让 scheduler 直接依赖 cache，但也不会提前污染队尾请求的 lookup 时间。

### 2. Scheduler 当前要求 request 已 lookup

`VllmLikeBatchScheduler` 当前会调用 `remaining_prefill_tokens()`，该方法要求 request 已经完成 cache lookup。

Batch C 因此必须在调用 `scheduler.schedule()` 前保证：

- running 中可调度请求已 lookup。
- waiting 队首中会被考虑的请求已 lookup。

如果后续发现这个 pre-lookup helper 过于别扭，可以在下一轮重构 scheduler，改成接收一个 lookup callback。但 Batch C 第一版先不把 cache 依赖塞进 scheduler。

### 3. 同一 iteration 内的可见性

同一 iteration 中多个相同 prompt 请求一起被调度时，后一个请求不能命中前一个请求。  

原因：

```text
materialize 发生在 iteration finish_time，而不是 schedule start_time。
```

因此 Batch C 在一个 iteration 内不能边调度边 materialize。

### 4. Running list 清理

请求 finish 后必须从 running 中移除。  

建议不要在遍历 running 时直接删除，而是在应用完本轮 slices 后统一重建：

```text
running = [state for state in running if state.status != FINISHED]
```

避免边遍历边修改造成漏处理。

### 5. Shape memo 不应缓存 replay 状态

`ShapeMemo` 只能缓存 latency result。  

不能缓存：

- request state
- cache lookup
- materialization result
- iteration metrics

## 开发顺序

### C1: Metrics schema

新增 `src/hitfloor/replay/metrics.py`。

验收：

- dataclass 字段稳定。
- 无 replay 逻辑。
- 单测可以直接构造。

### C2: Engine skeleton

新增 `src/hitfloor/replay/event_loop.py`。

先实现：

- 分实例。
- request -> state。
- 空请求返回空 result。
- 稳定排序。

验收：

- 不接 scheduler 时也能通过最小结构测试。

### C3: Cache lookup helper

实现：

- request 首次 lookup。
- 保存 hit/miss tokens。
- 保存 miss blocks。
- 不重复 lookup。

验收：

- 同一个 request lookup 一次。
- lookup 结果转成 `RequestState` 的 `cached_tokens/miss_tokens`。

### C4: Scheduler + latency loop

实现完整 loop：

- schedule。
- memoized latency estimate。
- finish_ms。
- apply scheduled tokens。
- emit iteration metrics。

验收：

- 单请求可完成。
- chunked request 可多 iteration 完成。

### C5: Finish-time materialization

实现：

- request finish 后 materialize miss blocks。
- emit request metrics。
- remove finished running requests。

验收：

- materialization before/after 可见性测试通过。
- 跨实例不共享 cache。

### C6: Integration test

新增 synthetic integration test。

验收：

- request metrics 和 iteration metrics 都有输出。
- `sum(iteration.scheduled_prefill_tokens) == sum(request.miss_tokens)`。
- 现有 Phase1 runner 测试不受影响。

## 需要用户审批的点

Batch C 开发前需要确认：

1. 已确认：Batch C 暂不接入 runner/report，只提供 `BatchAwareReplayEngine.run()` 给测试和 Batch D 使用。
2. 已确认：empty schedule 直接失败，而不是跳过请求或自动打开 chunked prefill。
3. 已确认：waiting lookup 采用“只对 scheduler 本轮可能考虑的队首请求 lookup”的保守策略。

Batch C 代码开发前新增前置审批：

1. 已完成 AIConfigurator manual review。
2. 已完成 Markov-Infer-sim manual review。
3. 已完成 latency backend interface redesign。
4. 用户审批重审后的 Batch C 数据结构和接口设计。
