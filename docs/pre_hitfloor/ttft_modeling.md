# HitFloor 前置：TTFT 建模口径

## 1. 文档定位

本文沉淀 HitFloor 前置讨论中关于 TTFT 的统一命名、定义和 InferTwin 当前实现映射。

第一版 HitFloor 不追求真实服务中所有微小延迟的精确建模。它关注的是：

```text
prefix cache hit / miss
-> uncached token compute
-> HBM / DDR tier hit
-> KV load wait / service
-> chunked prefill timeline
-> TTFT / P90 TTFT 趋势
```

因此，第一版 TTFT 不单独建模 first token overhead。模型真实处理 uncached token 并产生 first token 的时间，统一由 chunk 粒度 prefill compute 表达。

## 2. 统一 TTFT 组成

HitFloor 第一版的 TTFT 统一口径为：

```text
ttft_ms =
  scheduler_compute_wait_ms
  + sum(chunk_prefill_compute_ms)
  + kv_load_wait_ms
  + kv_load_service_ms
```

其中：

```text
first_token_overhead_ms = 0
```

如果为了 debug 或闭合 replay timeline 需要保留残差，只能使用：

```text
unattributed_ttft_ms
```

并且必须明确：`unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果，不应作为 HitFloor 主要分析指标。

## 3. 四个核心时间项

### 3.1 `scheduler_compute_wait_ms`

定义：

```text
request 已经进入实例内 replay / scheduler 可考虑范围，
但由于 chunked prefill、continuous batching、token budget 或 running set，
暂时没有被选中执行 uncached prefill compute 的等待时间。
```

它不包含：

- gateway routing wait。
- 模型服务接收请求前的 machine-side queue。
- KV load transfer queue wait。
- KV load service time。
- prefill compute service time。

对 TTFT 的影响：

- prefix hit 越高，uncached tokens 越少，chunk 数可能越少。
- chunk 数越少，请求经历的 scheduler compute wait 通常越少。
- 高并发、长 prompt、较小 `max_num_batched_tokens` 会增加 scheduler compute wait。

InferTwin 当前映射：

```text
current field: compute_wait_ms
target name: scheduler_compute_wait_ms
```

### 3.2 `chunk_prefill_compute_ms`

定义：

```text
一个 scheduler iteration 中，
被选中的 request slice 对 uncached tokens 执行 prefill compute 的服务时间。
```

request 级聚合：

```text
uncached_prefill_compute_ms = sum(chunk_prefill_compute_ms)
```

它只计算 miss / uncached tokens 的 compute，不计算 HBM/DDR 已命中 tokens 的 recompute。

对 TTFT 的影响：

- HBM hit 增加：`uncached_prefill_compute_ms` 减少。
- DDR hit 增加：`uncached_prefill_compute_ms` 减少，但 `kv_load_*` 增加。
- miss 增加：`uncached_prefill_compute_ms` 增加，chunk 数也可能增加。

InferTwin 当前映射：

```text
current request field: uncached_prefill_compute_ms
current internal accumulated field: prefill_compute_ms
target aggregate name: uncached_prefill_compute_ms
target per-chunk name: chunk_prefill_compute_ms
```

当前 InferTwin 不一定保留每个 request 的完整 per-chunk 明细；但 request / iteration metrics 已能表达 chunk 粒度 timeline 和 request 级 compute 聚合。

### 3.3 `kv_load_wait_ms`

定义：

```text
DDR / CPU / remote KV load 已经 ready，
但 transfer link、queue、stream 或资源尚不可用，
等待真正 load service 开始的时间。
```

它不包含实际搬运 KV 的服务时间。

对 TTFT 的影响：

- 多个 request 同时发生 DDR hit 时，可能共享 link / queue，增加 `kv_load_wait_ms`。
- DDR hit tokens 越多，不一定 wait 越长；wait 主要取决于 transfer queue 竞争。
- HitFloor 判断 DDR hit 是否有收益时必须使用：

```text
saved_compute_ms > kv_load_wait_ms + kv_load_service_ms
```

目标口径：

```text
kv_load_wait_ms = transfer_start_time_ms - transfer_ready_time_ms
```

InferTwin 当前状态：

```text
当前代码中的 kv_load_wait_ms 存在命名风险。
Step9 的 transfer queue 会产生 queue_wait_ms、transfer_ms、elapsed_ms。
当前 state.record_kv_load_event(...) 记录的是 elapsed_ms，
也就是 queue_wait_ms + transfer_ms。
```

因此，pre_hitfloor 需要修正或显式拆分：

```text
target kv_load_wait_ms = queue_wait_ms
target kv_load_service_ms = transfer_ms
target kv_load_total_ms = elapsed_ms
```

这属于 HitFloor 前置的指标口径修正，不应在 report 层靠解释绕过。

### 3.4 `kv_load_service_ms`

定义：

```text
真正执行 KV load / transfer / copy 的服务时间。
```

例如：

- DDR / CPU -> HBM。
- remote KV store -> local HBM。
- future Mooncake / TransferEngine load。

第一版 HitFloor 只要求 fitted/static 估算，不要求真实 Ramulator2 / Mooncake online replay。

可由以下 profile 估算：

```text
mode=zero:
  kv_load_service_ms = 0

