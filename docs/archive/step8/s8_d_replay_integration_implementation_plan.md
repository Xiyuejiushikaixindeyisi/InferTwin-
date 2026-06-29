# S8-D 实施方案：Replay Integration

状态：已完成代码开发，待用户代码评审。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-D：Replay Integration。

前置条件：

- S8-A 已完成 `ScheduledSlice.kv_load_tokens` / `kv_load_bytes` 与 `ShapeKey` KV load 维度。
- S8-B 已完成 `KVLoadLatencyComponent` 和 `KVLoadLatencyProfile` 显式 schema。
- S8-C 已完成 instance/model latency resolver 到 `ServingLatencyProfile` 的接入。

## 1. 类型与改动等级

本 Batch 属于核心仿真器。

改动等级：L3。

原因：

- 本 Batch 首次把 cache lookup 的 DDR hit 结果写入 replay-facing `BatchShape`。
- 本 Batch 会改变 DDR hit 请求的 `iteration_duration`、`finish_time` 和 `ttft_ms`。
- 本 Batch 会改变 DDR-only load-only 请求的完成路径：不能再被 zero-miss immediate finish 跳过。
- 本 Batch 需要修改 scheduler / replay event loop / request state 的协作边界。

本 Batch 不改变 Step7 已确认的 HBM / DDR / miss token accounting，也不改变 cache lookup / materialization / eviction 状态转移。

## 2. 本 Batch 做什么

S8-D 只做 replay integration：

1. 在 cache lookup 后，把 accounted DDR hit 转成 request 级 pending KV load：

```text
pending_kv_load_tokens = lookup.ddr_hit_tokens
pending_kv_load_bytes = sum(accounted_ddr_hit_blocks.size_bytes)
```

2. request 第一次被 scheduler 选中时，将 pending KV load 消费到本轮 `ScheduledSlice`：

```text
first scheduled slice:
  kv_load_tokens = pending_kv_load_tokens
  kv_load_bytes = pending_kv_load_bytes

later scheduled slices:
  kv_load_tokens = 0
  kv_load_bytes = 0
```

3. 同一 iteration 内多个 request 的 `kv_load_tokens` / `kv_load_bytes` 由 `BatchShape` 聚合，交给 `ServingLatencyProfile.kv_load_component`。

4. 保持 Step8 v1 默认加和语义：

```text
iteration_duration_ms = prefill_compute_ms + kv_load_ms
overlap_mode = none_v1
aggregation = shared_link_sum
```

5. 修正 zero-miss 分支：

```text
miss_tokens == 0 and ddr_hit_tokens == 0:
  HBM-only / empty prompt immediate finish，保持不变

miss_tokens == 0 and ddr_hit_tokens > 0:
  不 immediate finish
  进入一次 load-only iteration
  finish_time = now + kv_load_ms
```

6. 对 load-only shape 明确 compute 语义：

```text
scheduled_prefill_tokens = 0
kv_load_tokens > 0 or kv_load_bytes > 0
prefill_compute_ms = 0
kv_load_ms > 0
```

7. list replay 和 true streaming replay 共用同一套 scheduler / event loop helper，保持行为一致。

## 3. 本 Batch 不做什么

S8-D 不做：

- 不改变 `cached_tokens` / `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` 计算公式。
- 不改变 vLLM-like `prompt_tokens - 1` cache hit accounting。
- 不改变 HBM first、DDR second 的 lookup 顺序。
- 不改变 finish-time materialization。
- 不改变 HBM / DDR LRU eviction。
- 不做 DDR hit promotion 到 HBM。
- 不新增 load completion cache event。
- 不做 KV load queue / backpressure。
- 不做 compute/load overlap。
- 不做 layerwise 或 chunkwise KV load 拆分。
- 不接 Ramulator2 / Mooncake online replay。
- 不修改 report/export 字段；KV load typed metrics 聚合属于 S8-E。
- 不修改 capacity sweep CSV schema；S8-D 只通过现有 TTFT/P90 体现影响。
- 不做跨实例带宽共享或多实例池化。
- 不做 gateway routing、实例入口真实排队、Decode / TPOT。

