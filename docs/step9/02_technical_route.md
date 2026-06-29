# Step9 Technical Route: Chunk Timeline, Compute Wait, KV Load Timing

状态：正式技术路线，已通过评审。

阶段类型：核心仿真器。

改动等级：L3。Step9 会新增 replay/cache/latency timeline mode，但不得修改现有
`batch_aware_hbm_ddr_lru` 的默认行为。

依据：

- `docs/step9/01_source_alignment_and_error_analysis.md`
- `docs/archive/step8/04_kv_load_overlap_and_source_study.md`
- `docs/archive/step8/05_technical_route.md`

评审结论：

- 接受 Step9 属于核心仿真器，改动等级 L3。
- 接受 Step9 不仅实现 progressive full-block visibility，还补齐 Step8 后影响精度的 TTFT timeline 缺口。
- 接受新增 `compute_wait_ms`，用于表示 request 已进入 vLLM / InferTwin replay 后，等待 chunked prefill 组 batch 的时间。
- 接受新增 `kv_load_wait_ms`，用于表示 DDR/CPU KV load 等待时间。
- 接受 `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms` 作为兼容聚合字段。
- 接受后续通过新 mode `batch_aware_hbm_ddr_lru_progressive_timeline` 实现，不修改旧 `batch_aware_hbm_ddr_lru` 默认语义。

## 1. Step 目标

Step9 的目标是让 InferTwin 的 TTFT 与 prefix cache hit 估算从 request-level / iteration-level
粗粒度推进到 chunk timeline 粒度，并补齐 Step8 后仍影响精度的关键 replay 状态。

本 Step 需要实现或设计到可实现状态的核心能力：

1. Chunk-level TTFT composition。
   - 一条 request 的 prefill 由多个 scheduled prefill chunks 组成。
   - request TTFT 由 compute wait、KV load wait、chunk compute contribution 组合。

2. Compute wait state。
   - request 已被 vLLM / InferTwin replay 接收，但因 continuous batching / chunked prefill
     token budget / running-first policy 等原因，暂时没有被选入当前 scheduler iteration。
   - 这段时间不是 gateway queue，也不是实例入口 admission queue，而是 vLLM engine 内部
     chunked prefill 组 batch 等待。

3. KV load timing state。
   - DDR/CPU hit 不再只作为一次 scalar `kv_load_ms` 加到 iteration duration 上。
   - 新 mode 中，request 需要 external/DDR KV 时进入 KV load timeline；load 完成后才可继续
     消费对应 cached tokens 或进入 compute-ready 状态。

4. Minimal KV transfer queue / bandwidth model。
   - Step8 的 `shared_link_sum` 只做同一 iteration 内汇总，不表达跨请求并发传输排队。
   - Step9 建议新增 instance-local deterministic transfer queue，第一版使用
     `shared_link_fifo_v1`。

5. Progressive full-block visibility。
   - miss blocks 不必等整条 request prefill finish 才 materialize。
   - 新 mode 中，每个 scheduled chunk finish 后，newly completed full prefix blocks 可以进入
     本实例 HBM/DDR cache，成为后续 request lookup 候选。

6. Source-aligned error accounting。
   - 明确当前估算与真实 vLLM / vLLM-Ascend / Mooncake 的差异。
   - 对误差给出方向、可计算边界和需要观测的量，不给无校准依据的固定百分比。

## 2. 本 Step 属于核心仿真器还是外围能力

Step9 属于核心仿真器。

它修改的是 replay-facing 状态机、时间线、cache materialization timing 和 typed metrics。

外围能力只能消费 Step9 产出的 typed result，例如 capacity sweep / summary / dashboard。外围能力不得
重新计算：

- compute wait。
- KV load wait。
- chunk compute。
- hit tokens。
- materialization timing。
- TTFT。

## 3. 核心术语

### 3.1 instance admission queue wait

实例入口排队时间。表示 request 到达模型服务实例后，在被 vLLM engine 接收前的等待。

当前 InferTwin V1 不建模该时间：

```text
queue_waiting_ms = 0
```

Step9 不改变这个边界。

### 3.2 compute wait

request 已经进入 InferTwin / vLLM engine replay 状态，但当前没有被 scheduler 选入 iteration 的等待。

典型原因：