mode=token_linear_v1:
  kv_load_service_ms =
    ddr_fixed_overhead_ms
    + ddr_ms_per_cached_token * ddr_hit_tokens

mode=byte_linear_v1:
  kv_load_service_ms =
    ddr_fixed_overhead_ms
    + ddr_ms_per_byte * ddr_hit_bytes
```

InferTwin 当前映射：

```text
current field: kv_load_ms
target name: kv_load_service_ms
compatibility rule: kv_load_ms == kv_load_service_ms
```

后续报告中应同时输出：

```text
kv_load_wait_ms
kv_load_service_ms
kv_load_total_ms = kv_load_wait_ms + kv_load_service_ms
```

并保留 `kv_load_ms` 作为兼容字段。

## 4. First Token Overhead 口径

第一版 HitFloor 不单独建模：

```text
first_token_overhead_ms = 0
```

原因：

- HitFloor 当前核心目标是 cache capacity / tier hit / TTFT 趋势，而不是毫秒级真实 serving latency 复刻。
- 模型处理 uncached token 并产生 first token 的主时间已经由 chunk prefill compute 表达。
- first token sampling、stream flush、RPC 固定开销相对 HitFloor 关注的长 prompt prefill / KV load 来说不是第一优先级。

如果后续需要更精确 TTFT，可以新增：

```text
first_token_overhead_ms
```

或让 fitted TTFT backend 的 intercept 吸收该项。但这不应影响当前 HitFloor 的主建模。

## 5. Prefix Cache Hit 对 TTFT 的影响

### 5.1 HBM Hit

HBM hit 的影响：

```text
hbm_hit_tokens 增加
-> uncached_tokens 减少
-> chunk_prefill_compute_ms 总和减少
-> chunk 数可能减少
-> scheduler_compute_wait_ms 可能减少
-> TTFT 下降
```

第一版可认为 HBM hit 不产生显式 KV load service。

### 5.2 DDR / CPU Hit

DDR / CPU hit 的影响：

```text
ddr_hit_tokens 增加
-> uncached_tokens 减少
-> chunk_prefill_compute_ms 总和减少
-> 但 kv_load_service_ms 增加
-> 在共享 link / queue 下 kv_load_wait_ms 可能增加
```

DDR hit 是否带来 TTFT 收益取决于：

```text
saved_compute_ms > kv_load_wait_ms + kv_load_service_ms
```

因此 HitFloor 不能只看 total hit rate，必须区分：

```text
hbm_hit_tokens
ddr_hit_tokens
miss_tokens
kv_load_wait_ms
kv_load_service_ms
```

### 5.3 Miss

miss 的影响：

```text
miss_tokens 增加
-> uncached_tokens 增加
-> chunk_prefill_compute_ms 总和增加
-> chunk 数可能增加
-> scheduler_compute_wait_ms 可能增加
-> TTFT 上升
```

## 6. 与 InferTwin 当前实现的关系

当前 InferTwin 已实现：

- chunk-level TTFT timeline。
- `compute_wait_ms`。
- `uncached_prefill_compute_ms`。
- `kv_load_ms`。
- `kv_load_wait_ms`。
- `unattributed_ttft_ms`。

但进入 HitFloor 前，需要统一命名并修正解释：

| 目标字段 | 当前字段 | 当前状态 |
| --- | --- | --- |
| `scheduler_compute_wait_ms` | `compute_wait_ms` | 已统计，建议后续输出中使用更明确名称或增加 alias。 |
| `uncached_prefill_compute_ms` | `prefill_compute_ms` / `uncached_prefill_compute_ms` | 已统计，文档中应统一为 uncached prefill compute。 |
| `chunk_prefill_compute_ms` | iteration-level `prefill_compute_ms` | iteration 级已有，request 级以 sum 表达。 |
| `kv_load_service_ms` | `kv_load_ms` | 已统计，建议新增 alias；`kv_load_ms` 保持兼容。 |
| `kv_load_wait_ms` | `kv_load_wait_ms` | 当前命名存在风险，需要确保只表示 queue wait，不包含 service。 |
| `kv_load_total_ms` | 无显式字段 | 需要新增或在 typed result/report 中明确计算。 |
| `first_token_overhead_ms` | 无 | 第一版固定为 0，不建模。 |
| `unattributed_ttft_ms` | `unattributed_ttft_ms` | 仅作为 replay 残差 / debug 字段，不是主物理指标。 |

## 7. 系统侧最关键机制

本节从真实 vLLM / vLLM-Ascend / Mooncake 推理服务角度，说明哪些机制最影响 TTFT，以及它们如何进入 HitFloor 的时间组成。

### 7.1 直观流程图

```text
Trace arrival / fixed-routed instance
  |
  | 影响：request 何时进入实例 replay；当前不建模 gateway routing
  v
