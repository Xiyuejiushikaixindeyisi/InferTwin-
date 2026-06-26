# Step5 vLLM KV Block Management Study

本文记录 Step5 进入有限 HBM LRU 前，对本地 vLLM block 管理链路的调研结论。

调研目标不是复制 vLLM 运行时代码，而是明确 HitFloor 需要保留哪些语义：

- prefix cache lookup 何时发生。
- block 何时变成可命中。
- block 如何被保活和淘汰。
- 哪些事件需要输出给离线仿真报告。

## 1. 调研源码

本次主要阅读以下本地文件：

```text
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_coordinator.py
/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_utils.py
/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py
/home/zhangxiyue/vllm/vllm/distributed/kv_events.py
```

vLLM-Ascend 当前更多是在 scheduler、remote KV、profile 或 patch 层接入，基础 KV block manager 仍主要复用 vLLM 这套抽象。因此 Step5 先以 vLLM 主线为参考。

## 2. vLLM 核心分层

### 2.1 `KVCacheManager`

`KVCacheManager` 是 scheduler 面向的 facade。

它负责：

- 向 scheduler 提供 `get_computed_blocks(request)`。
- 在调度一个 request slice 时执行 `allocate_slots(...)`。
- request 完成、abort 或 preempt 时执行 `free(request)`。
- 通过 `take_events()` 导出 KV cache event。
- 隐藏内部 block group、block pool、attention spec 等复杂结构。

关键语义：

- prefix cache lookup 在 waiting request 被 scheduler 首次考虑时发生，而不是 trace arrival 时发生。
- vLLM 在全量命中时仍会保留最后 token 重新计算，用于 logits。HitFloor Step4/Step5 已冻结 zero-miss fast-finish 语义，不复制该限制。
- `allocate_slots(...)` 同时处理 prefix hit block、external KV、new token slot、lookahead token、sliding window skip 等运行时细节。

HitFloor 借鉴点：

- scheduler 不应直接理解 cache 内部结构。
- replay 与 cache 之间需要一个小而稳定的协议。
- cache event 应从 cache 层产生，而不是 report 层重算。

### 2.2 `KVCacheCoordinator`

`KVCacheCoordinator` 负责协调多个 KV cache group。

它负责：

- 创建共享的 `BlockPool`。
- 为不同 attention type 创建 `SingleTypeKVCacheManager`。
- 聚合 `find_longest_cache_hit(...)` 结果。
- 聚合 block allocation、cache、free、remove skipped blocks。

vLLM 支持：

- 单一 full attention cache group。
- hybrid attention cache groups。
- sliding window / chunked local attention。
- DCP / PCP block size alignment。
- cross attention。

HitFloor Step5 简化：

- 第一版只做 decoder-only full-prefix block 语义。
- 不做多个 cache group。
- 不做 sliding window、chunked local attention 的 null block。
- 不做 DCP / PCP 对 block size 的放大。

### 2.3 `SingleTypeKVCacheManager`

`SingleTypeKVCacheManager` 管理一种 attention type 的 request 到 blocks 的映射。

它维护：

```text
req_to_blocks: request_id -> list[KVCacheBlock]
num_cached_block: request_id -> int
```

关键动作：

- `find_longest_cache_hit(...)`：按 block hash 连续查找最长可命中的 prefix。
- `allocate_new_computed_blocks(...)`：把 prefix hit blocks 追加到 request 的 block list，并 touch 这些 block。
- `allocate_new_blocks(...)`：给本轮要计算的新 token 分配 block。
- `cache_blocks(...)`：把已满 block 写入 prefix cache hash table。
- `free(request_id)`：request 结束后释放 request blocks，释放顺序是 reverse order，使 tail blocks 优先成为 LRU 淘汰候选。

HitFloor 借鉴点：

- prefix hit 必须是从 block 0 开始的连续命中。
- hit block 需要刷新访问时间或 LRU 位置。
- materialization 需要按 block 顺序写入。
- 释放或 materialization 后的 eviction 顺序必须 deterministic。

### 2.4 `BlockPool`

`BlockPool` 是 vLLM 中最接近 Step5 的组件。

它同时承担两类职责：

1. 物理 KV block slot allocator。
2. prefix cache hash index 和 eviction manager。

核心数据结构：

- `blocks`: 所有 `KVCacheBlock`。
- `free_block_queue`: 空闲 block 和可淘汰 cached block 的 LRU 队列。
- `cached_block_hash_to_block`: block hash 到 cached block 的索引。
- `kv_event_queue`: `BlockStored` / `BlockRemoved` / `AllBlocksCleared` 事件。

