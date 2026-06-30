# Prefix Cache Hit 影响因素与 HitFloor 前置优先级

## 1. 文档定位

本文沉淀 HitFloor 前置讨论中关于真实系统 prefix cache hit 的关键判断。

HitFloor 的核心不是单纯拟合 TTFT，而是尽可能复刻真实推理服务中的 prefix cache 复用链：

```text
trace 中存在可复用前缀
-> token / block accounting 后形成可复用 block chain
-> block 在真实系统中生成、可见、保活、驱逐
-> lookup 时仍在 HBM / DDR / remote tier 中 resident
-> 命中结果进入 TTFT 组成
```

因此，HitFloor 前置条件的优先级应围绕 prefix cache hit 的真实决定因素重新排序，而不是只围绕配置 guard 和报表字段。

## 2. Prefix Cache Hit 的两类决定因素

Prefix cache hit 的影响因素可以分为两大类：

1. 真实 trace。
2. vLLM / vLLM-Ascend / Mooncake 等真实推理服务系统。

### 2.1 真实 Trace：决定复用机会的上限

真实 trace 决定了 prefix cache hit 的机会空间：

- 一条请求与历史请求共享前缀的长度。
- 热共享前缀的数量。
- 同一 prefix block chain 的 reuse 间隔。
- session / agent workflow 中前缀如何增长、缩短、分叉。

InferTwin 直接使用真实 trace，因此 trace 分布本身不需要模拟。但必须把 trace 中的共享前缀转换成接近真实 vLLM 的 block-level 复用机会。

需要区分两种上限：

```text
raw LCP opportunity:
  文本 / messages 层面的共享前缀机会

realizable block-level LCP opportunity:
  tokenizer + chat template + runtime block size + cached_tokens accounting 后，
  真正可能被 vLLM prefix cache 复用的 full block chain
```

HitFloor 关心的是第二种。

### 2.2 真实推理服务系统：决定真实命中

真实系统决定 prefix block 的生命周期、可见时间、命中方式和 tier residency。

也就是说，真实 hit 不是简单的：

```text
request A 和 request B 有相同前缀
```

而是：

```text
request A 生成的 block hash chain
在 request B lookup 时
是否已经 visible
是否仍然 resident
是否在 HBM / DDR / remote tier
是否受到 active KV pressure、eviction、pooling mode 影响
```

因此，HitFloor 的准确性主要取决于是否复刻真实系统中的 prefix cache 复用链。

## 3. Prefix Cache Hit 影响因素速览

本节给出一个更直观的判断：哪些因素会影响 prefix cache hit，以及它们是直接影响还是间接影响。

### 3.1 直观流程图

```text
真实 trace
  |
  | 直接影响：有没有共享前缀、共享多长、多久复用一次
  v
Post-template Token LCP / Block Hash Chain LCP
  |
  | 直接影响：tokenizer、chat template、runtime block size、CP/MTP accounting
  v
可复用 full blocks 上限
  |
  | 直接影响：block 何时生成、何时 visible、是否 full block 对齐
  v
Lookup 时刻可见的 prefix blocks
  |
  | 直接影响：HBM/DDR capacity、LRU/touch/eviction、pooling mode
  | 间接影响：batch/chunked prefill -> active KV occupancy -> 可用 cache capacity
  v
Lookup 时仍 resident 的 tier blocks
  |
  | 直接影响：HBM hit / DDR hit / miss
  | 间接影响：DDR load wait/service 改变 TTFT，但不改变已发生的 hit 事实
  v
Prefix cache hit result
  |
  | 直接影响：uncached tokens 和 kv_load tokens
  | 间接影响：TTFT、P90 TTFT、HitFloor capacity 判断
  v
HitFloor 输出
```

核心理解：

```text
trace 决定“有没有机会 hit”
block accounting 决定“最多能 hit 多少”
visibility 决定“lookup 时能不能看见”
capacity / lifecycle 决定“看见时还在不在”
tier residency 决定“是在 HBM hit，还是 DDR hit，还是 miss”
```

### 3.2 影响因素表