如果开发中发现必须修改上述内容，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/replay/metrics.py`

职责：

- 将 raw cache lookup result 转成 replay-facing lookup metrics。
- 维持 request token accounting 不变量。

计划修改：

- `LookupMetrics` 新增内部字段：

```python
ddr_hit_bytes: int = 0
```

- `LookupMetrics.from_result()` 使用 `account_prefix_lookup(...)` 的 `accounted.ddr_hit_blocks` 计算 bytes：

```text
ddr_hit_bytes = sum(block.size_bytes for block in accounted.ddr_hit_blocks)
```

原因：

- token-linear KV load 只需要 `ddr_hit_tokens`。
- byte-linear KV load 必须使用 accounted DDR hit blocks 的 bytes，而不能使用 raw DDR hit blocks。
- `accounted.ddr_hit_blocks` 已经经过 vLLM-like block conversion、CP/MTP drop、full-block 对齐等规则。

边界：

- 不把 `ddr_hit_bytes` 暴露到 report/export。
- 不新增 request metrics 字段；typed metrics 输出留给 S8-E。

### 4.2 `src/infertwin/scheduler/state.py`

职责：

- 保存单 request 在 scheduler/replay 中的生命周期状态。

计划修改：

- 新增一次性 KV load pending 状态：

```python
pending_kv_load_tokens: int = 0
pending_kv_load_bytes: int = 0
kv_load_scheduled: bool = False
```

- 扩展 `set_cache_lookup(...)`：

```python
def set_cache_lookup(
    self,
    cached_tokens: int,
    miss_tokens: int,
    kv_load_tokens: int = 0,
    kv_load_bytes: int = 0,
) -> None:
    ...
```

- 新增 helper：

```python
def has_pending_kv_load(self) -> bool:
    ...

def consume_pending_kv_load(self) -> tuple[int, int]:
    ...
```

语义：

- `pending_kv_load_tokens` / `pending_kv_load_bytes` 只来自 cache lookup。
- `consume_pending_kv_load()` 只允许消费一次。
- HBM-only hit 不产生 pending load。
- DDR hit request 即使后续被 chunked prefill 拆成多轮，也只在第一轮 slice 上携带 KV load。

边界：

- 不把 HBM hit 写入 KV load。
- 不在 `RequestState` 中保存真实 KV tensor、block list 或 tier policy。
- 不改变 `cached_tokens` / `miss_tokens` 不变量：

```text
cached_tokens + miss_tokens == prompt_tokens
```

### 4.3 `src/infertwin/scheduler/batch_shape.py`

职责：

- 定义 scheduler iteration 输出 shape。

计划修改：

- 放宽 `ScheduledSlice.scheduled_prefill_tokens` 校验：

```text
scheduled_prefill_tokens > 0:
  正常 prefill slice

scheduled_prefill_tokens == 0:
  仅允许 kv_load_tokens > 0 or kv_load_bytes > 0
  表示 load-only slice

scheduled_prefill_tokens < 0:
  fail-fast
```

- `computed_tokens_after == computed_tokens_before + scheduled_prefill_tokens` 不变量保持。
- `prompt_tokens >= computed_tokens_after` 不变量保持。

原因：

- `miss_tokens == 0 and ddr_hit_tokens > 0` 没有 prefill compute token，但仍需要进入一次 KV load latency iteration。
- 不能用 fake prefill token 代表 load，否则会污染 miss token / scheduled prefill token 统计。

边界：

- `scheduled_prefill_tokens == 0 and no KV load` 仍然非法。
- HBM-only zero-miss 不生成 load-only slice，继续 immediate finish。

### 4.4 `src/infertwin/scheduler/vllm_like.py`

职责：

- 形成 vLLM-like iteration request slices。
- 不估算 latency、不修改 cache。

计划修改：

- `_slice_for(...)` 消费 `RequestState.consume_pending_kv_load()`，把 pending KV load 写入 `ScheduledSlice`。
- waiting path 支持 load-only request：

```text
if planned_prefill_tokens == 0 and request.has_pending_kv_load():
  pop waiting
  mark RUNNING
  set first_scheduled_time_ms
  append load-only ScheduledSlice
