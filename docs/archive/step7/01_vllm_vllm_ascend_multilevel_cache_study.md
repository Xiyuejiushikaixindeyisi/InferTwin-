# Step7 调研：vLLM / vLLM-Ascend KV Cache 与多级 Cache

状态：调研完成，供 Step7 技术路线评审使用。

## 1. 调研范围

本轮读取了本地源码和文档：

```text
/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_coordinator.py
/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/kv_offload/
/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/
/home/zhangxiyue/vllm/docs/design/hybrid_kv_cache_manager.md
/home/zhangxiyue/vllm/docs/features/mooncake_connector_usage.md
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/notes/kv_cache_eviction.md
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/
```

## 2. vLLM 本地 KV cache：无 prefix cache / 无池化

vLLM v1 中，scheduler 与 KV cache 管理之间的核心接口是 `KVCacheManager`。

核心职责：

- 计算一个 request 需要多少 KV block。
- 判断是否能 allocate。
- 给 request 分配 block slot。
- request 完成后释放 block。
- 把不同 KV cache group 的 block 组织成 `KVCacheBlocks` 返回 scheduler / worker。

在 prefix caching 关闭时，`KVCacheCoordinatorNoPrefixCache` 不做 cache lookup，只负责 allocation/free。

关键链路：

```text
Scheduler.schedule()
-> KVCacheManager.allocate_slots(...)
-> KVCacheCoordinator.allocate_new_blocks(...)
-> SingleTypeKVCacheManager.allocate_new_blocks(...)
-> BlockPool.get_new_blocks(...)
```

`BlockPool` 是底层 block pool。它管理所有 KVCacheBlock、free queue 和 block allocation。无 prefix cache 时，block 只是 request 生命周期里的物理 slot，完成后回到 free queue。

对 InferTwin 的启示：

- Step7 不应把 cache tier 直接塞进 scheduler；scheduler 只消费 lookup 后的 `cached_tokens / miss_tokens` 与 latency。
- cache backend 应继续隐藏具体 tier / policy / store 细节。
- HBM block pool 和外部 tier 应在 cache backend 层处理，不让 replay event loop 知道物理实现。

## 3. vLLM prefix cache：显存内 KV 复用

prefix cache 打开后，vLLM 的关键结构是：

```text
KVCacheManager
-> KVCacheCoordinator
-> SingleTypeKVCacheManager
-> BlockPool
-> BlockHashToBlockMap
```

核心语义：

- `Request` 创建或追加 token 时会计算 full block hash。
- `get_computed_blocks()` 查找最长连续 prefix hit。
- full attention 从左到右扫描 block hash，遇到 miss 就停止。
- `max_cache_hit_length = request.num_tokens - 1`，最后一个 token 必须重算。
- MTP / EAGLE / EAGLE3 会丢弃最后一个 matched block。
- CP 会放大 effective block size。
- hit block 会 `touch()`，从 free queue 移除并增加 ref_cnt，避免被后续 allocation 淘汰。

vLLM 的本地 prefix cache 淘汰是 lazy eviction：

```text
request finish
-> KVCacheManager.free(request)
-> SingleTypeKVCacheManager.free(request_id)
-> BlockPool.free_blocks(reversed(req_blocks))
-> block ref_cnt--，ref_cnt==0 的 block 回到 free queue

future allocation needs block
-> BlockPool.get_new_blocks(...)
-> BlockPool._maybe_evict_cached_block(block)
-> pop cached_block_hash_to_block
-> block.reset_hash()
```

因此：

- `free()` 不是“真正 evict”，只是进入 eviction candidate。
- 真正从 prefix cache map 中删除发生在后续重新分配该 block 时。
- eviction order 由 free queue 顺序体现，通常 request tail blocks 先进入队列。

对 InferTwin 的启示：

- 当前 `HBMCache.materialize()` 在容量满时立即选择 victim 并删除 metadata，这是 offline 简化；它和 vLLM lazy eviction 不完全一致。
- Step7 如果继续沿用 immediate eviction，需要在文档中保留差异；若要更贴近 vLLM，可新增 lazy-free / resident-candidate 状态，但这会扩大 Step7。
- Step7 的首要目标不是重写 HBM eviction，而是新增 tier-aware cache backend；HBM lazy eviction 是否调整应单独评审。

## 4. vLLM hybrid cache：cache group 与 block alignment

vLLM hybrid KV cache 解决的是多种 attention layer 共存时的 block 管理问题。

关键点：

- `KVCacheCoordinator` 会按 KV cache group 协调多个 `SingleTypeKVCacheManager`。
- Full attention、sliding window、Mamba、local attention 等有不同 allocation 和 prefix-hit 规则。
- Prefix hit 必须对不同 group 求交集。
- 不支持 partial-block cache hit，因此 hit length 需要对齐到各 group block size 的公倍数。
- 对 hybrid Mamba 等模型，runtime block size 可能被放大，以统一 page size。

对 InferTwin 的启示：