| 因素 | 直接 / 间接 | 如何影响 prefix cache hit |
| --- | --- | --- |
| 真实 trace 的共享前缀长度 | 直接 | 决定文本层复用机会；HitFloor 记录时必须转换为 post-template block-chain LCP，不能直接把 raw text LCP 当作 cache hit 口径。 |
| 热前缀数量 | 直接 | 热前缀越集中，同一 block chain 被重复 lookup 的概率越高。 |
| reuse 间隔 | 直接 | reuse 间隔越短，block 更可能仍在 HBM；间隔越长，可能转为 DDR hit 或 miss。 |
| tokenizer | 直接 | 文本相同不代表 token 序列完全相同；token 不同会改变 block hash chain。 |
| chat template | 直接 | system / messages / tools 被模板展开后会改变 token prefix。 |
| runtime block size | 直接 | vLLM 以 full block 复用；block size 越大，partial prefix 越容易被向下取整丢掉。 |
| CP / DCP / PCP | 直接 | 会放大 effective block size，使 cached tokens 按更大粒度统计。 |
| MTP / EAGLE | 直接 | 可能丢弃最后一个 matched block，降低 cached tokens。 |
| block visibility timing | 直接 | block 生成后何时可被后续请求 lookup，决定长 prefill 中是否提前产生 hit。 |
| HBM prefix cache capacity | 直接 | HBM 容量越小，历史 prefix blocks 越容易被驱逐。 |
| DDR / CPU pooling capacity | 直接 | 决定 HBM 被驱逐或写入 DDR 后，历史 blocks 是否仍可在低层级命中。 |
| eviction / touch / keepalive | 直接 | 决定哪些 resident blocks 被保留，哪些被淘汰。 |
| pooling mode | 直接 | write-through 和 eviction-offload 会产生完全不同的 DDR 可见性和 DDR hit。 |
| active KV occupancy | 间接但强影响 | running request 占用 HBM 后，挤压 prefix cache 可用容量，导致 HBM hit 下降、DDR hit 或 miss 上升。 |
| batch / chunked prefill | 间接但强影响 | 通过 running set、active KV 和 chunk visibility 间接改变 hit 分布。 |
| prefill + decode 混合 batch | 间接 | decode KV 也会占用 HBM；decode-heavy 或 PD 混部场景会改变 active KV pressure。 |
| KV load service / wait | 间接 | 不改变是否 hit，但改变 DDR hit 对 TTFT 是否有收益。 |
| compute / transfer overlap | 间接 | 不改变 hit tokens，但改变 DDR hit 对最终 TTFT 的贡献。 |
| 稀疏注意力 / hybrid 模型 | 直接，V2/V3 | 可能改变 block group、layer 对齐和可复用规则，影响 prefix cache hit 定义。 |
| gateway routing | 间接，V2 | 改变请求落在哪个实例，从而改变同实例 prefix reuse 链。 |
| 多实例 pooling / remote hit | 直接，V2 | 让跨实例 blocks 可见，改变 hit 来源和 tier residency。 |

### 3.3 对 HitFloor 准确性影响最大的因素

从 HitFloor 视角看，最影响 HBM hit / DDR hit / miss 分布的因素优先级是：

```text
P0:
  block visibility timing
  active KV occupancy
  cache lifecycle / eviction / touch
  HBM / DDR capacity
  pooling mode

P1:
  runtime block size / CP / MTP accounting
  tokenizer / chat template parity
  DDR load profile
  hot prefix / LCP chain analytics

V2/V3:
  sparse / hybrid cache manager
  gateway routing
  multi-instance remote pooling
  decode / TPOT-aware active KV
```

其中 `runtime block size / CP / MTP / tokenizer` 是 correctness guard：配错会整体错误，但配置正确后，动态误差主要来自 visibility、active KV、capacity 和 lifecycle。

## 4. Trace 侧建议记录的 Block-Level LCP / 热前缀信息

建议后续新增独立的热前缀分析能力。该能力属于外围分析能力，但应消费核心 replay 的 block-level telemetry，不应反向修改 replay 结果。

HitFloor 语境中的 LCP 应该记录在以下处理之后：

```text
request_params
-> parse messages/tools/model
-> apply chat template
-> tokenizer
-> runtime/effective block size
-> build prefix block hash chain
-> block-chain LCP
```

因此，建议记录对象以 block hash chain 为主，而不是原始文本。原始文本 LCP 可以作为 trace 探索辅助字段，但不能作为 prefix cache hit 的判断口径。

### 4.1 InferTwin 当前记录到了哪一层

InferTwin 当前没有独立的 LCP / hot prefix analytics 模块，但 replay 使用的 prefix 口径已经是 block-chain 口径：

