# Step4 Technical Route and Code Plan

## 设计原则

Step4 代码应遵循当前项目开发要求：

- 先 schema，再核心 lib，再测试，再报告输出。
- replay、scheduler、latency backend、report writer 职责分离。
- 不在 CLI / HTML / report 中重算核心分析逻辑。
- 不为未知 simulator 写启发式 fallback。
- 相同输入必须输出确定结果。
- cache block 仍只保存 hash，不保存 token 全量。

## Step4 核心变化

Step1-Step3 的核心是：

```text
trace -> tokenizer/chat_template -> prefix blocks -> infinite HBM lookup -> request metrics
```

Step4 改成：

```text
trace -> tokenizer/chat_template -> prefix blocks
      -> per-instance event replay
      -> vLLM-like scheduler creates BatchShape per iteration
      -> latency backend returns iteration duration
      -> finish-time materialization
      -> request metrics + iteration metrics
```

最重要的语义变化：

```text
cache lookup 从“请求到达时”移动到“请求首次被 scheduler 考虑时”。
```

## 建议代码结构

```text
src/hitfloor/scheduler/
  __init__.py
  config.py
  state.py
  batch_shape.py
  vllm_like.py

src/hitfloor/latency/
  __init__.py
  schema.py
  backend.py
  formula.py
  memo.py
  aiconfigurator.py
  markov_infer_sim.py

src/hitfloor/replay/
  __init__.py
  event_loop.py
  metrics.py

tests/unit/scheduler/
  test_vllm_like_scheduler.py
  test_chunked_prefill.py
  test_prefix_lookup_timing.py

tests/unit/latency/
  test_formula_backend.py
  test_shape_memo.py

tests/integration/
  test_step4_batch_aware_replay.py
```

如果保留现有 `src/hitfloor/instance/replay.py`，建议只作为 Step1-Step3 兼容入口，不继续膨胀。Step4 新 replay 放在 `src/hitfloor/replay/event_loop.py`，避免单文件承担过多职责。

## 数据模型

### SchedulerConfig

职责：只描述 scheduler 约束，不读取 CLI，不访问 cache，不调用 latency backend。

字段建议：

```text
max_num_batched_tokens: int
max_num_seqs: int
enable_chunked_prefill: bool
long_prefill_token_threshold: int | None
policy: Literal["fcfs"]
```

第一版错误处理：

- `max_num_batched_tokens <= 0`: 配置错误。
- `max_num_seqs <= 0`: 配置错误。
- `policy != "fcfs"`: 配置错误。

### RequestState

职责：保存一个请求在 scheduler replay 内部的生命周期状态。

字段建议：

```text
request_id
tenant_id
instance_uuid
arrival_time_ms
prompt_tokens
prefix_blocks
status
arrival_seq

cache_lookup_done
cached_tokens
miss_tokens
num_computed_tokens

first_scheduled_time_ms
finish_time_ms
scheduled_iteration_count
```

不负责：

- 不自己做 cache lookup。
- 不自己调用 latency backend。
- 不自己输出 CSV。

### ScheduledSlice

职责：描述一个 iteration 中某个请求被调度的 token 切片。

字段建议：

```text
request_id
scheduled_prefill_tokens
computed_tokens_before
computed_tokens_after
prompt_tokens
cached_tokens
```

### BatchShape

职责：作为 scheduler 与 latency backend 的稳定边界。

字段建议：

```text
instance_uuid
iteration_id
start_time_ms
batch_size
scheduled_prefill_tokens
scheduled_decode_tokens
max_query_len
total_context_tokens
request_slices
```

其中：

- `batch_size` 第一版定义为本 iteration 被调度的 request slice 数。
- `scheduled_prefill_tokens` 是本 iteration 实际需要计算的 prefill miss tokens。
- `scheduled_decode_tokens` 第一版固定为 0。
- `max_query_len` 是本 iteration 内最大 scheduled prefill tokens。
- `total_context_tokens` 是本 iteration 中各请求进入本轮计算前已经可用的上下文 token 总和，可用于 backend 近似 attention 成本。

### LatencyBackend

职责：把 `BatchShape` 转成 iteration duration。

协议建议：

```text
estimate(shape: BatchShape) -> LatencyResult
```

约束：

- replay 核心只能依赖该协议。
- backend 不允许修改 request/cache/scheduler 状态。
- backend 必须声明输出单位，统一返回 `duration_ms`。

### LatencyResult

字段建议：

```text
duration_ms
backend
shape_key
memoized
details
```