- Step7 v1 不处理复杂 Hybrid 模型，但 schema 和 cache result 不能把“一个 block 就代表所有层同一 token 范围”写死。
- 新增接口时应避免把 tier key 写成简单 `block_index -> tier`，而应保留 `block_key / group / effective_block_size` 的扩展空间。
- Step7 文档应明确：当前只支持 unitary full-attention style prefix block，Hybrid cache group 属于 V2。

## 5. vLLM kv_offload：显存外 offload manager

本地主线 vLLM 已有 `vllm/v1/kv_offload/`。

核心抽象：

```text
OffloadingManager.lookup(keys)
OffloadingManager.prepare_load(keys)
OffloadingManager.touch(keys)
OffloadingManager.complete_load(keys)
OffloadingManager.prepare_store(keys)
OffloadingManager.complete_store(keys)
OffloadingManager.take_events()
```

CPU offloading manager 的关键语义：

- lookup 返回从第一个 block 开始连续命中的 offloaded block 数。
- prepare_load 会增加 ref_cnt，保护 block 不被 eviction。
- complete_load 释放 load 保护。
- prepare_store 会计算需要 store 的 block、选择 victim、返回 evicted keys。
- complete_store 成功后，block 变为 ready / loadable。
- cache policy 可插拔，例如 LRU / ARC。
- `FilterReusedOffloadingManager` 可以按 reuse frequency 控制是否值得 store。

对 InferTwin 的启示：

- Step7 的 DDR/CPU tier 应采用类似 `lookup / touch / store / evict` 的状态机，而不是一个 dict。
- 即使 Step7 不建真实 async load，也要在事件中表达 `lookup_ddr_hit`、`store_ddr`、`evict_ddr`，并为 Step8 的 `kv_load_ms` 预留 load token / bytes。
- DDR tier 的 eviction policy 应独立于 HBM eviction policy。

## 6. vLLM KVConnector / MooncakeConnector

vLLM distributed KV transfer 的抽象分三层：

- KV pipe：FIFO tensor transmission。
- KV lookup buffer：按 token / request key 做查找。
- KV connector：把 transfer 与 vLLM scheduler / worker 接起来。

`KVConnectorBase_V1` 的 scheduler-side 关键接口：

```text
get_num_new_matched_tokens(request, num_computed_tokens)
update_state_after_alloc(request, blocks, num_external_tokens)
build_connector_meta(scheduler_output)
request_finished(request, block_ids)
take_events()
```

MooncakeConnector 里：

- consumer 可通过 `get_num_new_matched_tokens()` 声明 remote prefill tokens。
- allocation 后通过 `update_state_after_alloc()` 记录要拉取的本地 block ids。
- worker 侧异步 load KV。
- producer request finish 后可能 `delay_free_blocks=True`，等 send 完成再释放本地 block。

对 InferTwin 的启示：

- Step7 单实例池化不需要实现跨实例 transfer，也不应引入 `remote_engine_id`。
- 但 Step7 应学习 connector 的状态拆分：lookup 告诉 scheduler “哪些 token 外部已算好”，store/load 的实际完成由 backend 事件体现。
- 对未来多实例 pooling，必须新增 remote tier / connector-like adapter，而不是把它塞进 DDR tier。

## 7. vLLM-Ascend 增量

本地 `vllm-ascend-kv-study` 结论：

- vLLM-Ascend 没有重写上游本地 block eviction 核心。
- Ascend scheduler 仍调用上游 `KVCacheManager.allocate_slots()` / `free()`。
- Ascend 增加了 CPU offload / AscendStore / Mooncake / UCM connector。
- 这些 connector 主要参与外部 KV lookup、异步 load/save、delay-free。
- 本地 GPU/NPU block_pool 的 eviction 次序仍由上游 `BlockPool` 和 free queue 决定。

Ascend CPU offload 的关键链：

```text
CPUOffloadingConnectorScheduler.get_num_new_matched_tokens()
-> RPC get_matched_num_and_touch(request)
-> CPUKVCacheManager.find_longest_cache_hit(...)
-> CPU block_pool.touch(...)

build_connector_meta()
-> RPC allocate_slots(...)
-> worker load CPU KV to GPU

request_finished()
-> record_request_cache_and_free_slots(request)
-> worker save finished
-> cache_and_free_slots(request_id)
-> CPU cache_blocks(...)
-> CPU free_slots(...)
```

对 InferTwin 的启示：

- Step7 的单实例池化更接近 Ascend CPU offload：同实例的 CPU/DDR prefix cache 可命中、可 touch、可 store、可 evict。
- Step7 仍不模拟真实 CPU-NPU copy；Step8 再用 `kv_load_ms` 建模 load latency。
- Step7 应记录 DDR hit tokens，而非把 DDR hit 当成 HBM hit。

## 8. Step7 必须保留的差异说明

Step7 计划仍与真实 vLLM / vLLM-Ascend 有差异：

- 不保存真实 KV tensor，只保存 hash metadata。
- 不模拟 physical block slot、ref_cnt、pinned、async load completion。
- 默认仍使用 finish-time materialization。
- DDR/CPU tier 仅在同实例内生效，不做 Mooncake global pool。
- `kv_load_ms` 暂时为 0，Step8 接入。

这些差异不是 bug，但必须通过清晰 schema 和 event 暴露，防止被误读为真实物理存储仿真。