- `max_num_batched_tokens` token budget 不够。
- `max_num_seqs` 或 running request 限制。
- vLLM-like running-first policy 让已有 running request 先继续。
- chunked prefill 切片后，请求需要等待下一轮 iteration 才能继续下一个 chunk。
- request 从 KV load wait 返回后，仍需等待下一轮 compute batch admission。

该时间应该计入 TTFT，因为它发生在 request 已经被推理框架接收之后、首 token 产生之前。

建议字段：

```text
compute_wait_ms
```

### 3.3 KV load wait

request 命中 DDR/CPU tier 后，等待 cached KV 被 load 到可计算状态的时间。

该时间也发生在 request TTFT critical path 上，应计入 TTFT。

建议字段：

```text
kv_load_wait_ms
```

### 3.4 scheduler wait

为了兼容旧口径，可以保留聚合字段：

```text
scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
```

但在 Step9 新 mode 中，report 应优先展示拆分字段，避免把 compute wait 与 KV load wait 混在一起。

## 4. TTFT 组成

Step9 新 mode 下，一条 request 的 TTFT 建议定义为：

```text
ttft_ms
  = compute_wait_ms
  + kv_load_wait_ms
  + uncached_prefill_compute_ms
  + modeled_serialization_ms
```

第一版：

```text
modeled_serialization_ms = 0
```

其中：

- `compute_wait_ms`：request 可被 compute 调度但未进入本轮 chunk batch 的等待。
- `kv_load_wait_ms`：request 等待 DDR/CPU KV load 完成的时间。
- `uncached_prefill_compute_ms`：多个 scheduled miss-token chunks 的 compute contribution 之和。
- `modeled_serialization_ms`：预留给 future connector overhead、event-loop overhead 或其他不可归入
  compute/load 的串行时间。

如果保留旧字段：

```text
scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
prefill_compute_ms = uncached_prefill_compute_ms
```

不要再把 `scheduler_wait_ms` 解释成实例入口排队。

## 5. Request Timeline

### 5.1 建议状态

Step9 新 mode 建议使用以下 replay-facing 状态：

```text
PENDING
WAITING_FOR_COMPUTE
WAITING_FOR_KV_LOAD
RUNNING_CHUNK
FINISHED
```

状态含义：

| 状态 | 含义 | 计入 TTFT 的字段 |
| --- | --- | --- |
| `PENDING` | trace arrival time 尚未到达 replay clock | 不计入 |
| `WAITING_FOR_COMPUTE` | 已进入 engine，等待 scheduler 选入 chunk batch | `compute_wait_ms` |
| `WAITING_FOR_KV_LOAD` | 已确认 DDR/CPU hit，等待 KV load 完成 | `kv_load_wait_ms` |
| `RUNNING_CHUNK` | 当前 iteration 中执行一个 prefill chunk | `uncached_prefill_compute_ms` |
| `FINISHED` | request TTFT 完成 | 不再计入 |

### 5.2 状态流

基本状态流：

```text
PENDING
-> WAITING_FOR_COMPUTE
-> RUNNING_CHUNK
-> WAITING_FOR_COMPUTE
-> RUNNING_CHUNK
-> FINISHED
```

带 DDR/CPU hit 的状态流：

```text
PENDING
-> WAITING_FOR_COMPUTE
-> WAITING_FOR_KV_LOAD
-> WAITING_FOR_COMPUTE
-> RUNNING_CHUNK
-> ...
-> FINISHED
```

HBM-only zero-miss：

```text
PENDING
-> WAITING_FOR_COMPUTE
-> FINISHED
```

DDR-only zero-miss：

```text
PENDING
-> WAITING_FOR_COMPUTE
-> WAITING_FOR_KV_LOAD
-> FINISHED
```

说明：

- HBM-only zero-miss 可以保持 immediate finish。
- DDR-only zero-miss 不能无代价 finish，必须经过 KV load wait。
- 如果 request 在某轮 scheduler 中没有被选中，该时间累积到 `compute_wait_ms`。
- 如果 request 被选中但需要 DDR load，则进入 `WAITING_FOR_KV_LOAD`，不把这段时间算作
  compute wait。

## 6. 输入、输出、配置变化

### 6.1 输入变化

Trace CSV 不新增必填字段。

Request params 不新增必填字段。