## 算法流程

### Per-instance replay

输入：同一个 `instance_uuid` 下按 `service_start_time` 排序的请求。

伪代码：

```text
now_ms = first_arrival_ms
waiting = []
running = []
pending_arrivals = sorted requests

while pending_arrivals or waiting or running:
    move arrivals with arrival_time_ms <= now_ms into waiting

    if waiting/running are empty:
        now_ms = next arrival time
        continue

    flush_materialized_cache_events(up_to=now_ms)

    batch = scheduler.schedule(
        now_ms=now_ms,
        waiting=waiting,
        running=running,
        cache=infinite_hbm_cache,
    )

    if batch is empty:
        now_ms = next arrival time or fail with config_guard
        continue

    latency = latency_backend.estimate(batch.shape)
    finish_ms = now_ms + latency.duration_ms

    apply scheduled slices at finish_ms
    materialize requests whose miss_tokens are complete
    write iteration metrics

    now_ms = finish_ms
```

### Arrival handling

请求到达时：

1. 已经在 Step1-Step3 完成 tokenizer/chat template/block hash。
2. 创建 `RequestState`。
3. 放入 waiting queue。

到达时不做 prefix cache lookup。

### Prefix lookup timing

请求首次被 scheduler 考虑时：

1. 确认所有 `finish_time <= now_ms` 的 materialization event 已经写入 cache。
2. 对 request 的 prefix blocks 做 lookup。
3. 设置 `cached_tokens` 和 `miss_tokens`。
4. 设置 `num_computed_tokens = cached_tokens`。

这个时机是 Step4 正确性的重点，必须有单测覆盖：

```text
finish_time 之前不可见，finish_time 之后可见。
```

### Batch formation

第一版 vLLM-like FCFS scheduler：

1. 初始化本轮 `token_budget = max_num_batched_tokens`。
2. 先遍历 running requests。
3. 再从 waiting queue 取请求。
4. 每个请求的候选 token 数：

```text
remaining = miss_tokens - (num_computed_tokens - cached_tokens)
```

5. 如果开启 chunked prefill：

```text
scheduled = min(remaining, token_budget, long_prefill_token_threshold or token_budget)
```

6. 如果不开启 chunked prefill：

```text
只有 remaining <= token_budget 时才能调度该请求
```

7. 同时满足 `max_num_seqs`。
8. 产生 `BatchShape` 和 `ScheduledSlice[]`。

### Finish-time materialization

一个请求只有在：

```text
num_computed_tokens == prompt_tokens
```

时才算 prefill 完成。

完成时：

1. `finish_time_ms = current_iteration_finish_ms`。
2. `ttft_ms = finish_time_ms - arrival_time_ms`。
3. 将该请求的 miss blocks 作为 materialization event 写入 cache。
4. event 的可见时间是 `finish_time_ms`。

对于 prefix blocks：

- 已命中的 block 不重复 materialize。
- 未命中的 block 在请求完成后 materialize。
- cache 内仍只保存 hash 和轻量 metadata。

## Shape Memoization

由于 2 小时 trace 可能产生大量重复 batch shape，Step4 应实现 latency shape memoization。

### ShapeKey

建议字段：

```text
backend
model_name
hardware_name
batch_size
scheduled_prefill_tokens
scheduled_decode_tokens
max_query_len
total_context_tokens_bucket
```

`total_context_tokens_bucket` 是否 bucket 化，需要根据 simulator 接口决定。Formula backend 可以先不 bucket，保持精确。

### Memo 行为

- 相同 `ShapeKey` 复用 `LatencyResult`。
- `LatencyResult.memoized = true`。
- memo 只缓存 backend 输出，不缓存 replay 状态。

单测覆盖：

- 相同 shape 只调用 backend 一次。
- 不同 model/hardware 不共享结果。

## FormulaLatencyBackend 第一版

用于 Batch C 开发和测试，不代表真实硬件。Batch D 默认 backend 已调整为 `FittedTTFTLatencyBackend` / `fitted_ttft`。

建议公式：

```text
duration_ms =
    fixed_overhead_ms
  + prefill_token_ms * scheduled_prefill_tokens
  + batch_overhead_ms * batch_size
  + context_token_ms * total_context_tokens
```

默认配置只放在 config 文件或显式构造参数中，不从环境变量读取。

测试目标：

- `duration_ms` 随 `scheduled_prefill_tokens` 增加。
- `duration_ms` 随 `batch_size` 增加。
- 相同输入输出完全一致。

## 配置示例

