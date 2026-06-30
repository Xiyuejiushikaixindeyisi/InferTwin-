# InferTwin V1 与真实 vLLM / vLLM-Ascend / Mooncake 推理链路对比

本文用于交接和评审 InferTwin V1 当前仿真语义。结论来自本地源码阅读：

- InferTwin: `src/infertwin/`
- vLLM: `/home/zhangxiyue/vllm/vllm/`
- vLLM-Ascend: `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/`
- Mooncake: `/home/zhangxiyue/Mooncake/`

本文只描述 V1 已实现能力和真实链路差异，不把未实现能力写成已实现能力。

## 1. 总体结论

InferTwin V1 是面向大型推理服务集群的离线 replay 仿真器。它已经具备：

- trace schema guard、request build、tokenizer / chat template、prefix block hash。
- 固定路由多实例隔离 replay。
- vLLM-like prefill scheduler replay。
- HBM + DDR/CPU prefix cache hit accounting。
- HBM / DDR LRU、cache lookup / store / materialization / eviction signal。
- fitted TTFT、KV load latency accounting、shared-link FIFO wait accounting。
- progressive full-block materialization 和 chunk-level TTFT timeline。
- true streaming 大 trace 主路径。

V1 与真实推理服务的最大差异集中在三类：

1. InferTwin 不部署模型，不执行真实算子和 kernel，TTFT 由 profile / fitted backend / replay timeline 组合得到。
2. InferTwin 不持有真实 KV tensor，只保存 prefix block hash 与轻量 metadata。
3. InferTwin 的通信链路是可解释的 deterministic abstraction，不是真实 Mooncake / HCCL / RDMA / DMA / CPU copy 链路。

因此，InferTwin V1 适合回答离线 replay、prefix cache hit、cache 容量 sweep、实例隔离、DDR/CPU hit accounting 和粗粒度 TTFT 变化趋势问题。不适合直接回答真实 kernel overlap、真实 TransferEngine backpressure、Decode / TPOT、Hybrid KV group、跨实例池化和硬件微架构级读写问题。

## 2. TTFT 建模

### InferTwin 当前做法

InferTwin 的 TTFT 由 replay timeline 和 latency profile 共同决定。

- `ServingLatencyProfile` 的 iteration duration 口径是：

```text
duration_ms = queue_ms + uncached_prefill_compute_ms + kv_load_ms
```

- 默认 queue component 为 0，表示真实机器侧接收前排队暂不建模。
- `FittedTTFTLatencyBackend` 用 token-linear profile 估算 uncached prefill compute。
- `KVLoadLatencyProfile` 支持 zero、token-linear、byte-linear mode。
- Step9 progressive mode 下，请求级 TTFT 由下面字段闭合：

```text
ttft_ms = compute_wait_ms
        + kv_load_wait_ms
        + uncached_prefill_compute_ms
        + unattributed_ttft_ms
```