Step9 继续消费既有 request build 结果：

- prompt tokens。
- prefix block hash chain。
- block conversion/accounting result。
- instance uuid。
- model/runtime profile。
- per-instance TTFT / KV load profile。

### 6.2 配置变化

新增显式 mode，禁止静默改变旧 mode：

```yaml
cache:
  mode: batch_aware_hbm_ddr_lru_progressive_timeline

timeline:
  ttft_granularity: chunk
  compute_wait: explicit_state
  materialization: progressive_full_block
  kv_load_timing: blocking_before_compute
  kv_transfer_queue: shared_link_fifo_v1
  overlap_mode: intra_request_none_v1
```

字段语义：

| 字段 | V1 Step9 建议值 | 语义 |
| --- | --- | --- |
| `ttft_granularity` | `chunk` | TTFT 由多个 chunk/load/wait entry 组合。 |
| `compute_wait` | `explicit_state` | 显式统计 chunked prefill scheduler 等待。 |
| `materialization` | `progressive_full_block` | chunk finish 后 materialize full blocks。 |
| `kv_load_timing` | `blocking_before_compute` | 同一 request 必须等 KV load 完成后才能消费对应 cached tokens。 |
| `kv_transfer_queue` | `shared_link_fifo_v1` | instance-local 确定性 load queue。 |
| `overlap_mode` | `intra_request_none_v1` | 不做同一 request 的 layerwise compute/load overlap。 |

说明：

- `intra_request_none_v1` 不等于整个实例停止计算。一个 request 等 KV load 时，实例可以继续
  调度其他 eligible request；这属于 scheduler state 行为，不是 layerwise overlap 建模。
- 旧 `batch_aware_hbm_ddr_lru` 不读取 `timeline` 配置。

### 6.3 输出变化

请求级 typed metrics 建议新增：

- `ttft_granularity`。
- `timeline_mode`。
- `compute_wait_ms`。
- `kv_load_wait_ms`。
- `scheduler_wait_ms`。
- `uncached_prefill_compute_ms`。
- `modeled_serialization_ms`。
- `chunk_count`。
- `load_event_count`。
- `progressive_materialized_blocks`。
- `progressive_materialized_tokens`。

Iteration / aggregate metrics 建议新增：

- `waiting_for_compute_count`。
- `waiting_for_kv_load_count`。
- `scheduled_chunk_count`。
- `kv_transfer_queue_depth_max`。
- `kv_load_wait_ms_sum`。
- `compute_wait_ms_sum`。

大 trace 默认只输出 aggregate，不默认 dump per-chunk timeline 明细。

## 7. 对核心链路的影响评估

### 7.1 trace schema guard

不改变 trace schema。

核心 reader 继续 fail-fast 拒绝空 `instance_uuid`。无实例 id trace 仍由外围 normalizer 或未来
gateway simulation 显式处理。

### 7.2 request build

不改变 request build 输入。

request build 不预生成 chunk timeline，因为 chunk 边界取决于 runtime scheduler token budget、
KV load state、其他 request 的 running/waiting 状态。

### 7.3 tokenizer / chat template

不改变 tokenizer / chat template。

长请求拒绝仍发生在 tokenizer/request build 阶段。Step9 不做隐式截断。

### 7.4 prefix block hash

不改变 prefix hash 规则。

Step9 只改变 block 的可见时间：

```text
finish-time mode:
  request finish -> materialize all full miss blocks

progressive timeline mode:
  chunk finish -> materialize newly completed full miss blocks
```

partial block 仍不可见。

### 7.5 scheduler replay

这是 Step9 的主要影响面之一。

新增或显式化：

- `WAITING_FOR_COMPUTE`。
- `WAITING_FOR_KV_LOAD`。
- chunk timeline entry。
- scheduler iteration 中未被选中的 eligible request 的 wait accounting。

不改变：

- `batch_size` 仍是 iteration 内 request slice 数。
- `max_num_batched_tokens` 仍是 iteration token budget。
- fixed-routing multi-instance isolation。
- old mode 的 finish-time materialization。

### 7.6 cache lookup / materialization / eviction

新增 progressive materialization policy，但不改变 lookup accounting 规则。

不改变：