```

- running path 支持 load-only request，作为防御性分支。

边界：

- 不让 scheduler 做 cache lookup。
- 不让 scheduler 计算 DDR hit。
- 不让 scheduler 计算 KV load latency。
- scheduler 只消费已经由 replay 写入 `RequestState` 的 pending KV load shape。

### 4.5 `src/infertwin/replay/event_loop.py`

职责：

- 管理 per-instance replay lifecycle。
- 在 lookup、scheduler、latency、finish/materialization 之间编排状态。

计划修改：

- `_ensure_lookup(...)` 将 `LookupMetrics.ddr_hit_tokens` / `ddr_hit_bytes` 写入 `RequestState.set_cache_lookup(...)`。

```text
state.set_cache_lookup(
  cached_tokens=lookup_metrics.effective_hit_tokens,
  miss_tokens=lookup_metrics.miss_tokens,
  kv_load_tokens=lookup_metrics.ddr_hit_tokens,
  kv_load_bytes=lookup_metrics.ddr_hit_bytes,
)
```

- `_finish_zero_miss_requests(...)` 和 `_prepare_waiting_frontier(...)` 增加 guard：

```text
if state.remaining_prefill_tokens() == 0 and state.has_pending_kv_load():
  不 immediate finish
  交给 scheduler 产生 load-only slice
```

- `_apply_schedule_result(...)` 支持 `scheduled_prefill_tokens == 0` 的 load-only slice：

```text
state.apply_scheduled_tokens(scheduled_tokens=0, finish_time_ms=finish_ms)
```

或新增更清晰的 `state.apply_load_only_iteration(finish_time_ms)`。

优先建议：

```text
修改 apply_scheduled_tokens，使 0 token 只在 remaining_prefill_tokens() == 0 且本轮 slice 有 KV load 时由 event_loop 调用。
```

但为了让 `RequestState` 不依赖 `ScheduledSlice`，代码实现时可选择新增 `apply_load_only_iteration()`，由 event loop 在检测到 load-only slice 时调用。开发时若选择另一种实现，必须保持测试覆盖同等语义。

边界：

- 不改变 `_drain_cache_events` 调用顺序。
- 不改变 materialization policy 调用条件；DDR-only load-only request 没有 miss blocks，不会 materialize。
- 不新增 cache event。
- 不改变 per-instance grouping 和实例隔离。

### 4.6 `src/infertwin/latency/profile.py`

职责：

- 组合 TTFT、queue、KV load component。

计划修改：

- 增加 load-only shape 的 compute 口径：

```text
if shape.scheduled_prefill_tokens == 0 and shape.kv_load_request_count > 0:
  ttft_ms / prefill_compute_ms = 0
  kv_load_ms = kv_load_component.estimate_iteration(shape)
  duration_ms = queue_ms + kv_load_ms
```

原因：

- `FittedTTFTLatencyBackend` 的 intercept 是 prefill compute profile 的一部分。
- load-only request 没有 uncached prefill compute，不应该因为 TTFT intercept 被额外收费。

边界：

- 正常 prefill shape 继续调用原 `ttft_backend.estimate_iteration(shape)`。
- HBM-only zero-miss 不会进入 `ServingLatencyProfile.estimate_iteration()`。
- legacy `FormulaLatencyBackend` 暂不增加 KV load-aware load-only 特殊分支；Step8 正式语义以 `ServingLatencyProfile` 为准。

如果用户认为 load-only 仍应收取 TTFT fixed overhead，需要在代码开发前修改本设计。

### 4.7 `src/infertwin/streaming/replay.py`

职责：

- streaming request source 的 per-instance replay loop。

计划：

- 原则上不修改。
- streaming replay 已复用 `BatchAwareReplayEngine._prepare_scheduler_frontier()`、`_apply_schedule_result()` 和同一个 scheduler。
- 如果代码开发中发现 list replay 与 streaming replay 出现重复逻辑必须同步修改，应暂停并重新评审。

### 4.8 测试文件

计划新增/修改：

```text
tests/unit/scheduler/test_request_state_kv_load.py
tests/unit/scheduler/test_vllm_like_scheduler.py
tests/unit/scheduler/test_batch_shape_kv_load.py
tests/unit/replay/test_step8_kv_load_replay.py
tests/unit/latency/test_serving_latency_profile.py
tests/unit/streaming/test_streaming_replay.py
tests/integration/test_step8_streaming_kv_load_e2e.py
```

具体测试策略见第 8 节。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 `LookupMetrics.ddr_hit_bytes`

新增字段：

```python
ddr_hit_bytes: int = 0
```

语义：

- accounted DDR hit blocks 的 KV bytes。
- byte-linear KV load 的 replay input。
- 使用 `accounted.ddr_hit_blocks`，不使用 raw DDR hit blocks。

### 5.2 `RequestState` KV load pending fields

新增字段：

```python
pending_kv_load_tokens: int
pending_kv_load_bytes: int
kv_load_scheduled: bool
```

语义：

- request 第一次 scheduler selection 前保存 KV load shape。
- scheduler 消费后置零或标记已消费。
- 用来保证多 chunk request 不重复收取 KV load。

### 5.3 Load-only `ScheduledSlice`

新增允许形态：

```text
scheduled_prefill_tokens = 0
kv_load_tokens > 0 or kv_load_bytes > 0
```

语义：

- 本 iteration 不计算 uncached prefill token。
- 本 iteration 只为非 HBM hit 执行 KV load latency。

### 5.4 `ServingLatencyProfile` load-only branch

新增内部语义：

```text
load_only = shape.scheduled_prefill_tokens == 0 and shape.kv_load_request_count > 0