```text
TraceRecord
-> parse_request_params
-> tokenizer_registry.encode
-> RequestBuildContext.calculate_block_conversion
-> build_prefix_blocks(token_ids, effective_block_size)
-> SimulationRequest.prompt_blocks
```

`SimulationRequest.prompt_blocks` 中每个 `PrefixBlock` 保存：

```text
block_key
content_hash
block_index
token_count
size_bytes
```

其中 `block_key` 是链式 hash，包含 parent hash、model、tenant/cache scope 和当前 block content hash。后续 HBM / DDR lookup 也是沿 `prompt_blocks` 从头连续匹配，遇到第一个 miss 后停止。

streaming path 会把 `prompt_blocks` 序列化进 JSONL shard，因此大 trace replay 读取的也是 tokenized + blockized 后的 prefix block chain，而不是原始文本。

当前缺口是：

```text
InferTwin 会用 block-chain 做 lookup / hit accounting，
但还没有单独统计不同 request 之间的 block-chain LCP、热前缀生命周期、
reuse interval、prefix_chain_id 或前缀演化。
```

### 4.2 建议新增的热前缀记录字段

建议记录字段：

```text
prefix_chain_id
block_hash_prefix
tenant_id
model
instance_uuid
first_seen_time
last_seen_time
visible_start_time
visible_end_time
reuse_count
lookup_count
hit_count
miss_count
hbm_hit_count
ddr_hit_count
evicted_count
prefix_length_blocks
prefix_length_tokens
reuse_interval_ms distribution
```

还应记录 prefix chain 的演化：

```text
base prefix
-> base + tool result
-> base + tool result + code
-> base + revised code
```

这类信息对 code agent / agent workflow 场景尤其重要，因为 agent 常常反复输入历史代码、工具结果和中间产物。

## 5. 系统侧最关键机制

### 5.1 KV Block Lifecycle

KV block 管理直接影响 prefix cache hit，优先级最高。

需要关注：

- block allocation。
- block materialization / store。
- block visibility。
- block touch / keepalive。
- block eviction。
- block tier residency。
- request finish 后 block 是否可复用。
- progressive chunk 完成后 full block 是否可复用。

真实 vLLM / vLLM-Ascend 中，prefix cache hit 依赖 cache manager / block manager 的状态转移。InferTwin 不能只判断 hash 是否相等，还必须判断 hash 对应的 block 在 lookup 时是否可见、是否仍 resident。

### 5.2 Cache Capacity 与 Active KV Occupancy

cache 容量是 prefix cache hit 的基础。真实系统中的 HBM KV capacity 不能简单等同于 prefix cache 可用容量。

应区分：

```text
physical_kv_capacity:
  真实设备可用于 KV 的总容量。

active_kv_occupancy:
  running requests 正在使用、必须保留的 KV blocks。

prefix_cache_residency_capacity:
  剩余可用于历史 prefix cache residency 的容量。
```

真实系统中 active KV 的优先级通常高于 cached prefix KV：

```text
active KV / running request blocks
> free blocks
> cached prefix blocks
```

因此，高并发长请求会通过 active KV 挤压 HBM prefix cache：

```text
effective_hbm_prefix_capacity(t)
= total_hbm_kv_blocks
- active_prefill_blocks(t)
- active_decode_blocks(t)
- reserved_blocks
```

当前阶段可以先使用轻量近似：

```text
effective_hbm_prefix_capacity(t)
= total_hbm_kv_blocks
- active_running_blocks(t)
- reserved_blocks
```

这对 HitFloor 很关键，因为它解释：

- 高并发下为什么 HBM hit 下降。
- 高并发下为什么 DDR hit 可能上升。
- 低并发下为什么更多 hit 留在 HBM。
- 同样 total hit rate 下为什么 TTFT 不同。

### 5.3 Batch / Chunked Prefill 对 Prefix Hit 的影响

组 batch 对 prefix cache hit 的影响不是简单的 batch latency，而主要通过两条路径发生。

路径 A：影响 active KV occupancy。

```text
batch / chunked prefill 决定 running set 和已生成 active blocks
-> active KV occupancy 改变
-> HBM prefix cache 可用容量改变
-> HBM hit / DDR hit / miss 分布改变
```

路径 B：影响 block visibility timing。

```text
chunk 完成后 newly completed full blocks 是否可见
-> 后续请求是否能在长 prefill 过程中提前 hit
```