其中 `unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果。

### 真实 vLLM / vLLM-Ascend 中的 TTFT

真实 TTFT 来自完整在线链路，包括：

- API / gateway / server queue。
- tokenizer / input processor。
- scheduler waiting。
- prefix cache lookup。
- KV transfer / connector load。
- model runner prefill kernel。
- 输出首 token 前的运行时开销。

vLLM v1 scheduler 不是简单的 prefill-only 公式。它通过 `num_computed_tokens` 追赶 `num_tokens_with_spec`，统一覆盖 chunked prefill、prefix caching、spec decode 和 decode scheduling。vLLM-Ascend 还会引入 NPU stream、CPU offload、Mooncake connector、layer-wise load / save 等额外时序。

### 差异和影响

| 维度 | InferTwin V1 | 真实服务 | 影响 |
| --- | --- | --- | --- |
| prefill compute | fitted / static profile | 真实模型算子与 kernel | 可用于趋势估计，不是算子级真值 |
| queue | 默认 0；Step9 记录 compute wait / kv load wait | 多级在线队列 | V1 不解释网关和实例接收前排队 |
| KV load | token / byte linear + FIFO wait | H2D、RDMA、HCCL、DMA、CPU copy 等 | V1 可做粗粒度 latency accounting |
| Decode / TPOT | 未建模 | 真实请求生命周期的一部分 | PD 混部或 decode-heavy 场景需后续新增 |
| overlap | 默认不建模 same-request layerwise overlap | 真实系统可能 compute / transfer overlap | V1 可能高估或误归因部分 DDR hit TTFT |

## 3. Batch 组法

### InferTwin 当前做法

InferTwin 使用 `VllmLikeBatchScheduler` 生成 replay iteration。

核心语义：

- 每个实例独立维护 pending、waiting、running。
- 请求到达时间满足后从 pending 进入 waiting。
- scheduler 优先处理 running，再从 waiting admission。
- 遵守 `max_num_batched_tokens`、`max_num_seqs` 和 chunked prefill token slice。
- `batch_size` 定义为本 iteration 内的 request slice 数。
- DDR hit 且 miss token 为 0 时可以形成 load-only slice。
- 如果 scheduler 产出 empty schedule，replay fail-fast，不静默跳过请求。

### 真实 vLLM 中的 batch

vLLM v1 scheduler 同样先处理 running，再处理 waiting，并在 token budget 和 max running seqs 约束下推进请求。它还包含：

- preemption。
- priority queue 或 FCFS queue。
- long prefill token threshold。
- speculative decode token。
- encoder / multimodal 预算。
- LoRA 约束。
- pipeline parallel 相关状态。
- KVConnector async load 状态，例如 `WAITING_FOR_REMOTE_KVS`。

vLLM 的 scheduler 并没有把 decode 和 prefill 完全拆成两个孤立系统，而是围绕每个 request 的 `num_computed_tokens` 统一调度。

### 差异和影响

InferTwin 的 batch replay 对齐了 prefix-cache 和 chunked-prefill 相关的核心 token budget 语义，但不模拟真实 model runner、preemption、LoRA、encoder、PP、decode-heavy 干扰和 GPU kernel execution。

因此 V1 的 batch 能回答：

- 同一个 trace 在固定实例内如何被分 chunk replay。
- 不同 cache hit 对 scheduled prefill tokens 和 TTFT 的影响。
- streaming trace 下 instances 之间是否隔离。

V1 不能回答：

- decode 对 prefill 的抢占或吞吐影响。
- PP / LoRA / multimodal / speculative decode 的真实调度影响。
- 真实 kernel shape 对 batch latency 的非线性影响。

## 4. Prefix Cache Hit 实现和统计

### InferTwin 当前做法

InferTwin 的 prefix cache hit 是 hash-only block 级仿真。

链路：

1. request build 解析 trace。
2. tokenizer / chat template 得到 token ids。
3. `block_hasher` 生成 prefix block hash chain。
4. cache lookup 在 HBM tier 先查，再在 DDR/CPU tier 查。
5. lookup 只统计连续 prefix full block hit。
6. `cached_token_accounting` 应用 vLLM-like 统计规则：

```text
max_cache_hit_length = prompt_tokens - 1
effective_block_size = runtime_block_size * PCP * DCP
cached_tokens = full matched effective blocks, with optional MTP/EAGLE one-block drop
```

统计字段区分：

- `hbm_hit_tokens`
- `ddr_hit_tokens`
- `miss_tokens`
- `kv_hit_tokens`
- `kv_hit_rate`
- `kv_load_tokens`
- `kv_load_bytes`

### 真实 vLLM 中的 prefix cache hit

vLLM 的 `KVCacheManager.get_computed_blocks()` 会设置：

```text
max_cache_hit_length = request.num_tokens - 1
```

然后由 KV cache coordinator 查找 longest cache hit。FullAttention manager 从 block hashes 左到右扫描，遇到 miss 停止。启用 EAGLE / MTP 时会丢弃最后一个 matched block。DCP / PCP 会放大 effective block size。Hybrid cache coordinator 会按不同 cache group block size 的最小公倍数对齐，因为当前不支持 partial-block cache hit。

vLLM-Ascend 的 CPU offload connector 也有 scheduler-side CPU prefix cache metadata lookup，并使用相同的 `request.num_tokens - 1` 约束。

### 差异和影响

InferTwin 在普通 full-attention prefix cache usage accounting 上尽量对齐 vLLM 规则。主要差异是：

- InferTwin 不保存真实 KV tensor，只保存 block hash 与 metadata。
- InferTwin 当前不完整建模 Hybrid Mamba / sliding window / 多 cache group 的真实 block layout。
- InferTwin 的 DDR/CPU hit 是同实例 pooling tier accounting，不是 Mooncake remote global prefix cache。
- InferTwin 默认不会因为真实 active sequence refcount、pinned block 或 physical slot pressure 改变 lookup 结果。

V1 对 full-attention 模型的 prefix hit 趋势和 capacity sweep 有较好解释力。Hybrid / sparse attention / remote pooling 需要后续新增 cache manager / conversion policy / backend，不应在现有语义中隐式兼容。

## 5. KV Block 分配和管理

### InferTwin 当前做法

InferTwin 的 cache block 是仿真 metadata，不是真实 KV tensor block。

HBM LRU：

- resident block 由 block hash 标识。
- 容量使用 `hbm_capacity_blocks`。
- lookup 返回连续 prefix hit。
- materialize 把 miss full blocks 写入 HBM。
- capacity 超限时调用 stateful eviction policy，当前默认 LRU。
- emit `lookup_hit`、`lookup_miss`、`materialize`、`evict`。

DDR LRU：

- resident block 同样只保存 hash metadata。
- 容量使用 `ddr_capacity_blocks`。
- lookup / store / eviction 独立于 HBM。
- emit `lookup_hit`、`lookup_miss`、`store`、`evict`。

Tiered cache：

- lookup 顺序为 HBM first，然后 DDR over HBM miss blocks。
- materialize 时可同时写 HBM 和 DDR。
- Step9 progressive mode 下，scheduled chunk finish 后 newly completed full miss blocks 可见。
- partial block 仍不可见。

### 真实 vLLM / vLLM-Ascend 中的 block 管理

vLLM 的 `BlockPool` 管理 `KVCacheBlock` 对象、free queue、hash-to-block map、null block 和 events。`SingleTypeKVCacheManager` 维护 request 到 block 的映射。真实 block 管理包含：

- block slot allocation。
- cached block touch。
- `ref_cnt`。
- free queue。
- preemption。
- block eviction before reusing free block。
- request finish 后 reverse-order free。
- KV event `BlockStored` / `BlockRemoved`。

vLLM-Ascend 的 CPU offload path 还维护 CPU block pool、GPU block id、CPU block id、metadata server、load / save stream 和 per-layer copy。

### 差异和影响

InferTwin 不建模真实 serving execution 所需的 physical KV slot allocation。它的 capacity 表达的是 prefix cache residency capacity，而不是模型运行时必须持有的全部 active sequence KV capacity。

这意味着：

- 单个 prompt 大于 cache capacity 时不会 OOM。InferTwin 可以只保留可容纳的 suffix/resident block metadata，后续 prefix hit 可能为 0。
- InferTwin 不通过 refcount / pinned block 保护正在运行的真实 KV tensor。
- InferTwin 不模拟 vLLM 的 preemption。
- InferTwin 的 eviction 只影响后续 prefix cache hit，不影响当前请求能否继续执行。

这是 V1 为离线 replay 做出的明确抽象。若未来要模拟真实 capacity pressure、preemption 或 active KV slot，需要新增 physical slot backend，而不是修改当前 prefix cache residency 语义。

## 6. Replay 链路

### InferTwin 当前 replay 链路

InferTwin 的核心链路如下：

```text
trace row
  -> trace schema guard
  -> request build
  -> tokenizer / chat template
  -> prefix block hash
  -> instance grouping or streaming shard
  -> per-instance replay
  -> pending arrival
  -> waiting lookup frontier
  -> scheduler schedule
  -> latency estimate / transfer queue accounting
  -> running state update
  -> chunk finish / request finish
  -> materialization / store / eviction
  -> typed request metrics / iteration metrics / report