if load_only:
  prefill_compute_ms = 0
  kv_load_ms = kv_load_component(shape)
else:
  prefill_compute_ms = ttft_backend(shape)
  kv_load_ms = kv_load_component(shape)
```

不新增 public schema。

## 6. 核心算法逻辑

### 6.1 Lookup -> pending KV load

伪代码：

```text
_ensure_lookup(state, request):
  lookup = cache.lookup_prefix(...)
  lookup_metrics = LookupMetrics.from_result(lookup, request=request)

  state.set_cache_lookup(
    cached_tokens=lookup_metrics.effective_hit_tokens,
    miss_tokens=lookup_metrics.miss_tokens,
    kv_load_tokens=lookup_metrics.ddr_hit_tokens,
    kv_load_bytes=lookup_metrics.ddr_hit_bytes,
  )
```

### 6.2 Zero-miss immediate finish guard

伪代码：

```text
if state.remaining_prefill_tokens() == 0:
  if state.has_pending_kv_load():
    do not finish here
  else:
    finish_zero_miss_request(...)
```

语义：

- HBM-only full hit / empty prompt：继续 immediate finish。
- DDR hit：必须等待 KV load latency。

注意：

- 在当前默认 vLLM-like full-attention accounting 下，`prompt_tokens - 1` 规则通常会让普通非空 prompt 至少保留 1 个 miss token，因此 `miss_tokens == 0 and ddr_hit_tokens > 0` 不是主路径高频情况。
- 但该 guard 仍是必须的，因为未来 Step9 progressive mode、特殊 request、非 full-attention accounting 或后续 cache manager 可能产生真正 load-only request。

### 6.3 Scheduler consumes pending load once

伪代码：

```text
_slice_for(request, scheduled_tokens):
  kv_load_tokens, kv_load_bytes = request.consume_pending_kv_load()
  return ScheduledSlice(
    scheduled_prefill_tokens=scheduled_tokens,
    ...
    kv_load_tokens=kv_load_tokens,
    kv_load_bytes=kv_load_bytes,
  )
```

多 chunk request 示例：

```text
lookup:
  ddr_hit_tokens = 1024
  miss_tokens = 8192

iteration 0:
  scheduled_prefill_tokens = 2048
  kv_load_tokens = 1024

iteration 1:
  scheduled_prefill_tokens = 2048
  kv_load_tokens = 0
```

### 6.4 Load-only scheduling

伪代码：

```text
if planned_tokens == 0 and request.has_pending_kv_load():
  schedule load-only slice
```

load-only slice：

```text
scheduled_prefill_tokens = 0
computed_tokens_before = prompt_tokens
computed_tokens_after = prompt_tokens
kv_load_tokens = ddr_hit_tokens
```

### 6.5 Latency estimation

普通 DDR hit + miss：

```text
duration = fitted_prefill_compute(scheduled_prefill_tokens)
         + kv_load(ddr_hit_tokens / ddr_hit_bytes)