因此，batch 不是 HitFloor 中可以完全忽略的 TTFT 细节。即使不追求精确 batch latency，仍必须关心它对 active KV 和 visibility 的影响。

### 5.4 Prefix Block Visibility

prefix block visibility 是 HitFloor 准确性的 P0 问题。

如果仿真器假设：

```text
request 全部 prefill 完成后，所有 miss blocks 才可见
```

则在长 prompt / 长 prefill 场景下可能低估 prefix cache hit。

更接近真实系统的口径应是：

```text
某个 scheduler iteration / chunk 完成后，
本轮 newly completed full blocks 对后续 lookup 可见。
```

同一个 iteration 内是否可见不应默认假设，因为本轮 batch 已经完成 selection。

Step9 已实现 progressive full-block visibility，这个方向正确；后续仍需对照 vLLM / vLLM-Ascend 继续确认是否足够接近真实 block manager 行为。

### 5.5 Pooling / DDR Tier 语义

DDR hit 高于 HBM hit 是合理现象，尤其当：

- HBM prefix cache 被 active KV 挤压。
- DDR 容量远大于 HBM。
- reuse interval 超过 HBM residency，但未超过 DDR residency。
- pooling / offload 机制让 DDR 保留更多历史 blocks。

但不同 pooling mode 的语义完全不同。

```text
write_through_on_materialization:
  request block 可见后同时写入 HBM 和 DDR。

hbm_evict_offload_ddr:
  block 先在 HBM，只有被 HBM 淘汰或 offload 时才进入 DDR。
```

这两个模式会产生完全不同的 DDR hit 分布。HitFloor 开发前必须显式区分，否则 DDR hit 会被高估或低估。

### 5.6 Cached Tokens Accounting

cached_tokens accounting 是 prefix cache hit 的 correctness guard。

当前需要继续遵循：

- `max_cache_hit_length = prompt_tokens - 1`。
- full block 向下取整。
- 使用 runtime block size，而不是只看 CLI `--block-size`。
- PCP / DCP 放大 effective block size。
- MTP / EAGLE / EAGLE3 丢弃最后一个 matched block。
- hybrid cache group 需要 LCM 对齐，暂时作为 V2/V3 研究问题。

这些规则如果错了，prefix hit 会整体错；但在配置正确后，它们通常不是 HitFloor 误差最大的动态来源。

### 5.7 模型结构 / 稀疏注意力

模型本身会显著影响 prefix cache hit，尤其是 sparse attention、hybrid attention、Mamba / SSM 组合模型。

这类模型可能破坏两个基础假设：

- 每层 KV 都按 per-token 可拼接方式存储。
- 所有层的同一个 block 对应同一段 token。

因此，当前 HitFloor 应明确主要面向 full-attention、block-chain 可复用的模型。Sparse / hybrid cache manager 放到 V2 / V3，不应在 V1 中隐式兼容。

## 6. 本轮源码调研结论：真实系统机制

本节基于本地 vLLM、vLLM-Ascend 和 Mooncake / Mooncake Store 源码阅读，回答进入 HitFloor 前最关键的机制问题。

参考源码：

- `/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py`
- `/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py`
- `/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py`
- `/home/zhangxiyue/vllm/vllm/v1/kv_offload/abstract.py`
- `/home/zhangxiyue/vllm/vllm/v1/kv_offload/cpu/manager.py`
- `/home/zhangxiyue/vllm/vllm/v1/kv_offload/worker/cpu_gpu.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/cpu_offload/`
- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py`
- `/home/zhangxiyue/Mooncake/mooncake-store/`

### 6.1 真实 vLLM 中 prefix block 何时可被其他请求 hit

结论：

```text
本地 HBM prefix cache:
  full block 被计算完成，并且 scheduler / KVCacheManager 调用 cache_blocks 后，
  block hash metadata 进入 block pool 的 prefix cache map。

外部 KV / remote KV:
  connector 判断外部命中后，需要完成 KV load / async recv；
  load 完成后才调用 cache_blocks，随后才成为本地可复用 prefix block。