- contiguous prefix hit。
- vLLM-like `cached_tokens` accounting。
- CP / PCP / DCP / MTP / EAGLE conversion。
- HBM/DDR LRU stateful policy interface。

改变：

- chunk finish 后可能更早 materialize full blocks。
- eviction 可能更早发生。
- cache event timestamp 需要支持 chunk finish time。

### 7.7 latency backend

新增 timeline composition 层。

建议结构：

```text
ServingLatencyProfile
  -> compute component for scheduled chunk
  -> KVLoadLatencyProfile for load request
  -> KVTransferTimelinePolicy for load wait
  -> RequestTTFTComposer
```

旧 fitted/static backend 仍可作为 compute component。

### 7.8 per-instance isolation

不改变实例隔离。

每个 instance 独立拥有：

- scheduler state。
- waiting-for-compute state。
- waiting-for-kv-load state。
- HBM/DDR cache。
- KV transfer queue。
- latency profile。
- event sink。

### 7.9 typed metrics / typed result

Step9 必须先扩展 typed result，再更新 report。

report/export 只能消费 typed fields，不得重新推导：

- wait states。
- chunk count。
- load wait。
- materialization timing。
- TTFT。

## 8. 与现有 V1 Replay 语义的关系

保留旧 mode：

```text
batch_aware_hbm_lru
batch_aware_hbm_ddr_lru
```

旧 mode 继续保持：

- finish-time materialization。
- Step8 KV load scalar accounting。
- no explicit compute wait state。
- no transfer queue/backpressure。

新增 mode：

```text
batch_aware_hbm_ddr_lru_progressive_timeline
```

新 mode 才启用：

- explicit compute wait。
- explicit KV load wait。
- transfer queue v1。
- chunk-level TTFT。
- progressive full-block materialization。

## 9. 不做什么

Step9 不做：

- gateway routing。
- instance admission queue simulation。
- Decode / TPOT。
- cross-instance pooling。
- SSD tier。
- online Ramulator2 replay。
- online Mooncake TransferEngine replay。
- real KV tensor storage。
- physical KV slot / refcount / pin。
- partial block cache hit。
- complex Hybrid model cache group 仿真。
- same-request layerwise compute/load overlap。

## 10. 风险与边界

主要风险：

1. wait 语义混淆。
   - `compute_wait_ms` 是 vLLM engine 内部 chunked prefill batching wait。
   - `queue_waiting_ms` 仍是实例入口 admission queue wait，当前为 0。

2. timeline 双重计费。
   - time interval 只能属于 compute wait、KV load wait、chunk compute 中的一类。

3. 新 mode 影响旧结果。
   - 必须用 old-mode regression test 保证 `batch_aware_hbm_ddr_lru` 不变。

4. transfer queue 被误解为真实 Mooncake。
   - 第一版只是 deterministic abstraction，不是 TransferEngine 仿真。

5. 大 trace 明细过大。
   - 默认聚合，不默认输出 per-chunk event dump。

6. 精度不能固定百分比承诺。
   - 需要通过误差边界和后续校准 residual 表达。

## 11. 建议 Batch 开发顺序

### S9-A：Route Finalization

文档收口。

- 以 `02_technical_route.md` 为正式路线。
- 明确 compute wait / KV load wait / chunk compute 的边界。
- 确认 mode、state、schema、batch 顺序。

### S9-B：Timeline Schema / Typed Result

新增 timeline 数据结构和 typed metrics。

建议新增：

- `RequestTimelineState`。
- `ChunkTimelineEntry`。
- `KVLoadTimelineEntry`。
- `RequestTimelineSummary`。

不接 replay 主逻辑。

### S9-C：Compute Wait Accounting

在 scheduler replay 中显式统计 `WAITING_FOR_COMPUTE`。

- eligible but not scheduled 的时间计入 `compute_wait_ms`。
- chunk 间等待计入 `compute_wait_ms`。
- 不处理 KV load。
- 不做 progressive materialization。

### S9-D：KV Load Timing State

接入 `WAITING_FOR_KV_LOAD`。

- DDR hit request 进入 load wait。
- HBM-only zero-miss 仍 immediate finish。
- DDR-only zero-miss 需要 load-only finish。

不做 shared-link queue。

### S9-E：KV Transfer Queue / Shared Link v1

新增 instance-local `shared_link_fifo_v1`。