```

DDR-only load-only：

```text
duration = kv_load(ddr_hit_tokens / ddr_hit_bytes)
```

HBM-only zero-miss：

```text
duration = 0
iteration_metrics = none
```

## 7. 对核心 replay 语义的影响

### 7.1 是否改变 cached_tokens

不改变。

`cached_tokens` 仍由 vLLM-like cache block conversion 决定。

### 7.2 是否改变 hbm_hit_tokens / ddr_hit_tokens / miss_tokens

不改变。

S8-D 只消费 `LookupMetrics.ddr_hit_tokens` 作为 latency input，不修改 token accounting。

### 7.3 是否改变 finish_time / ttft_ms

改变。

当 request 有 DDR hit 且 `kv_load.mode != zero` 时：

```text
finish_time_ms 增加 kv_load_ms
ttft_ms 增加 kv_load_ms
```

当 request 只有 HBM hit 或 miss，无 DDR hit：

```text
finish_time / ttft_ms 与 S8-C 保持一致
```

当 request 是 DDR-only load-only：

```text
原先可能 immediate finish
S8-D 后 finish_time = now + kv_load_ms
```

### 7.4 是否改变 cache event 顺序

不改变 cache event 顺序。

S8-D 不新增 load event。lookup event 仍发生在 scheduler frontier lookup 阶段；materialization event 仍发生在 request finish time。

### 7.5 是否改变 materialization timing

不改变。

finish-time materialization 继续保持。Step9 才会新增 progressive visibility mode。

### 7.6 是否改变实例隔离

不改变。

每个实例仍有自己的 request source、cache、scheduler、latency backend。S8-D 不引入跨实例 bandwidth sharing。

### 7.7 是否影响 true streaming 大 trace

影响，但必须保持 streaming-safe。

- S8-D 不增加全量 request 持有。
- pending KV load 只存在 active `RequestState` 中。
- 不新增 cache event 内存持有。
- streaming replay 和 list replay 通过共享 helper 保持一致。

## 8. 测试计划

### 8.1 单测

#### `tests/unit/scheduler/test_request_state_kv_load.py`

新增测试：

1. `set_cache_lookup(..., kv_load_tokens, kv_load_bytes)` 保存 pending KV load。
2. `consume_pending_kv_load()` 第一次返回 tokens/bytes。
3. 第二次消费返回 0/0 或明确已无 pending load。
4. 负数 KV load tokens/bytes fail-fast。
5. `cached_tokens + miss_tokens == prompt_tokens` 不变量保持。

#### `tests/unit/scheduler/test_batch_shape_kv_load.py`

修改/新增测试：

1. `scheduled_prefill_tokens == 0 and kv_load_tokens > 0` 合法。
2. `scheduled_prefill_tokens == 0 and kv_load_tokens == 0 and kv_load_bytes == 0` 非法。
3. 负数 scheduled prefill 仍 fail-fast。

#### `tests/unit/scheduler/test_vllm_like_scheduler.py`

新增测试：

1. 第一次 scheduled slice 携带 pending KV load。
2. 同一 request 第二次 scheduled slice 不再携带 KV load。
3. waiting 队首 request 若 remaining prefill 为 0 但有 pending KV load，会产生 load-only slice。
4. HBM-only zero-miss 无 pending load，不由 scheduler 产生 slice。

#### `tests/unit/latency/test_serving_latency_profile.py`

新增测试：

1. load-only shape 不调用/不收费 TTFT compute intercept。
2. load-only shape duration 等于 `kv_load_ms`。
3. 正常 prefill + DDR load shape 仍等于 `ttft_ms + kv_load_ms`。

#### `tests/unit/replay/test_step8_kv_load_replay.py`

新增测试：

1. DDR hit request 的 first iteration shape key 包含 `kvload_tokens > 0`。
2. DDR hit request 的 TTFT 随 `ddr_ms_per_cached_token` 增加。
3. chunked prefill request 只在第一轮 iteration 收一次 KV load。
4. HBM-only zero-miss immediate finish 保持：无 iteration，`ttft_ms == 0`。
5. DDR-only load-only request 进入一轮 load-only iteration。

关于第 5 条：

- 当前默认 vLLM-like full-attention accounting 下，普通非空 prompt 因 `prompt_tokens - 1` 通常不会自然产生 `miss_tokens == 0`。
- 测试可用最小 fake cache / 显式 `CacheBlockConversionResult` 构造该保护分支，并在测试名中标明这是 replay guard，不代表默认 vLLM full-attention 主路径。

### 8.2 集成测试

新增：

```text
tests/integration/test_step8_streaming_kv_load_e2e.py
```

覆盖：

1. 使用 streaming `batch_aware_hbm_ddr_lru`。
2. 合成 trace 让 instance-a 产生 DDR hit。
3. model default latency 使用：

```yaml
kv_load:
  mode: token_linear_v1
  ddr_fixed_overhead_ms: 0.0
  ddr_ms_per_cached_token: 0.5