```

更具体地说：

- `BlockPool.cache_full_blocks()` 只缓存 full blocks，并把 block hash 写入 `cached_block_hash_to_block`。
- `KVCacheManager.get_computed_blocks()` lookup 时使用 `max_cache_hit_length = request.num_tokens - 1`，并要求 hit length 是 full-block 对齐。
- `KVCacheManager.allocate_slots()` 在本轮 scheduled tokens 分配完成后，会调用 `cache_blocks(request, num_tokens_to_cache)`。
- 如果 `delay_cache_blocks=True`，即 remote KV async load 场景，vLLM 暂不 cache 这些 blocks；等 `_update_waiting_for_remote_kv()` 确认 transfer 完成后，才调用 `cache_blocks()`。

因此，真实 vLLM 不是“request 全部 finish 后 blocks 才可见”。更接近的口径是：

```text
一个 newly completed full block
在对应 scheduler progress / chunk 结算并完成 cache_blocks 之后，
可以被后续 scheduler lookup 命中。
```

但不应假设：

```text
同一个 scheduler iteration 中已经完成 batch selection 的其他 request
可以回头命中本 iteration 刚生成的 block。
```

对 InferTwin 的判断：

- Step9 的 progressive full-block visibility 比 finish-time materialization 更接近真实 vLLM。
- 它适合作为 HitFloor 第一版的 prefix visibility baseline。
- 仍需明确它是 iteration/chunk 粒度近似，不是逐 kernel / 逐 layer 可见。

### 6.2 running request active KV 如何占用 HBM

真实 vLLM 的 block pool 同时承载：

```text
running request active blocks
cached prefix blocks with hash metadata
free queue / eviction candidates
```

关键机制：

- `get_new_blocks()` 从 free queue 取 block；如果这个 block 仍带 cached hash，会先 `_maybe_evict_cached_block()`，再把它分配给 active request。
- `touch()` 用于 prefix hit blocks：如果 block 的 `ref_cnt == 0`，说明它在 free queue 中，是可淘汰 cached block；touch 会先把它从 free queue 移除，再增加 `ref_cnt`。
- `free_blocks()` 在 request finish 或释放时减少 `ref_cnt`；当 `ref_cnt == 0` 时，block 回到 free queue。

因此，active KV 的真实语义是：

```text
ref_cnt > 0:
  block 被 running request 持有，不能作为 prefix cache victim 直接复用。

ref_cnt == 0 且带 block_hash:
  block 是 cached prefix block，同时也是 free queue 中的 eviction candidate。
```

### 6.3 容量紧张时 cached prefix blocks 与 active blocks 的优先级

结论：

```text
active KV / running request allocation
优先级高于
cached prefix block metadata。
```

原因是：当 vLLM 需要给 running request 分配新 block，而 free queue 中拿到的 block 是 cached prefix block 时，会先移除它的 prefix hash metadata，然后把物理 block 分配给 active request。

因此，高并发和长 prefill 会挤压 HBM prefix cache。HitFloor 如果只用静态 `hbm_capacity_blocks` 作为 prefix cache 容量，会系统性高估 HBM hit。

建议的第一版建模方式：

```text
effective_hbm_prefix_capacity(t)
= max(
    0,
    configured_hbm_prefix_capacity_blocks
    - active_kv_occupancy_blocks(t)
    - reserved_blocks
  )
```

更接近真实物理容量的写法是：

```text
effective_hbm_prefix_capacity(t)
= min(
    configured_hbm_prefix_capacity_blocks,
    physical_kv_capacity_blocks - active_kv_occupancy_blocks(t) - reserved_blocks
  )