- 计算 `kv_load_wait_ms`。
- 输出 queue depth / wait aggregate。
- 保持 deterministic。

不模拟真实 protocol / priority / thread pool。

### S9-F：Chunk-Level TTFT Composer

组合 request TTFT：

```text
compute_wait_ms + kv_load_wait_ms + uncached_prefill_compute_ms
```

并输出 request-level timeline summary。

### S9-G：Progressive Full-Block Materialization

新增 progressive materialization policy。

- chunk finish 后 materialize newly completed full blocks。
- 同 iteration 事件顺序 deterministic。
- event reason 区分 progressive / finish-time。

### S9-H：Streaming Integration / Report Fields

把新 mode 接入 streaming runner 和 typed aggregate。

- 默认聚合。
- report/export 只消费 typed result。
- 大 trace 不默认输出 per-chunk 明细。

### S9-I：E2E 

阶段验收与归档。

- targeted tests。
- 小 E2E。
- streaming smoke。
- old-mode regression。


## 12. 测试策略

必须覆盖：

- request 已到达但未被选中时，`compute_wait_ms` 增加。
- chunk 间未被选中时，`compute_wait_ms` 增加。
- DDR hit 进入 `WAITING_FOR_KV_LOAD`，不计入 compute wait。
- HBM-only zero-miss 继续 immediate finish。
- DDR-only zero-miss 需要 load-only finish。
- 多个 DDR load 进入 shared-link queue，等待时间 deterministic。
- chunk finish 后 full blocks 可见，后续 request hit 增加。
- partial block 不可见。
- old mode 结果不变。
- 多实例 compute wait / KV load wait / transfer queue 互相隔离。
- streaming path 不构造全量 request list。

建议 E2E：

1. token budget 较小，单 request 被切成多个 chunks。
   - 验证 chunk count 和 chunk 间 compute wait。

2. 多 request 同时到达，token budget 只能选部分 request。
   - 验证未选中 request 的 `compute_wait_ms`。

3. DDR-hit request 与 HBM-hit request 同时到达。
   - 验证 DDR request 的 `kv_load_wait_ms`，HBM request 不受同一 request load 阻塞。

4. 两条相同长 prompt，第二条在第一条 chunk finish 后、request finish 前到达。
   - finish-time mode hit 低。
   - progressive timeline mode 命中已完成 full blocks。

## 13. 是否需要对比 vLLM / vLLM-Ascend / Mooncake

需要。

Step9 每个核心 batch 都应回看以下源码口径：

- vLLM scheduler 如何处理 waiting / running / token budget / external KV waiting。
- vLLM KV cache manager 如何计算 computed blocks 和 cache full blocks。
- vLLM-Ascend CPU/NPU offload 如何通过 stream/event 表达异步 transfer。
- vLLM-Ascend Mooncake P2P 如何按 TP / PCP / DCP / PP 切分 remote blocks。
- Mooncake Store / TransferEngine 如何表达 batch transfer、buffer、replica 和 protocol。

但 Step9 不接 online Ramulator2 / Mooncake replay。

## 14. 需要用户审批的决定

进入代码开发前，需要审批：

1. 是否接受 `02_technical_route.md` 作为 Step9 正式技术路线，`README.md` 只保留索引。
2. 是否接受 Step9 属于核心仿真器，改动等级 L3。
3. 是否接受新增 mode：
   `batch_aware_hbm_ddr_lru_progressive_timeline`。
4. 是否接受旧 `batch_aware_hbm_ddr_lru` 完全保持 Step8 行为。
5. 是否接受新增 `WAITING_FOR_COMPUTE`，并将 chunked prefill 组 batch 等待计入
   `compute_wait_ms`。
6. 是否接受新增 `WAITING_FOR_KV_LOAD`，并将 DDR/CPU hit load 等待计入 `kv_load_wait_ms`。
7. 是否接受 `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms` 作为兼容聚合字段。
8. 是否接受 Step9 v1 新增 instance-local `shared_link_fifo_v1`。
9. 是否接受 Step9 v1 不做 same-request layerwise compute/load overlap。
10. 是否接受 Step9 v1 仍不做 DDR hit promotion、physical slot/refcount/pin、partial block hit。
11. 是否接受 S9-A 到 S9-I 的 batch 顺序。
