# vLLM / vLLM-Ascend 调研笔记

## 1. 调研目的

本调研服务于 HitFloor 核心仿真器工程优化阶段。

关注点：

- vLLM scheduler 如何组织 running / waiting / chunked prefill。
- vLLM KV cache manager 如何完成 lookup、slot allocation、cache full blocks、eviction。
- vLLM / vLLM-Ascend 中哪些配置会改变 block size 或 cached_tokens 统计。
- Mooncake / KV transfer 对未来多级 cache 和跨实例池化的接口启发。

不关注：

- 真实 kernel 实现。
- 真实模型权重加载。
- 真实物理 KV tensor 布局。
- 外围 report/export 能力。

## 2. 本地代码路径

本次阅读的主要本地路径：

```text
/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py
/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_coordinator.py
/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py
/home/zhangxiyue/vllm/vllm/v1/kv_cache_interface.py
/home/zhangxiyue/vllm/vllm/platforms/interface.py
/home/zhangxiyue/vllm/vllm/engine/arg_utils.py
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/utils.py
/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/
```

## 3. vLLM Scheduler 结构

vLLM v1 scheduler 的核心思想不是固定区分 prefill phase / decode phase，而是维护每条 request 的：

```text
num_computed_tokens
num_tokens_with_spec
```

每轮调度尝试给 request 分配新的 token 工作量，让 `num_computed_tokens` 逐步追上目标 token 数。这个设计天然覆盖：

- chunked prefill。
- prefix caching。
- speculative decoding。
- decode。
- future jump decoding。

对 HitFloor 的启发：

- HitFloor 当前只模拟 prefill，但 scheduler 状态仍应围绕“已计算 token / 待计算 token”组织。
- `BatchShape` 不应只理解成外部 TTFT 仿真器输入，而应是 replay iteration 的调度结果。
- 后续引入 decode 时，应扩展 request state 和 scheduled slice，而不是重写 replay 主循环。

## 4. Waiting / Running 调度路径

vLLM 每轮调度大致路径：

```text
new scheduler step
-> schedule RUNNING requests first
-> schedule WAITING requests
-> for first-time waiting request:
     get_computed_blocks()
     optional external KV connector lookup
     compute num_computed_tokens
     choose num_new_tokens under token budget / chunk threshold
     allocate_slots()
     move request to running
-> update num_scheduled_tokens
-> update request.num_computed_tokens after schedule
```

HitFloor 当前路径：

```text
pending -> waiting -> first-schedule lookup -> scheduled slice -> finish event -> materialize -> finished
```

主要差异：

- HitFloor 当前不维护真实 running physical slots。
- HitFloor 当前不做 preemption。
- HitFloor 当前不做 decode。
- HitFloor 当前把 cache materialization 放在 request finish time。

工程优化结论：

- `pending -> waiting` 设计应保留，因为 arrival eligibility 和 scheduler consideration 是两个不同事件。
- waiting queue / scheduler / cache lookup 不应提前 lookup 全 waiting 队列。
- 后续如果实现 progressive block visibility，应新增 materialization mode，不改变 `batch_aware_hbm_lru` 的 frozen 语义。

## 5. KVCacheManager / Coordinator / BlockPool 分层

vLLM KV cache 相关代码分层清晰：

```text
Scheduler
-> KVCacheManager
-> KVCacheCoordinator
-> SingleTypeKVCacheManager
-> BlockPool
```

各层职责：

- `Scheduler`：决定本轮 schedule 哪些 request、多少 token。
- `KVCacheManager`：对 scheduler 暴露 `get_computed_blocks()`、`allocate_slots()`、`cache_blocks()`、`free()`。
- `KVCacheCoordinator`：协调 unitary / hybrid cache groups。
- `SingleTypeKVCacheManager`：实现 full attention、sliding window、mamba、MLA 等 manager-specific lookup。
- `BlockPool`：管理 block object、free queue、cached hash map、touch、evict、events。

对 HitFloor 的启发：

- `cache/` 下应继续保持 backend、block metadata、event sink、eviction policy 分离。
- 后续新增多级 cache 或稀疏 attention cache manager 时，不应把逻辑塞入 replay event loop。
- 淘汰策略应该是 stateful policy，但 block residency / event emission / lookup 仍由 cache backend 统一负责。

## 6. Prefix Cache Lookup 规则

vLLM `KVCacheManager.get_computed_blocks()` 的关键语义：

```text
max_cache_hit_length = request.num_tokens - 1
computed_blocks, num_tokens = coordinator.find_longest_cache_hit(...)
```

原因：

- 即使 prompt 全命中，也至少需要重新计算最后一个 token 才能得到下一 token logits。
- 当前 lookup 只接受 full block hit。
- partial block prefix hit 不计入 cached tokens。

`FullAttentionManager.find_longest_cache_hit()` 的关键语义：

- 从左到右扫描 block hash。
- 遇到第一个 miss 停止。
- CP 会把统计 block size 放大为 `runtime_block_size * PCP * DCP`。
- `use_eagle` 为 true 时丢弃最后一个 matched block。
- hybrid cache group 需要按 LCM 对齐，避免 partial-block hit。