```yaml
scheduler:
  policy: fcfs
  max_num_batched_tokens: 8192
  max_num_seqs: 32
  enable_chunked_prefill: true
  long_prefill_token_threshold: 4096

latency:
  backend: fitted_ttft
  model_name: glm-v5
  hardware_name: local-dev
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
    calibrated_from: manual_default
```

## 实现顺序

### 1. Schema first

新增 scheduler / latency / replay 的 dataclass 或 pydantic-like 类型。

验收：

- 类型边界清楚。
- 单测可直接构造核心对象。
- 不依赖外部 simulator。

### 2. FormulaLatencyBackend + memo

实现最小 latency backend 和 shape memoization。

验收：

- latency 随 miss tokens 增加。
- 相同 shape memo 命中。
- 不读环境变量。

### 3. VllmLikeBatchScheduler

实现 FCFS、running-first、waiting admission、chunked prefill。

验收：

- token budget 不被突破。
- seq budget 不被突破。
- chunked prefill 能拆分长请求。
- 无 chunked prefill 时，大请求在预算不足时不会被错误调度。

### 4. BatchAwareReplayEngine

把请求到达、scheduler、cache lookup、latency backend、finish-time materialization 串起来。

验收：

- prefix cache 在 finish_time 前不可见。
- prefix cache 在 finish_time 后可见。
- 请求 TTFT 等于 finish_time - arrival_time。
- `sum(scheduled_prefill_tokens per request) == miss_tokens`。

### 5. Report writer 扩展

输出 `iteration_metrics.csv`，并扩展 `request_metrics.csv` 与 `summary.md`。

验收：

- report writer 只消费结果对象，不重算 replay。
- CSV schema 稳定。

### 6. Simulator adapter skeleton

在用户提供信息前只创建接口占位和 schema guard。

验收：

- 未配置外部 simulator 时不影响 formula backend。
- 如果用户选择外部 backend 但缺少必要配置，清晰报错，不 silent fallback。

## 测试计划

### Unit tests: scheduler

- `test_fcfs_schedules_waiting_requests_by_arrival_seq`
- `test_running_requests_are_scheduled_before_waiting_requests`
- `test_scheduler_respects_max_num_batched_tokens`
- `test_scheduler_respects_max_num_seqs`
- `test_chunked_prefill_splits_long_request`
- `test_non_chunked_prefill_waits_when_request_exceeds_budget`

### Unit tests: cache lookup timing

- `test_cache_materialization_is_not_visible_before_finish_time`
- `test_cache_materialization_is_visible_at_finish_time`
- `test_lookup_happens_on_first_schedule_not_arrival`

### Unit tests: latency

- `test_formula_latency_increases_with_prefill_tokens`
- `test_formula_latency_increases_with_batch_size`
- `test_shape_memo_reuses_identical_shape`
- `test_shape_memo_separates_model_and_hardware`

### Integration tests

- Synthetic trace with same prompt on same instance:
  - first request misses.
  - second request hits only if first request finished before second lookup.
- Synthetic trace with long prompt:
  - long prompt split across iterations.
  - TTFT equals sum of iteration durations touching that request plus scheduler wait.
- Synthetic trace with two instances:
  - same prompt across instances does not hit.

## 风险与决策点

### 1. batch size 口径需要 simulator 确认

HitFloor 内部第一版定义：

```text
batch_size = 本 iteration 调度的 request slice 数
```

如果外部 simulator 要求的是 token batch 或算子 batch，需要 adapter 做转换，不能污染 scheduler。

### 2. decode 干扰暂不建模

真实 vLLM 中 prefill 与 decode 混合会影响 token budget。Step4 第一版只为 TTFT 建立 prefill replay。若用户希望现网正在 decode 的请求也占用 batch budget，需要 trace 增加 completion/decode 信息，或引入输出长度分布假设。

### 3. scheduler_wait 是否计入 TTFT

默认建议计入：

```text
TTFT = finish_time - arrival_time
```

同时单独输出 `scheduler_wait_ms`，便于后续切换口径。如果产品坚持 `queue_time = 0`，可以额外输出 `service_ttft_ms = prefill_compute_time + kv_load_time`，但不要覆盖 `ttft_ms` 字段。

### 4. 外部 simulator 不应阻塞 Step4 主流程

Step4 必须能在没有 AIConfigurator / Markov-Infer-sim 的情况下通过 fitted TTFT backend 完成开发、测试和 E2E。外部 simulator adapter 是替换 latency backend，不是重写 replay。