```

第一版不需要完整复刻 vLLM `ref_cnt` / free queue，但至少应该在 scheduler iteration 边界估算 active KV：

```text
active_kv_occupancy_blocks(t)
= sum(ceil(active_request_computed_or_allocated_tokens / effective_block_size))
```

当前 InferTwin 还没有这个能力，因此在高并发长请求场景下：

- HBM hit 可能被高估。
- DDR hit 可能被低估或分布位置不准。
- HBM-heavy / DDR-heavy 的 HitFloor 判断可能偏乐观。

### 6.4 pooling / DDR 的真实语义不是单一 write-through

真实系统里至少存在三类不同语义，不能合并成一个“DDR LRU 字典”。

#### A. vLLM CPU offload 抽象

vLLM `OffloadingManager` 明确区分：

- `lookup()`：判断哪些 blocks 已经 offloaded。
- `prepare_load()` / `complete_load()`：load 期间保护 blocks，完成后才重新允许 eviction。
- `prepare_store()` / `complete_store()`：store 完成后 blocks 才 become loadable。
- `touch()`：更新 offloaded blocks 的 recency。

这说明 offload tier 的可见性受 store/load 完成状态影响，不是 materialize 时同步可见。

#### B. vLLM-Ascend CPU offload connector

vLLM-Ascend 的 CPU offload 路径包含：

- scheduler 侧 `get_num_new_matched_tokens()` 通过 metadata server 查 CPU prefix cache，并 touch 命中 blocks。
- metadata 侧 `CPUKVCacheManager` 维护 CPU-side block pool、prefix hash、allocation、free。
- worker 侧按 layer 触发 `start_load_kv()` / `wait_for_layer_load()` / `load_kv_layer()`，把 CPU KV 拷回 device KV cache。

这说明真实 load 可以是 layer-wise，并且有独立 stream / async 行为。InferTwin 当前 request/iteration-level KV load 是粗粒度近似。

#### C. Mooncake / Mooncake Store

Mooncake Connector 的 P/D KV transfer 是 remote prefill / remote decode 传输路径：

- scheduler 通过 `get_num_new_matched_tokens()` 判断 external tokens。
- `update_state_after_alloc()` 记录需要 recv / send 的 block ids。
- worker 根据 remote/local block ids 和 transfer regions 构造传输计划，并调用 transfer engine 批量传输。

Mooncake Store 是更通用的对象/segment/replica/lease 存储池：

- `batch_get_into_multi_buffers()` 会先 BatchQuery metadata，再选择 MEMORY / LOCAL_DISK / DISK replica。
- MEMORY replica 可以直接 RDMA / GPUDirect 到目标 buffer。
- LOCAL_DISK / DISK 有不同路径，DISK 可能需要临时 CPU buffer 再 scatter。
- Master 中存在 `offload_on_evict`，即 LOCAL_DISK offload 可以发生在 eviction time，而不是 PutEnd。
- Store 还有 lease / pin / replica placement / eviction，这些都会影响对象是否可读、读哪条链路、何时被淘汰。

因此，Mooncake / vLLM-Ascend pooling 不应被默认理解成：

```text
every materialized block immediately write-through to DDR
```

它可能是：

```text
metadata-driven remote store
eviction-time offload
explicit CPU offload store/load
P/D KV transfer
```

具体取决于部署形态。

### 6.5 关键问题回答

#### 如果真实系统 DDR hit 主要来自 offload，只做 write-through 会不会明显高估 DDR 可见性？

会。

如果真实系统是 `hbm_evict_offload_ddr` 或 `offload_on_evict`：

```text
block 只有在 HBM eviction/offload 发生且 store 完成后，
才会在 DDR/CPU tier 可见。
```

而 InferTwin 当前 `TieredPrefixCache.materialize()` 是：

```text
miss block materialize 时同时写 HBM 和 DDR。
```

这会导致：

- DDR 中出现真实系统尚未 offload 的 blocks。
- DDR residency 时间被提前。
- DDR hit rate 被高估。
- DDR-heavy 场景下 TTFT 被低估，因为本该 miss 或等待 store 完成的 blocks 被当成可 load。

如果真实部署确实是 write-through store，每个 completed full block 都同步或异步写入 pool，那么当前 InferTwin 方向上更接近，但仍缺少：

- async store completion。
- store failure。
- lease / pin / replica placement。
- shared transfer queue。
- read/write bandwidth contention。

#### 真实 vLLM / vLLM-Ascend 中，一个 prefix block 到底什么时候能被其他请求 hit？

本地 HBM prefix cache：

```text
full block computed
-> scheduler / cache manager 调用 cache_blocks
-> block hash metadata 进入 prefix cache map
-> 后续 scheduler lookup 可以 hit
```

外部 KV / CPU offload / remote prefill：

```text
external lookup 命中
-> 分配 local slots / metadata
-> load 或 recv 完成
-> cache_blocks 或 connector-side ready 状态完成
-> 后续 lookup 才可稳定复用
```

因此，真实系统中“可被 hit”的时间点不是 request finish，而是 full block cache/store 的完成点；对 remote/offload tier 还要等 transfer/store/load 状态完成。

#### progressive visibility 是否已经足够接近真实 vLLM？

对本地 HBM full-block visibility 来说，已经足够接近第一版 HitFloor baseline。

原因：

- vLLM 以 full block 为 prefix cache lookup 单位。
- vLLM 会随着 scheduler progress 缓存已经完成的 full blocks。
- Step9 progressive full-block materialization 避免了长 prefill 场景下 finish-time materialization 低估 hit。

但它还不够完整：

- 没有 active KV occupancy / ref_cnt / free queue。
- 没有 store/load completion state。
- 没有真实 offload-on-evict 或 write-through mode 区分。
- 没有 layer-wise load 可见性。
- 没有 decode KV growth。

所以结论应写成：

```text
progressive visibility 对 local HBM prefix timing 是可接受近似；
对 tier residency / DDR hit / capacity pressure 仍不充分。
```

#### active KV occupancy-aware HBM capacity 如何建模？

建议分两级。

第一版保守实现：

```text
在每个 scheduler iteration 开始或结束时，
根据 running requests 的已分配/已计算 token 数估算 active blocks，
动态调整 HBM prefix cache 可用容量。
```

公式：

```text
active_blocks(request, t)
= ceil(active_tokens(request, t) / effective_block_size)

