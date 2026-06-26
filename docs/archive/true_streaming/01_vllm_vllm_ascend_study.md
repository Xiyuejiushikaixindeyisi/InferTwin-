# True Streaming 调研：vLLM / vLLM-Ascend

调研时间：2026-06-26

本地源码：

```text
/home/zhangxiyue/vllm
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend
```

## 1. vLLM 的相关结构

### 1.1 RequestQueue

参考文件：

```text
/home/zhangxiyue/vllm/vllm/v1/core/sched/request_queue.py
```

vLLM 把 waiting request 抽象为 `RequestQueue`：

- `add_request()`
- `pop_request()`
- `peek_request()`
- `prepend_request()`
- `remove_request()`
- `__iter__()`

实现包括：

- `FCFSRequestQueue`：底层是 `deque`，主路径 `popleft()` 是 O(1)。
- `PriorityRequestQueue`：底层是 `heapq`。

对 HitFloor 的启发：

- HitFloor 已有 `WaitingQueue`，方向正确。
- true streaming 不应回到 list pending + index 的模式。
- replay 的 request arrival 应通过一个 request source / queue abstraction 输入状态机。

### 1.2 Scheduler.add_request

参考文件：

```text
/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py
```

vLLM 在线 serving 的 request 是增量进入 scheduler：

- 新 request 通过 `add_request()` 进入 waiting queue。
- duplicate / resumable request 通过 streaming queue 或 session update 处理。
- scheduler 本身维护 `waiting`、`skipped_waiting`、`running` 和 `requests`。

对 HitFloor 的启发：

- vLLM 不需要预先看到所有 request。
- HitFloor offline trace 也不应该要求先构造全部 request。
- 但 HitFloor 与 vLLM 有一个关键差异：HitFloor 需要 capacity sweep，即同一份 trace 在多个 cache capacity 下复用。因此不能简单地把 CSV iterator 直接传给每个 capacity，否则会重复 tokenizer 和 hash build。

### 1.3 Waiting Scheduling

vLLM scheduling 主流程包括：

- 先处理 running requests。
- 再从 waiting / skipped waiting 选择可调度 request。
- 对 waiting 队首 request 做 prefix cache lookup。
- 计算 `num_computed_tokens`。
- 根据 token budget、chunked prefill、long prefill threshold 决定本轮 `num_new_tokens`。
- KV cache manager 分配 slots。
- request 进入 running。

对 HitFloor 的启发：

- HitFloor 当前 first-schedule-time lookup 与 vLLM 的核心方向一致。
- bounded waiting lookup 的保守策略合理：只 lookup 本轮 scheduler 可能考虑的 waiting frontier，不提前扫全 waiting queue。
- true streaming 只应替换 pending request 输入方式，不应改变 lookup / scheduler / cache accounting 语义。

### 1.4 KVCacheManager.get_computed_blocks

参考文件：

```text
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py
```

关键语义：

- prefix caching disabled 或 skip read 时返回 0。
- `max_cache_hit_length = request.num_tokens - 1`。
- cache hit 只能命中完整 block。
- full attention 下如启用 eagle / mtp 语义，需要 drop 最后一个 matched block。
- CP / PCP / DCP 会影响 effective block size。

对 HitFloor 的启发：

- EO-H 已将 vLLM-like cached_tokens accounting 贯穿 replay lookup。
- true streaming 不应修改 `cached_tokens` 口径。
- true streaming 的 request shard 必须保存足够信息，让 replay 无需重新 tokenizer 也能复现同一套 block conversion / cache accounting。

### 1.5 BlockPool.cache_full_blocks

参考文件：

```text
/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py
/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py
```

vLLM 会在 block 变成 full block 后写入 prefix cache map。

这和 HitFloor 当前默认 `FinishTimeMaterializationPolicy` 有差异：

- vLLM 更接近 progressive full-block visibility。
- HitFloor 当前等 request prefill finish 后一次性 materialize。

对 true streaming 的影响：

- 本任务不解决 progressive block visibility。
- 但 streaming replay 的接口不能把 materialization 写死在 runner 内，仍应继续使用 `MaterializationPolicy`。
- 后续新增 `ProgressiveChunkMaterializationPolicy` 时，应能复用 streaming replay 的 request source。

## 2. vLLM-Ascend 的相关结构

参考文件：

```text
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/core/recompute_scheduler.py
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/core/scheduler_dynamic_batch.py
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/
```

观察：

- vLLM-Ascend 延续了 vLLM scheduler / KV cache manager 的基本状态机。
- dynamic batch / recompute scheduler 会在首次 prefix cache lookup 后设置 request 的 cached token accounting。
- Ascend 侧有 CP、MTP、KV pool、external KV transfer、pooling 等扩展。
- `num_cached_tokens`、`vllm_cached_tokens`、`kvpool_cached_tokens` 等字段说明 local cache 和 external cache 需要明确拆分。

对 HitFloor 的启发：

- true streaming 第一版仍只做 HBM local cache，不引入 DDR / pool。
- request shard schema 应预留 local / external cache 口径扩展空间，但不能在 v1 中输出虚假 external hit。
- true streaming 不能把 vLLM-Ascend 的多级缓存或 KV transfer 混入本任务。

## 3. HitFloor 与 vLLM 的关键差异

| 维度 | vLLM / vLLM-Ascend | HitFloor 当前 | True Streaming 目标 |
| --- | --- | --- | --- |
| 输入 | 在线 request 增量进入 | CSV build 全量 request list | CSV 逐行 build，写 shard 或流入 replay |
| 多 capacity | 无 sweep 需求 | build once in memory reuse | build once to disk shard reuse |
| request queue | waiting/running 内部状态 | pending list + waiting/running | request source + waiting/running |
| cache storage | 真实 KV block / block id | hash-only metadata | 继续 hash-only metadata |
| materialization | full block 可逐步 cache | finish-time materialization | 保持默认不变，接口可扩展 |
| metrics | 在线输出 / stats | tuple metrics then aggregate | streaming sink 聚合 |

## 4. 设计结论

1. true streaming 应新增 opt-in runner，不修改现有 `CapacitySweepRunner` 默认路径。
2. request build 和 replay 要拆成两个阶段：
   - streaming request shard build：CSV -> tokenizer -> hash-only request shard。
   - streaming replay：per-instance shard -> replay state machine -> metrics sink。
3. capacity sweep 仍应 build once，但复用磁盘 shard，而不是内存 request list。
4. 第一版要求 trace 按 `(service_start_time, instance_uuid, request_id)` 或至少按 `service_start_time` 单调；不做 external sort。
5. streaming replay 必须与现有 list replay 在小 trace 上输出相同核心指标。