```

内存版 `BatchAwareReplayEngine` 会返回完整 metrics。Streaming 版 `StreamingBatchAwareReplayEngine` 通过 `RequestSource` 和 sink 逐条输出，避免大 trace 全量驻留内存。

实例隔离是核心语义：

- 每个 instance_uuid 有独立 scheduler state。
- 每个 instance_uuid 有独立 HBM / DDR cache。
- 每个 instance_uuid 有独立 latency backend / model runtime defaults resolver。
- 当前没有 gateway routing 仿真。
- 当前没有跨实例 pooling hit。

### 真实推理服务链路

真实链路是在线系统：

```text
client / gateway
  -> service ingress
  -> tokenizer / input processor
  -> scheduler
  -> KV cache manager / connector
  -> model runner kernels
  -> KV transfer / offload / store
  -> output processor
  -> streaming response / metrics
```

真实链路中，时间由 wall-clock、GPU/NPU stream、network transfer、kernel execution 和异步 completion 共同决定。

### 差异和影响

InferTwin 是 deterministic offline replay。它用 trace arrival time 和 fitted duration 推进仿真时间，不运行真实模型，也不等待真实异步 IO。这样可以在没有显卡的情况下做大 trace 离线分析，但不能替代真实在线服务压测。

## 7. Cache Lookup / Store / Eviction 信号

### InferTwin 当前信号

InferTwin 使用统一 `CacheEvent` schema：

- `lookup_hit`
- `lookup_miss`
- `materialize`
- `store`
- `evict`

核心字段包括：

- timestamp。
- instance_uuid。
- request_id。
- block_key。
- block_index。
- token_count。
- cache_tier。
- source_tier / target_tier。
- load_tokens / store_tokens。
- hbm / ddr used blocks。
- hbm / ddr capacity blocks。
- eviction_policy。
- reason。

事件顺序由 replay 逻辑确定：

1. scheduler-considered waiting frontier 做 bounded lookup。
2. lookup 产生 HBM / DDR hit 或 miss event。
3. iteration 完成时，根据 materialization policy 写入 full blocks。
4. capacity 超限时 eviction policy 选择 victim，产生 evict event。
5. tiered mode 下 HBM materialize 与 DDR store 都可以产生事件。

### 真实系统中的信号

vLLM 在 KV event enabled 时可产生类似 `BlockStored`、`BlockRemoved`、`AllBlocksCleared` 的 KV event，medium 通常是 GPU。vLLM-Ascend 的 CPU offload / Mooncake connector 有各自的 prefix cache stats、metadata、transfer metrics 和 per-layer load / save 行为。Mooncake store 侧更关注 object get / put、replica query、transfer status、timeout、admission queue 等信号。

### 差异和影响

InferTwin 的 cache event 是仿真器内部稳定 schema，面向 CSV/report/debug。它不是 vLLM KV event protocol，也不是 Mooncake telemetry。

这是合理边界。若未来需要对接真实观测，可以新增 exporter / adapter，把 InferTwin typed event 映射成 vLLM-like 或 Mooncake-like 视图，而不应该让 report 层反向改变核心 replay。

## 8. 通信链路

### InferTwin 当前做法

InferTwin V1 的通信链路是 latency accounting abstraction。

- DDR/CPU hit 产生 `kv_load_tokens` 和可选 `kv_load_bytes`。
- `KVLoadLatencyProfile` 把 tokens 或 bytes 转换成 `kv_load_ms`。
- Step9 的 `SharedLinkFIFOTransferQueue` 记录 instance-local FIFO shared-link wait。
- 默认不建模 same-request compute / load overlap。
- 不执行真实 transfer。
- 不接 Ramulator2 / Mooncake online replay。

### 真实 vLLM-Ascend / Mooncake 链路

vLLM-Ascend CPU/NPU offload path 使用 NPU stream 和 `torch.ops._C_ascend.swap_blocks` 在 CPU/NPU 之间搬运 KV blocks，也有 layer-wise load / wait / save 逻辑。Mooncake connector 初始化 `TransferEngine`，注册内存，并在 `protocol=ascend` 下走相应传输后端。Mooncake store `batch_get_into_multi_buffers()` 会：

1. query object metadata。
2. 选择 replica。
3. 区分 MEMORY、LOCAL_DISK、DISK 等路径。
4. 构造 batch transfer。
5. 通过 TransferEngine submit / poll status。
6. 处理 timeout 和 partial result。

Mooncake TransferEngine 侧存在 batch submit、status polling、segment open、memory registration、admission queue 等机制。真实链路可能涉及 RDMA、fabric memory、local disk、disk fallback、GPU scatter-gather buffer、CPU staging、HCCL all-to-all 或其他硬件相关路径，具体取决于部署和 connector。

### 差异和影响

InferTwin 当前不表达：

- 真实 transfer protocol。
- replica placement。
- object pin / lease。
- remote memory vs local disk vs disk fallback。
- per-layer load completion。
- load completion event。
- transfer priority。
- backpressure。
- same-request load / compute overlap。

V1 的通信模型适合做粗粒度“DDR hit 是否带来额外 TTFT”和“多个请求共享链路等待”的趋势分析。若要研究真实通信系统，需要新增 KV transfer timeline backend、Mooncake adapter、Ramulator2 calibration adapter 或 hardware profile，不应修改当前默认语义。

## 9. 已对齐点与未对齐点汇总

| 链路 | InferTwin V1 对齐程度 | 当前差异 | 是否需要修改 |
| --- | --- | --- | --- |
| TTFT | 中等 | fitted / profile，不是真实 kernel | V1 可接受；V2 可新增 calibration / overlap backend |
| batch | 中等 | 无 decode、preemption、LoRA、encoder、PP | V1 可接受；decode-heavy 或混部场景需新增 scheduler mode |
| prefix cache hit | 较高，针对 full-attention | Hybrid / sparse / remote pooling 未完整建模 | full-attention V1 可接受；Hybrid/sparse 需新 cache manager |
| KV block management | 中等 | hash metadata，不是真实 tensor slot | V1 可接受；物理 capacity / preemption 需新 backend |
| cache event | 中等 | InferTwin typed event，不是 vLLM/Mooncake 原生 telemetry | 保持稳定；可新增 exporter |
| DDR/CPU pooling | 中等 | 同实例 DDR tier，不是真实 Mooncake remote global cache | Step7/8/9 V1 可接受；remote pooling 后续新增 |
| communication | 低到中 | linear profile + FIFO，不是真实 TransferEngine | V1 可接受；通信研究需新增 adapter/backend |
| replay | 较高，针对离线固定路由 | 不模拟在线 gateway 和真实硬件 | 核心定位正确；gateway 是外围/后续能力 |

## 10. V1 使用边界

InferTwin V1 可以用于：

- 大 trace streaming replay。
- 单实例或多实例固定路由 replay。
- 相同模型多实例 replay。
- 不同模型实例使用不同 latency profile 的 replay。
- HBM / DDR prefix cache hit accounting。
- cache capacity sweep。
- fitted TTFT 与 KV load latency 趋势分析。
- progressive full-block visibility 对长 prefill cache reuse 的影响分析。

InferTwin V1 不应用于直接得出：

- 真实 GPU/NPU kernel 级性能。
- 真实 Mooncake / RDMA / HCCL 传输性能。
- Decode / TPOT 影响。
- gateway routing 策略优劣。
- 跨实例 remote pooling 命中收益。
- Hybrid Mamba / sparse attention 的真实 cache group 行为。
- 物理 KV slot pressure、preemption 和 OOM 行为。

这些能力应在后续阶段通过新 mode、新 backend、新 policy、新 adapter 或新 schema 引入，避免污染当前稳定 replay 语义。