effective_hbm_prefix_capacity(t)
= max(0, configured_hbm_prefix_capacity_blocks
          - sum(active_blocks)
          - reserved_blocks)
```

其中 `active_tokens` 第一版可以取：

```text
min(request.num_computed_tokens + currently_scheduled_chunk_tokens,
    request.prompt_tokens)
```

更真实的后续版本：

- 引入 active block set。
- 引入 ref_cnt。
- 区分 active prefill KV 与 active decode KV。
- 区分 request finish release 与 block reuse after touch。
- 与 HBM LRU free queue 统一。

### 6.6 对 InferTwin 当前实现的误差方向

| 当前实现 | 真实系统差异 | 误差方向 |
| --- | --- | --- |
| `TieredPrefixCache.materialize()` 同时写 HBM 和 DDR | 真实可能是 write-through，也可能是 eviction-offload / offload-on-evict / remote store | 若真实主要是 offload，则高估 DDR 可见性和 DDR hit；若真实 write-through，则方向接近但低估 async store delay |
| Progressive full-block materialization | 真实 vLLM 在 scheduler progress 后缓存 full blocks | 对 local HBM visibility 方向正确；粒度仍比真实调度粗 |
| HBM LRU 只管理 cached prefix metadata | 真实 HBM block pool 同时管理 active blocks、free queue、ref_cnt | 高并发长请求下高估 HBM prefix capacity |
| DDR LRU 只管理 metadata | 真实 DDR/CPU/store 有 store completion、load protection、lease、pin、replica、eviction | DDR hit timing 可能过早；DDR residency 可能过于理想化 |
| 无 active KV occupancy | 真实 active KV 优先占用 HBM | 高估 HBM hit，低估 active pressure 对 tier 分布的影响 |
| 无 pooling mode schema | 真实部署 mode 不同，DDR hit 来源不同 | 无法解释 write-through 与 offload 结果差异 |

### 6.7 对 pre_hitfloor 优先级的修正

进入 HitFloor 外围能力之前，建议把优先级调整为：

```text
P0:
  1. active KV occupancy-aware HBM capacity
  2. pooling mode schema: write_through / hbm_evict_offload / remote_store
  3. DDR visibility timing: store completion / offload completion
  4. progressive visibility 与 local HBM lookup 的边界说明

P1:
  5. active block / cached block / free queue 的轻量状态模型
  6. DDR load wait/service 与 shared link 的配置 guard
  7. hot prefix / block-chain LCP analytics

V2/V3:
  8. ref_cnt 级 block manager
  9. decode KV growth
  10. hybrid / sparse cache manager
  11. Mooncake Store replica/lease/pin/placement 级建模
```

其中，HitFloor 表最先需要的是 P0 的前两项。否则在调节 cache capacity 时，HBM hit 和 DDR hit 的边界会不稳定。

## 7. Prefix Cache Hit 的综合表达

HitFloor 中真实 prefix hit 可以理解为：

```text
real_prefix_hit
= trace_reuse_opportunity
  * block_accounting_correctness
  * visibility_timing
  * active_kv_pressure
  * cache_lifecycle
  * tier_residency