Prefix cache lookup
  |
  | 影响：决定 HBM hit / DDR hit / miss
  | HBM hit -> 减少 compute，不增加 load
  | DDR hit -> 减少 compute，但增加 KV load wait/service
  | miss    -> 增加 uncached prefill compute
  v
Chunked prefill scheduling
  |
  | 影响：request 是否本轮被选中、被切多大 chunk
  | 未被选中 -> scheduler_compute_wait_ms 增加
  v
Uncached chunk prefill compute
  |
  | 影响：sum(chunk_prefill_compute_ms)
  | prefix hit 越高，通常 chunk compute 越少
  v
KV load timeline for DDR / CPU hit
  |
  | 影响：kv_load_wait_ms + kv_load_service_ms
  | 多请求共享 link 时 wait 可能变大
  v
Request first token ready
  |
  | 第一版：first_token_overhead_ms = 0
  v
TTFT
```

### 7.2 机制影响表

| 机制 | 直接影响的时间项 | 如何影响 TTFT | HitFloor 优先级 |
| --- | --- | --- | --- |
| prefix cache lookup result | `chunk_prefill_compute_ms`, `kv_load_service_ms` | HBM/DDR hit 减少 uncached compute；DDR hit 额外产生 KV load。 | P0 |
| prefix block visibility | `chunk_prefill_compute_ms`, `scheduler_compute_wait_ms` | block 更早可见会提高 hit，减少后续请求 compute 和 chunk 轮数。 | P0 |
| chunked prefill / scheduler token budget | `scheduler_compute_wait_ms`, `chunk_prefill_compute_ms` | 决定 request 被切成几轮、每轮是否被选中。 | P0 |
| active KV occupancy | 间接影响全部 TTFT components | active KV 挤压 HBM prefix cache，改变 HBM/DDR/miss 分布，进一步改变 compute/load。 | P0 |
| HBM / DDR capacity | `chunk_prefill_compute_ms`, `kv_load_service_ms` | 容量决定 hit tier；HBM hit 减 compute，DDR hit 减 compute 但加 load。 | P0 |
| eviction / touch / keepalive | `chunk_prefill_compute_ms`, `kv_load_service_ms` | 决定 lookup 时 block 是否仍 resident，以及在 HBM 还是 DDR。 | P0 |
| KV load queue / shared link | `kv_load_wait_ms` | 多个 DDR hit 同时 load 时会排队，增加 TTFT。 | P0 |
| KV load service profile | `kv_load_service_ms` | 决定 DDR hit 的 load cost，影响 DDR hit 是否真正收益。 | P0/P1 |
| compute / transfer overlap | 组合语义 | 真实系统可能 overlap；当前默认不 overlap，可能高估 DDR-heavy TTFT。 | P2 |
| kernel shape / batch latency 非线性 | `chunk_prefill_compute_ms` | 真实 prefill latency 不一定线性；第一版可用 fitted chunk compute 近似。 | P2 |
| Decode / TPOT | 间接影响 active KV / scheduler | decode-heavy 或 PD 混部场景会影响 active KV 和 batch contention。 | P2 |
| first token overhead | 固定小项 | 第一版设为 0，不进入主公式。 | 不阻塞 |

### 7.3 对 HitFloor 最关键的 TTFT 机制

HitFloor 第一版最需要准确表达的是：

```text
prefix hit tier
-> uncached prefill compute 是否减少
-> DDR hit 是否引入 load wait/service
-> chunked prefill 是否产生 scheduler compute wait
```

因此，优先级最高的系统机制是：

```text
P0:
  prefix block visibility
  active KV occupancy
  chunked prefill scheduling
  HBM / DDR capacity
  cache lifecycle / eviction / touch
  KV load wait/service split