对 HitFloor 的启发：

- 必须新增 block size / cache block conversion module。
- `requested_block_size`、`runtime_block_size`、`effective_block_size` 不能混用。
- `cached_tokens` 应由明确 calculator 输出，不应分散在 request builder、cache backend、report 中重复计算。

## 7. BlockPool 与 Event 语义

vLLM `BlockPool` 维护：

- `cached_block_hash_to_block`：hash 到 cached block。
- `free_block_queue`：空闲和可淘汰 block 队列。
- `touch()`：命中 block 后提升引用并从 free queue 中移除。
- `_maybe_evict_cached_block()`：复用 block 前清理 hash 并发出 remove event。
- `cache_full_blocks()`：当 request 有新的 full blocks 时写入 prefix cache。

重要差异：

- vLLM 有真实 block id、ref count 和 physical slot。
- HitFloor 当前只保存 hash key + metadata，不保存真实 KV tensor。
- HitFloor 当前不建 pinned/refcount/physical slots。

工程优化结论：

- HitFloor 继续保持 hash-only 存储，避免内存爆炸。
- 如果后续要更贴近 vLLM，可新增 physical-slot/refcount mode，不能污染当前轻量 backend。
- `cache_events.csv` / event sink 应保持 streaming 或 stats-only，不允许默认大内存聚合事件明细。

## 8. Finish-Time Materialization 与真实 vLLM 差异

HitFloor 当前规则：

```text
request prefill finish 后，miss blocks 才对后续 request 可见。
```

真实 vLLM 更细：

- request 运行期间 `num_computed_tokens` 会推进。
- 新 full blocks 可以通过 `cache_blocks()` 写入 prefix cache。
- full blocks 不一定等整个 request TTFT 完成才可见。

为什么这个差异重要：

- 长 prompt prefill 可能持续几十秒。
- 如果相同 prefix 的后续请求在第一个长请求尚未完成但部分 full blocks 已经算完时到达，真实系统可能已经可以命中部分 blocks。
- HitFloor finish-time materialization 可能低估长 prefill 场景的 KV hit。

为什么当前仍保留：

- Step5/Step6 的 `batch_aware_hbm_lru` 已冻结该语义。
- finish-time materialization deterministic、低成本、易测试。
- 当前没有真实 per-chunk latency / per-block completion timeline。

工程优化建议：

- 不修改 `batch_aware_hbm_lru`。
- 先新增 materialization policy 接口。
- 后续新增 `batch_aware_hbm_lru_progressive` 或类似模式，按 chunk finish 或 block finish 暴露 full blocks。

## 9. vLLM-Ascend 相关语义

vLLM-Ascend 中需要进入 profile / config guard 的语义：

- CP / PCP / DCP 会影响 batch、通信资源和 effective block size。
- 非 hybrid 场景中，prefix cache 或 chunked prefill 开启时，Ascend 可能强制 block size 为 128。
- hybrid attention + mamba 场景可能由模型相关逻辑决定 runtime block size。
- speculative method 中，`eagle`、`eagle3`、`mtp` 都需要按 use_eagle 类语义处理 cached blocks drop。
- Mooncake / KV transfer 会引入 local cache hit 与 external cache hit 的拆分。

对 HitFloor 的启发：

- `DeploymentProfile` 必须显式表达 CP、speculative、KV transfer、多级 cache、池化等开关。
- 不支持的组合必须由 `ConfigGuard` 阻止，不能猜测。
- local hit / external hit / recomputed tokens 未来应进入统一 metrics schema。

## 10. Mooncake / KV Transfer 启发

vLLM-Ascend Mooncake connector 体现了未来多级缓存和跨实例池化的几个接口点：

- scheduler 侧先查询 external matched tokens。
- allocate 后 connector 更新 request transfer state。
- async KV load 会让 request 进入等待远端 KV 的状态。
- metrics 需要拆分 local prefix hit 与 external KV hit。
- block size、PCP、DCP、remote block metadata 需要一起传递。

对 HitFloor 的未来设计启发：

- 多级 cache backend 应有统一 lookup result：

```text
local_hbm_hit_tokens
local_ddr_hit_tokens
remote_hit_tokens
miss_tokens
kv_load_tokens_by_tier
```

- KV load latency 应由 latency profile 或 Ramulator2 adapter 估算。
- 多实例池化不应改变当前 fixed-routing isolated replay；应新增 pooling backend / connector。

## 11. 对 HitFloor 工程优化的总判断

优先级最高的不是立刻实现所有 vLLM 细节，而是先把语义地基补齐：

1. Profile schema / RunSpec / ConfigGuard。
2. Block size / cache block conversion module。
3. Replay golden tests，保证默认模式不漂移。
4. Materialization policy 接口和 progressive visibility 设计。
5. Latency profile 管理接口。
6. 大 trace 性能和事件大小控制。

这些优化能让后续 Step7+ 的多级 cache、KV load latency、queue simulation、gateway simulation 都建立在稳定结构上。
