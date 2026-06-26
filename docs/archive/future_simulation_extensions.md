# HitFloor Future Simulation Extensions

本文记录 HitFloor 在 Step1-Step5 仿真骨架之上的长期扩展方向。

核心定位：

```text
Step1-Step5 的阶段目标是搭建可扩展仿真骨架。
后续能力应作为独立仿真层、策略类、adapter 或 cache backend 接入，
不要把所有逻辑混进单个 replay loop。
```

当前 Step5 仍处于技术路线和代码编写方案讨论阶段。本文描述的是长期架构边界，不表示这些能力已经实现。

## 1. Gateway Simulation

未来 trace 可能不包含 `instance_uuid`。

在这种情况下，可以在现有骨架前增加 gateway 仿真层：

```text
trace request
  -> gateway policy
  -> selected instance_uuid
  -> instance replay
```

gateway 层负责：

- 请求路由策略仿真。
- 多实例负载分配。
- 基于 cache locality 的路由策略。
- 基于租户、模型、SLO 或负载的路由策略。

gateway 层不应修改实例内 replay、cache lifecycle 或 latency backend 的既有语义。

## 2. Instance-Side Queueing Simulation

当前骨架默认不引入外部排队时间。

后续可以在实例侧增加排队仿真层：

```text
instance arrival
  -> queueing policy
  -> scheduler admission
  -> batch-aware replay
```

可扩展方向：

- 实例侧请求排队时间建模。
- admission control。
- priority queue。
- per-tenant fairness。
- SLO-aware scheduling。

chunked prefill 阶段也可以引入更细粒度的 chunk 排队与调度策略：

```text
request
  -> prompt chunks
  -> chunk queue
  -> scheduler iteration
```

这类能力应新增 queueing/scheduling policy 类型，不应改变 README 中已冻结的 `batch_size`、`ScheduledSlice`、`BatchShape` 语义。

## 3. Eviction Algorithm Simulation

Step5 已将淘汰逻辑设计为 evictor / eviction policy。

第一版默认：

```text
LRUEvictor
```

后续可以新增：

- T-LRU。
- TTL-based eviction。
- frequency-aware eviction。
- cost-aware eviction。
- SLO-aware eviction。
- tenant-aware eviction。
- sparse-attention-aware eviction。

原则：

- 新算法新增 evictor 类。
- 不改变 `LRUEvictor` 已冻结语义。
- cache 层负责调用 evictor 和产生事件。
- evictor 不写 report、不读 CLI、不修改 cache。

## 4. Multi-Tier Cache Simulation

未来 HitFloor 会支持多级存储：

```text
HBM -> DDR -> SSD
```

三层都可能产生 KV hit，但 load latency 不同：

- HBM hit：最快。
- DDR hit：需要 DDR KV load time。
- SSD hit：需要 SSD KV load time。
- miss：需要 prefill compute time。

当前骨架只有 HBM hit，KV load latency 暂不计。

后续多级缓存建议拆成两个独立问题：

1. cache lookup / lifecycle：

```text
PrefixLookupResult(
  hbm_hit_blocks,
  ddr_hit_blocks,
  ssd_hit_blocks,
  miss_blocks,
)
```

2. latency backend：

```text
ttft =
  scheduler_wait
  + prefill_compute_time(miss_tokens)
  + hbm_load_time(hbm_hit_tokens)
  + ddr_load_time(ddr_hit_tokens)
  + ssd_load_time(ssd_hit_tokens)
```

多级缓存不应通过修改 HBM-only 字段含义来实现，应新增 cache tier schema 和 latency input schema。

## 5. Cache Management for Sparse Attention

稀疏注意力逐渐成为主流后，cache 管理不一定仍然等价于 full-prefix contiguous cache。

未来可以接入新的 cache 管理仿真：

- sliding window cache。
- chunked local attention cache。
- sink token cache。
- hybrid attention cache groups。
- sparse-attention-aware block retention。
- attention-pattern-aware eviction。

这类能力应新增 cache manager / cache coordinator 类型，而不是改变当前 full-prefix cache 的含义。

推荐方向：

```text
FullPrefixCacheManager
SparseAttentionCacheManager
HybridAttentionCacheCoordinator
```

## 6. Mooncake Multi-Instance Pooling

未来可以仿真 Mooncake 风格的多实例 KV 池化。

在该模式下，请求可能跨实例命中：

```text
instance-local HBM
  -> pooled DDR / remote memory
  -> other instance KV
```

可扩展方向：

- 跨实例 cache lookup。
- pooling index。
- remote KV availability。
- remote KV load latency。
- 池化容量和淘汰策略。
- 一致性和可见性规则。

该能力不应改变当前固定路由、多实例隔离 replay 的语义。应新增 pooling cache backend 或 connector。

## 7. 架构原则

后续扩展必须遵守：

- 新语义新增类型、接口、adapter 或 backend。
- 不静默改变 README 中冻结字段含义。
- report 只序列化 lib 输出，不重算核心仿真逻辑。
- gateway、queueing、scheduler、cache、latency backend 保持分层。
- 每个新策略必须有独立单测和端到端合成数据验证。