P1:
  DDR load profile calibration
  HitFloor benefit explanation
  hot prefix / LCP chain analytics

P2:
  compute / transfer overlap
  kernel shape nonlinearity
  Decode / TPOT
  first token overhead refinement
```

### 7.4 当前 InferTwin 与真实系统的边界

当前 InferTwin 已经覆盖：

- chunk-level timeline。
- scheduler compute wait accounting。
- uncached prefill compute attribution。
- DDR / CPU hit 的 KV load service time。
- deterministic shared-link KV transfer queue accounting。
- progressive full-block visibility。

但仍存在关键边界：

- active KV occupancy 尚未进入 HBM prefix capacity。
- `kv_load_wait_ms` / `kv_load_service_ms` 的命名和统计需要修正。
- 默认不建模 same-request compute / transfer overlap。
- 不建模真实 TransferEngine / Mooncake online replay。
- 不建模 kernel shape 的复杂非线性。
- 不建模 Decode / TPOT 对 active KV 和 batch 的影响。
- first token overhead 第一版固定为 0。

这些边界不会阻止 HitFloor 第一版开发，但会决定 HitFloor 第一版的解释口径：它应优先保证 HBM/DDR/miss 分布和 TTFT component 方向正确，而不是承诺毫秒级真实线上 TTFT。

## 8. HitFloor 第一版建议输出

HitFloor 表中建议至少包含：

```text
scope
instance_uuid
hbm_capacity_blocks
ddr_capacity_blocks
hbm_hit_tokens
ddr_hit_tokens
miss_tokens
hbm_hit_rate
ddr_hit_rate
miss_rate
p90_ttft_ms
p90_scheduler_compute_wait_ms
p90_uncached_prefill_compute_ms
p90_kv_load_wait_ms
p90_kv_load_service_ms
p90_kv_load_total_ms
```

其中：

```text
p90_kv_load_total_ms
= p90 或 request-level aggregation of (kv_load_wait_ms + kv_load_service_ms)
```

具体选择 “先逐 request 求 total 再算 P90”，还是 “P90 wait + P90 service”，需要在 HitFloor 外围能力方案中单独确定。更推荐：

```text
request_kv_load_total_ms = request_kv_load_wait_ms + request_kv_load_service_ms
p90_kv_load_total_ms = percentile(request_kv_load_total_ms, 90)
```

## 9. 后续需要修正的优先项

进入 HitFloor 前，TTFT 相关修正应分成三类：

1. P0：必须修正，否则 HitFloor 会把 TTFT 组成解释错。
2. P1：建议修正，否则 HitFloor 趋势解释能力不足。
3. P2：未来精细化，不阻塞 HitFloor 第一版。

### P0-1：KV Load 时间字段拆分与命名修正

问题：

当前 InferTwin 已有：

```text
kv_load_ms
kv_load_wait_ms
```

但语义需要收紧：

- `kv_load_ms` 实际表示 KV load service time。
- 当前 `kv_load_wait_ms` 存在命名风险，历史实现中可能记录的是 transfer elapsed，也就是 wait + service。

目标修正：

```text
kv_load_service_ms = transfer_ms
kv_load_wait_ms = transfer_start_time_ms - transfer_ready_time_ms
kv_load_total_ms = kv_load_wait_ms + kv_load_service_ms
```

兼容策略：

```text
kv_load_ms = kv_load_service_ms
```

影响：

- 不改变 prefix cache hit。
- 不改变 HBM / DDR / miss token accounting。
- 可能改变 typed metrics 字段名和 report 输出。
- 如果当前 request-level `kv_load_wait_ms` 确实包含 service，需要修正统计逻辑；否则 HitFloor 会重复计算 DDR load cost。

验收标准：

- 一个无排队 DDR load：`wait=0`，`service>0`，`total=service`。
- 两个并发 DDR load 共享 link：第二个 request `wait>0`，`service>0`，`total=wait+service`。
- `kv_load_ms` 保持等于 `kv_load_service_ms`。

### P0-2：统一 Scheduler Compute Wait 命名

问题：

当前字段名是：

```text
compute_wait_ms
```

但在 HitFloor 语境中容易被误解成硬件 compute 等待或 queue waiting。

目标修正：

```text
scheduler_compute_wait_ms = compute_wait_ms
```

定义必须固定为：

```text
request 已经进入实例 replay / scheduler 可考虑范围，
但由于 chunked prefill、continuous batching、token budget 或 running set，
暂时没有被选中执行 uncached prefill compute 的等待时间。
```

边界：

- 不是 gateway queue。
- 不是模型服务接收前排队。
- 不是 KV load queue。
- 不是 prefill compute service。

影响：

- 不改变 replay 结果。
- 主要影响 typed result、report、summary 和文档。

验收标准：

- 输出中能同时保留兼容字段或清楚映射。
- HitFloor report 使用 `scheduler_compute_wait_ms`。

### P0-3：First Token Overhead 固定为 0

问题：

真实系统中可能存在 sampling、stream flush、RPC、framework bookkeeping 等 first token overhead。但第一版 HitFloor 不需要精确到这一层。

目标修正：

```text
first_token_overhead_ms = 0
```

原则：

- 不新增业务字段参与 TTFT 主公式。
- 不把 `unattributed_ttft_ms` 解释成 first token overhead。
- 不让 fitted backend 的 intercept 在文档里被强解释为 first token overhead。

影响：

- 简化第一版 TTFT 口径。
- 减少用户误以为 InferTwin 已精细建模真实首 token 采样和网络返回。

验收标准：

- 文档和 report 不输出单独 first token overhead，或明确为 0 / not modeled。

### P0-4：`unattributed_ttft_ms` 降级为 Debug / Residual 字段

问题：

`unattributed_ttft_ms` 容易被误解为：

- compute / load overlap。
- first token overhead。
- serialization overhead。
- 真实系统中某个物理阶段。

这些解释都不应该成立。

目标修正：

```text
unattributed_ttft_ms = replay 粒度残差
```

它只用于：

- debug replay timeline 是否闭合。
- 暴露当前粒度无法归因的残差。
- 防止 TTFT 分解出现负值或不守恒。

影响：

- 不作为 HitFloor 主指标。
- 不参与 DDR hit 是否有收益的判断。

验收标准：

- 文档、summary、handoff 中统一称为 residual / debug。
- 不再使用 `overlap_or_residual_ms` 这类容易混淆的名字。

### P1-1：Chunk Prefill Compute 口径与 HitFloor 输出对齐

问题：

HitFloor 关心的是 prefix hit 如何减少 uncached prefill compute。

当前 InferTwin 已有：

```text
uncached_prefill_compute_ms
chunk_count
iteration-level prefill_compute_ms
```

但 HitFloor report 应避免把它写成泛化的 `prefill_time`。

目标修正：

```text
sum(chunk_prefill_compute_ms) = uncached_prefill_compute_ms
```

输出建议：

```text
p90_uncached_prefill_compute_ms
avg_chunk_count
p90_chunk_count
```

影响：

- 不改变 replay。
- 提升 HitFloor 对“为什么 prefix hit 降低 TTFT”的解释能力。

验收标准：

- HBM hit 增加时，`uncached_prefill_compute_ms` 应下降。
- DDR hit 增加时，`uncached_prefill_compute_ms` 下降，但 `kv_load_*` 上升。
- miss 增加时，`uncached_prefill_compute_ms` 上升。

### P1-2：Prefix Hit 对 TTFT 的收益解释

问题：

HitFloor 不能只输出 hit rate。尤其 DDR hit 不一定总是收益。

目标修正：

对每个 scope / capacity 输出或 summary 中解释：

```text
saved_compute_ms_from_hbm_hit
saved_compute_ms_from_ddr_hit
ddr_load_cost_ms = kv_load_wait_ms + kv_load_service_ms
ddr_net_benefit_ms = saved_compute_ms_from_ddr_hit - ddr_load_cost_ms
```

第一版如果不直接输出这些字段，也必须在 summary 中说明判断口径：

```text
DDR hit 有收益 iff saved_compute_ms > kv_load_wait_ms + kv_load_service_ms
```

影响：

- 属于 HitFloor 外围能力解释层。
- 只能消费 core typed result，不得重新 replay。

验收标准：

- summary 不再把 total hit rate 当成唯一指标。
- HBM hit 和 DDR hit 分开解释。

### P1-3：Active KV / Batch 对 TTFT 的间接影响进入前置讨论

问题：

prefix hit 对 TTFT 的影响不仅来自 compute token 减少，还来自：

```text
batch / chunked prefill
-> active KV occupancy
-> HBM prefix cache capacity
-> HBM hit / DDR hit / miss 分布
-> TTFT
```

目标修正：

在 HitFloor 前置技术路线中，把 active KV occupancy-aware HBM capacity 放到 TTFT 解释链路里，而不只是 prefix cache hit 解释链路里。

影响：

- 这是核心 replay 潜在 L3 改动。
- 不应在 report 层用经验公式补偿。

验收标准：

- high-concurrency synthetic trace 下，active-aware 模式应能改变 HBM / DDR / miss 分布，并进一步影响 TTFT components。

### P2-1：Compute / Transfer Overlap Backend

问题：

真实系统中 DDR / CPU / remote KV load 可能与部分 prefill compute overlap。

当前第一版使用：

```text
overlap_mode = none_v1
ttft includes kv_load_wait_ms + kv_load_service_ms
```

这是保守口径，可能高估 DDR-heavy 场景 TTFT。

未来修正：

新增显式 backend / policy，而不是改默认行为：

```text
overlap_mode=max_compute_load_v1
overlap_mode=layer_pipeline_v1
```

前置条件：

- 更细粒度的 KV load shape。
- layer / chunk 级 compute profile。
- 明确 transfer timeline。

### P2-2：First Token Overhead 精细建模

第一版固定为 0。未来只有在以下情况才需要打开：

- 真实线上 TTFT 校准显示 fixed overhead 占比明显。
- trace 以短 prompt 为主，uncached prefill compute 很小。
- 需要评估 gateway / RPC / streaming flush 对 TTFT 的影响。

实现方式：

```text
first_token_overhead_backend
或 fitted_ttft intercept calibration
```

默认仍应关闭。

### P2-3：Decode / TPOT 对 TTFT 的间接影响

Decode / TPOT 不是第一版 HitFloor 主线，但在 PD 混部或 decode-heavy 场景会通过 active KV 和 batch contention 影响 TTFT。

开启条件：

- 输入 trace 有 output token count。
- 目标部署是 PD 混部或 decode-heavy。
- 明确需要评估 decode 对 active KV / scheduler 的影响。

实现方式：

- 新增 decode-aware replay mode。
- 新增 active decode KV occupancy。
- 新增 TPOT latency component。