```

其中：

- trace reuse opportunity 由真实 trace 决定。
- block accounting correctness 由 tokenizer、chat template、runtime block size、CP/MTP 等决定。
- visibility timing 由 chunked prefill / scheduler iteration / block materialization 决定。
- active KV pressure 由 batch、并发、prefill/decode active blocks 决定。
- cache lifecycle 由 block manager、touch、keepalive、eviction 决定。
- tier residency 由 HBM / DDR / remote pooling mode 和容量决定。

## 8. HitFloor 前置优先级修正

此前 pre_hitfloor 技术路线更偏工程 guard，例如 pooling mode、metrics alias、calibration status。这些工作仍然必要，但它们不是 prefix cache hit 准确性的最大来源。

更合理的优先级如下。

### P0-1：Prefix Block Visibility 与真实系统对齐

目标：

- 确认 Step9 progressive full-block visibility 是否足够接近真实 vLLM / vLLM-Ascend。
- 明确 block 在 chunk / iteration 完成后的可见时刻。
- 避免长 prefill 场景低估 hit。

### P0-2：Active KV Occupancy-Aware HBM Capacity

目标：

- 建模 running requests active KV 对 HBM prefix cache capacity 的挤压。
- 区分 physical capacity、active occupancy、prefix cache residency capacity。
- 支持解释高并发下 HBM hit 下降、DDR hit 上升。

### P0-3：Cache Lifecycle / Eviction / Keepalive 贴近真实 Block Manager

目标：

- 确认 InferTwin 的 lookup / touch / materialization / eviction 顺序与真实系统是否一致。
- 明确哪些事件是 InferTwin typed event，哪些不等同于 vLLM 原生 telemetry。
- 必要时新增 policy / mode，而不是在 report 层修正。

### P0-4：Pooling Mode 语义显式化

目标：

- 明确 `write_through_on_materialization` 与 `hbm_evict_offload_ddr` 的区别。
- 当前支持的 mode 必须写入配置和输出。
- 未支持 mode fail-fast。

### P0-5：Tier-Aware Metrics

目标：

- 明确输出 HBM hit、DDR hit、miss。
- 明确输出 KV load service / wait / total。
- 明确 DDR hit 是否真的有收益：

```text
saved_compute_ms > kv_load_service_ms + kv_load_wait_ms
```

### P1-1：Trace Hot Prefix / LCP Chain Analytics

目标：

- 记录热前缀的复用次数、位置、存在时间、长度变化。
- 支持分析真实 trace 中 agent workflow / session prefix 演化。
- 作为 HitFloor 解释性和后续策略设计的重要外围分析能力。

### P1-2：DDR Load Profile Guard / Calibration Status

目标：

- 标注 KV load profile 是否 calibrated。
- 缺少校准时使用 conservative default 或明确标注 uncalibrated。
- 不把 DDR-heavy TTFT 结果包装成高置信结论。

### P1-3：Tokenizer / Runtime Block / CP / MTP Guard

目标：

- 作为 correctness guard 保证 block accounting 不出错。
- 确认 tokenizer、chat template、runtime block size、CP、MTP/EAGLE 规则。
- 配置错误时 fail-fast。

## 9. 对 InferTwin 当前实现的判断

当前 InferTwin 已具备很多 HitFloor 所需基础：

- fixed-routing、多实例隔离 replay。
- HBM LRU。
- HBM + DDR LRU tiered cache。
- KV load service 和 wait accounting。
- chunk-level TTFT timeline。
- progressive full-block visibility mode。
- typed request / iteration / streaming metrics。

但在进入 HitFloor 外围能力前，最值得优先确认或增强的是：

1. Step9 progressive visibility 是否与真实 vLLM / vLLM-Ascend 足够一致。
2. HBM capacity 是否仍被当成静态 prefix cache capacity。
3. active KV occupancy 是否会显著改变 HBM / DDR hit 分布。
4. 当前 DDR write-through mode 是否会高估 DDR hit。
5. cache lifecycle 与真实 block manager 的差异是否会影响 HitFloor 表的结论。

这些问题应优先于单纯的 report 字段整理。

## 10. 后续建议

本轮已完成一次源码调研，结论是：HitFloor 前置工作不应只做配置和 report guard，而应优先处理真实 prefix cache hit 的动态误差来源。

建议后续技术路线围绕以下顺序展开：

1. 先设计 active KV occupancy-aware HBM capacity。
2. 再设计 pooling mode schema，明确当前是 write-through 还是 offload-driven。
3. 然后设计 DDR visibility timing / store completion 的保守模型。
4. 最后再进入 HitFloor 外围能力，生成 HBM/DDR capacity 与 hit/TTFT 的关系表。