```

4. 验证：

```text
trace_row.ddr_hit_tokens > 0
instance_a.p90_ttft_ms > 同配置 mode=zero baseline
instance_b 若无 DDR hit，则 TTFT 不受 KV load 参数影响
```

注意：

- S8-D 不要求 `capacity_sweep.csv` 新增 `kv_load_ms` 字段；只验证 TTFT 已受影响。
- S8-E 再做 typed metrics / CSV 字段扩展。

### 8.3 小 E2E

建议用现有 streaming runner 合成数据：

```text
capacity = 1
block_size_tokens = 2
两条同 instance 相同 prompt
第一条 materialize 到 HBM + DDR
第二条部分 HBM hit + 部分 DDR hit
```

比较：

```text
kv_load mode=zero:
  p90_ttft_ms = baseline

kv_load token_linear_v1:
  p90_ttft_ms = baseline + expected_kv_load_ms
```

如果由于 percentile 或 batch 并发导致精确值不稳定，至少验证单 request metric / iteration metric 中的 finish time 差异。

### 8.4 Golden 更新

原则上不更新 HBM-only golden。

原因：

- HBM-only `batch_aware_hbm_lru` 路径不应改变。
- Existing Step4/Step5 golden 中 `ddr_hit_tokens == 0`，KV load 默认为 0。

可能需要更新：

- 如果 `ScheduledSlice` 允许 0 prefill 后影响已有 validation 文案，但 HBM-only输出不应改变。
- 如果新增 Step8 DDR E2E golden，则使用新的 dedicated test，不修改旧 golden。

## 9. 风险与回滚边界

### 9.1 风险：scheduler 承担过多 KV load 逻辑

控制：

- scheduler 只消费 `RequestState.pending_kv_load_*`。
- cache lookup、DDR hit accounting、latency estimation 仍在各自模块。

### 9.2 风险：load-only slice 破坏旧的 positive scheduled token 假设

控制：

- 只允许 `scheduled_prefill_tokens == 0` 与 KV load 同时出现。
- HBM-only zero-miss 继续不生成 slice。
- 新增 batch shape / scheduler / replay 测试。

### 9.3 风险：byte-linear bytes 口径不完整

控制：

- `ddr_hit_bytes` 使用 accounted DDR hit blocks。
- 若 blocks `size_bytes == 0`，byte-linear component 已 fail-fast。
- token-linear 模式不依赖 bytes。

### 9.4 风险：TTFT intercept 在 load-only 分支被重复收费

控制：

- 在 `ServingLatencyProfile` 中显式定义 load-only compute=0。
- 测试覆盖 load-only duration 只来自 KV load。

### 9.5 回滚边界

S8-D 回滚只需恢复：

```text
src/infertwin/replay/metrics.py
src/infertwin/scheduler/state.py
src/infertwin/scheduler/batch_shape.py
src/infertwin/scheduler/vllm_like.py
src/infertwin/replay/event_loop.py
src/infertwin/latency/profile.py
tests added/modified for S8-D
```

不会涉及 config schema、model registry、cache backend、streaming shard builder 或 report/export。

## 10. 完成后如何判断可以进入下一个 Batch

S8-D 完成条件：

1. DDR hit request 的 `BatchShape.kv_load_tokens` / `kv_load_bytes` 非零。
2. 同一 request 多 chunk 只收一次 KV load。
3. `ServingLatencyProfile` 让 DDR hit request 的 `finish_time` / `ttft_ms` 增加。
4. HBM-only zero-miss immediate finish 保持不变。
5. DDR-only load-only guard 已覆盖。
6. HBM / DDR / miss token accounting 不变。
7. cache event 顺序不变或测试确认没有新增/重排。
8. true streaming E2E 通过。
9. `ruff` 和 `git diff --check` 通过。

建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/scheduler/test_vllm_like_scheduler.py \
  tests/unit/scheduler/test_request_state_kv_load.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/replay/test_step8_kv_load_replay.py \
  tests/unit/streaming/test_streaming_replay.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py

.venv/bin/ruff check \
  src/infertwin/replay/metrics.py \
  src/infertwin/scheduler/state.py \
  src/infertwin/scheduler/batch_shape.py \
  src/infertwin/scheduler/vllm_like.py \
  src/infertwin/replay/event_loop.py \
  src/infertwin/latency/profile.py \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/scheduler/test_vllm_like_scheduler.py \
  tests/unit/scheduler/test_request_state_kv_load.py \
  tests/unit/replay/test_step8_kv_load_replay.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py

git diff --check
```