关键语义：

- `get_new_blocks(num_blocks)` 从 free/LRU 队列取 block。
- 如果取出的 block 已有 cache hash，则 `_maybe_evict_cached_block(...)` 先从 prefix cache index 移除并产生 remove event。
- `touch(blocks)` 会增加 ref count；若 block 当前在 free queue 中，先从 free queue 移除，避免被淘汰。
- `free_blocks(ordered_blocks)` 会降低 ref count，并把 ref count 归零的 block 追加回 free queue。
- `cache_full_blocks(...)` 给 full block 绑定 hash，并写入 prefix cache index。

HitFloor 借鉴点：

- LRU 候选只应来自当前不被 active request 使用的 block。
- hit/touch 与 materialize 应由 cache 层产生日志事件。
- event queue 应支持 `take_events()`，避免 report 层重新推导事件。

HitFloor 不复制：

- 真实 block id 分配。
- ref count 与 free queue 的完整运行时语义。
- null block。
- T-LRU。
- metrics collector。
- remote KV connector 交互。
- 真实 token ids 和 KV tensor。

### 2.5 `KVCacheBlock` 和 block hash

vLLM 的 `KVCacheBlock` 保存：

- `block_id`
- `ref_cnt`
- `block_hash`
- free queue 链表指针
- null block 标记

vLLM block hash 是链式 hash：

```text
current_block_hash = hash(parent_block_hash, current_block_tokens, extra_keys)
```

HitFloor 当前 `PrefixBlock` 已采用相同方向的 hash-only 设计：

```text
PrefixBlock(
  block_key,
  content_hash,
  block_index,
  token_count,
  size_bytes,
)
```

Step5 不需要保存全量 token ids，也不需要保存真实 KV tensor。

## 3. vLLM Scheduler 调用链路

waiting request 被调度时，大致链路如下：

1. scheduler 从 waiting queue 取队首请求。
2. 如果 request 还没有 computed tokens，调用 `kv_cache_manager.get_computed_blocks(request)`。
3. 得到本地 prefix cache hit tokens。
4. 结合 external KV 命中结果，计算本轮还需要 compute 的 token 数。
5. 根据 `max_num_batched_tokens`、`max_num_seqs`、chunked prefill 等约束裁剪 `num_new_tokens`。
6. 调用 `kv_cache_manager.allocate_slots(...)`。
7. 如果 slot 不足，当前 request 本轮不能被调度。
8. request 完成后调用 `kv_cache_manager.free(request)`。
9. scheduler 汇总 `kv_cache_manager.take_events()`。

HitFloor Step4 已实现其中的离线 replay 主干：

- fixed-routing, multi-instance isolated replay。
- waiting frontier lookup。
- bounded waiting lookup。
- vLLM-like scheduler。
- chunked prefill。
- finish-time materialization。

Step5 需要补齐：

- 有限 HBM capacity。
- block 生命周期状态。
- LRU touch / eviction。
- cache event 输出。

## 4. 对 HitFloor Step5 的结论

Step5 应保留的 vLLM 语义：

- lookup 在 scheduler 首次考虑 request 时发生。
- prefix hit 是连续 block prefix。
- hit 会刷新 LRU。
- 未完成 prefill 的 miss blocks 不可见。
- materialization 只在 request prefill finish 后发生。
- eviction 由 cache 层产生，不由 report 层重算。
- 多实例固定路由 replay 下，每个 instance 有独立 cache。

Step5 应主动简化的运行时细节：

- 不建真实 physical block table。
- 不建 `allocate_slots(...)` 的完整物理槽位模型。
- 不建 ref count 的完整并发运行时语义。
- 不建 remote KV、DDR、cross-instance pooling。
- 不建 sliding window、hybrid attention、spec decode、encoder cache。
- 不把外部 simulator 的 batch input 混进 cache manager。
- 不建逐 block progressive materialization。真实 vLLM / vLLM-Ascend 可能在 prefill 过程中让 full blocks 更早进入 prefix cache index；HitFloor Step5 固定采用 finish-time materialization，只有 request prefill 完成后的 miss blocks 才对后续 request 可见。

Step5 的核心代码方向：

```text
PrefixBlock tuple
  -> PrefixCache.lookup_prefix()
  -> replay computes miss tokens by scheduler iterations
  -> request finish
  -> PrefixCache.materialize()
  -> LRU evict if over capacity
  -> CacheEvent emitted by cache
```