达到以上条件后，可以进入 S8-E：Streaming Metrics / Typed Result。

## 11. 需要用户审批的内容

进入 S8-D 代码开发前，请用户明确批准或修改以下决定：

1. S8-D 属于核心仿真器，改动等级为 L3。
2. 接受在 `RequestState` 中新增一次性 pending KV load 字段，用于保证同一请求只收费一次。
3. 接受 `ScheduledSlice.scheduled_prefill_tokens == 0` 作为 load-only slice，但仅当 `kv_load_tokens > 0` 或 `kv_load_bytes > 0` 时合法。
4. 接受 scheduler 消费 pending KV load 并写入 first scheduled slice；scheduler 不做 lookup、不估算 latency。
5. 接受 `LookupMetrics.ddr_hit_bytes` 使用 accounted DDR hit blocks 计算，而不是 raw DDR hit blocks。
6. 接受 HBM-only zero-miss 继续 immediate finish。
7. 接受 `miss_tokens == 0 and ddr_hit_tokens > 0` 进入 load-only iteration。
8. 接受 load-only shape 的 prefill compute time 为 0，不收 TTFT fitted intercept。
9. 接受 S8-D 不新增 request/iteration/capacity sweep typed KV load metrics；这些留给 S8-E。
10. 接受 S8-D 不新增 cache load event，不改变 cache event 顺序。
11. 接受 S8-D 不做 overlap、load queue/backpressure、promotion、layerwise load、Ramulator2/Mooncake online replay。
12. 接受本 Batch 允许修改的文件范围为第 4 节列出的源码和测试文件。
13. 接受如果开发中必须修改 streaming runner、cache backend、report/export、config schema 或 model registry，需要暂停并重新评审。

如果以上任一决定不被接受，应先修订本文，再进入 S8-D 代码开发。

## 12. 执行记录

执行状态：已完成。

本轮实际修改：

- `LookupMetrics` 增加 `ddr_hit_bytes`，来源为 accounted DDR hit blocks。
- `RequestState` 增加一次性 pending KV load 状态和消费 helper。
- `ScheduledSlice` 支持 load-only slice：`scheduled_prefill_tokens == 0` 且必须有 KV load。
- `VllmLikeBatchScheduler` 在 first scheduled slice 消费 pending KV load；多 chunk 后续 slice 不重复收费。
- `BatchAwareReplayEngine` 将 lookup 的 DDR hit token/bytes 写入 request state，并阻止 DDR hit zero-miss 走 immediate finish。
- `ServingLatencyProfile` 对 load-only shape 使用 `ttft_ms=0`，只计 queue 与 KV load。
- 新增 S8-D replay 单测和 streaming E2E。

本轮没有做：

- 没有修改 HBM / DDR / miss token accounting。
- 没有修改 cache lookup / materialization / eviction。
- 没有新增 cache load event。
- 没有新增 request / iteration / capacity sweep KV load typed metrics；这些留给 S8-E。
- 没有实现 overlap、load queue/backpressure、promotion、layerwise load、Ramulator2 / Mooncake online replay。

验证结果：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/scheduler/test_request_state_kv_load.py \
  tests/unit/scheduler/test_vllm_like_scheduler.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/replay/test_step8_kv_load_replay.py \
  tests/unit/streaming/test_streaming_replay.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

结果：`39 passed`。

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_backend_factory.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

结果：`33 passed`。

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_fitted_ttft_backend.py \
  tests/unit/latency/test_formula_backend.py
```

结果：`5 passed`。

```bash
.venv/bin/ruff check ...
git diff --check ...
```

结果：均通过。

能否进入 S8-E：

- 从代码开发角度，S8-D 已具备进入 S8-E 的条件。
- S8-E 应专注 typed metrics / typed result / report/export，只消费 S8-D 产生的 replay result，不重算 KV load 语义。
