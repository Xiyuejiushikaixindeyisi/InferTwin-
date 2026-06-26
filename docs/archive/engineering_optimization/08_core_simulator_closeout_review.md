# 工程优化核心仿真器收口 Review

## 1. Review 结论

本次 review 对象是：

```text
核心仿真器
```

不是外围能力。`capacity_sweep.csv`、summary、dashboard、hit floor search、GB 到 block 转换都不属于本次 review 主体。

结论：

```text
工程优化阶段 EO-A 到 EO-G 已完成核心仿真器的第一轮收口。
当前 batch_aware_hbm_lru replay 可作为固定路由、多实例隔离、有限 HBM LRU、prefill-only TTFT 的稳定仿真骨架继续开发。
```

但当前不能宣称已经完全等价于真实 vLLM / vLLM-Ascend。主要原因是：

- 仍然没有真实模型推理和真实 KV tensor。
- 仍然没有 physical KV slots、pinned/refcount、decode/TPOT。
- `CacheBlockConversionPolicy` 已通过 EO-H 贯穿 replay lookup metrics，但仍是 usage cached_tokens accounting，不是 physical KV 逐行为仿真。
- 默认仍采用 finish-time materialization，未启用 progressive full-block visibility。

用户评审结论：

- `CacheBlockConversionPolicy` 贯穿 replay lookup metrics 应作为工程优化 Batch EO-H 收口。
- finish-time materialization 可能低估长 prefill 期间的 block reuse，这一点接受作为后续 replay/cache mode 演进，不阻塞 EO-H。
- Decode / TPOT 仍未建模，这一点接受作为后续 decode-aware replay 演进，不阻塞 EO-H。

EO-H 已完成，执行记录见：

```text
docs/engineering_optimization/09_eo_h_execution.md
```

## 2. 有限 HBM 下一条 Request 的处理顺序

### 2.1 Trace 到 SimulationRequest

入口：

```text
src/hitfloor/experiment/request_builder.py
```

处理顺序：

```text
CSV row
-> TraceRecord
-> parse request_params JSON
-> validate request model
-> tokenizer + chat template
-> prompt token ids
-> tokenizer length guard
-> block size / cache block conversion metadata
-> prefix block hash
-> SimulationRequest
```

关键行为：

- `build_request_build_result_from_config()` 边读 `read_trace_csv()` 边构造请求，不再先持有全量 `TraceRecord`。
- 超过 `max_prompt_tokens` 的请求会触发 `PromptTooLongError`，被记录为 `RejectedTraceRecord`。
- 被拒绝请求不会进入 replay、scheduler、cache lookup、TTFT 或 hit-rate 分母。
- 单次 `ExperimentRunner` 在存在拒绝请求时写出 `rejected_requests.csv`。
- `CapacitySweepRunner` 在 `config_details` 中记录 accepted / rejected count。

当前仍持有：

- accepted `SimulationRequest` 列表。
- 每个 accepted request 的 prefix block hash 链。
- tokenizer 输出的 token ids 会在构造 prefix blocks 时短暂存在。

### 2.2 实例隔离 Replay

入口：

```text
src/hitfloor/replay/event_loop.py
```

处理顺序：

```text
accepted SimulationRequest list
-> group by instance_uuid
-> each instance owns independent cache / clock / waiting queue / running list
-> replay instance independently
-> globally sort request metrics and iteration metrics
```

含义：

- 当前是固定路由、多实例隔离 replay。
- CSV 中的 `instance_uuid` 已经是路由结果。
- HitFloor 不模拟 gateway routing。
- 实例之间 cache 不共享，不互相影响。
- 实例 replay 逻辑独立，但当前实现是串行执行，不做并行加速。

### 2.3 pending -> waiting

每个实例内：

```text
pending = sorted(requests by start_time_ms, request_id)
now_ms = first arrival time
```

当：

```text
request.start_time_ms <= now_ms
```

request 从 `pending` 进入 `waiting`，并创建 `RequestState`。

解释：

- `pending` 表示 trace 中还没有到达模型服务的请求。
- `waiting` 表示请求已经到达当前实例，等待被本实例 scheduler 考虑。
- 这里的 waiting 是 replay 状态机队列，不是业务侧真实排队时间建模。

### 2.4 Cache Lookup

入口：

```text
BatchAwareReplayEngine._prepare_scheduler_frontier()
BatchAwareReplayEngine._ensure_lookup()
HBMCache.lookup_prefix()
```

lookup 时机：

- running request 如果尚未 lookup，先 lookup。
- waiting queue 只对 scheduler 本轮可能考虑到的队首请求做保守 lookup。
- 不提前 lookup 整个 waiting queue。

HBM lookup 行为：

```text
for block in prompt_blocks:
    if block resident:
        hit
    else:
        stop contiguous prefix hit
        remaining blocks are miss
```

发生信号：

- 命中的每个 block 发出 `LOOKUP_HIT`。
- miss suffix 中每个 block 发出 `LOOKUP_MISS`。
- LRU policy 收到 `on_access(reason="lookup_hit")`。

结果写入 `RequestState`：

```text
cached_tokens = lookup.effective_hit_tokens
miss_tokens = lookup.miss_tokens
num_computed_tokens = cached_tokens
cache_lookup_done = true
```

### 2.5 Zero-Miss Fast Finish

如果 lookup 后：

```text
remaining_prefill_tokens == 0
```

request 不进入 scheduler iteration，直接在当前 `now_ms` finish。

输出：

- `scheduled_iteration_count = 0`
- `ttft_ms = finish_time_ms - arrival_time_ms`
- 对完全命中请求，通常 `ttft_ms = scheduler_wait_ms`

### 2.6 vLLM-like Batch Scheduling

入口：

```text
src/hitfloor/scheduler/vllm_like.py
src/hitfloor/scheduler/planning.py
```

调度顺序：

```text
running requests first
then waiting queue FCFS
respect max_num_batched_tokens
respect max_num_seqs
respect chunked prefill threshold
```

输出：

```text
BatchShape
  instance_uuid
  iteration_id
  start_time_ms
  request_slices
  scheduled_prefill_tokens
  batch_size
```

如果还有 pending work，但 scheduler 产生 empty batch，HitFloor 直接失败，不自动跳过请求，也不自动开启 chunked prefill。

### 2.7 Latency Estimate 与 Finish Event

入口：

```text
src/hitfloor/latency/
BatchAwareReplayEngine._estimate_latency()
ShapeMemo
```

当前可用 backend：

- `fitted_ttft`
- `formula`
- `serving_latency_profile`

当前默认语义：

```text
finish_ms = now_ms + latency_backend.estimate_iteration(BatchShape).duration_ms
```

`ServingLatencyProfile` 当前组合：

```text
duration_ms = queue_ms + ttft_ms + kv_load_ms
```

但默认：

- `ttft_ms` 来自 fitted TTFT。
- `queue_ms = 0`。
- `kv_load_ms = 0`。
- `decode / TPOT = not_modeled_in_current_replay`。

### 2.8 Scheduled Slice 完成

入口：

```text
RequestState.apply_scheduled_tokens()
```

在 iteration finish time：

```text
num_computed_tokens += scheduled_prefill_tokens
scheduled_iteration_count += 1
```

如果：

```text
num_computed_tokens == prompt_tokens
```

则 request 进入 `FINISHED`。

### 2.9 Finish-Time Materialization

入口：

```text
src/hitfloor/cache/materialization.py
FinishTimeMaterializationPolicy
HBMCache.materialize()
```

当前默认：

```text
request prefill 完成后，miss blocks 一次性 materialize
```

发生信号：

- 新 resident block 发出 `MATERIALIZE`。
- 如果 HBM resident blocks 超过 capacity，先由 eviction policy 选 victim。
- victim 被移除时发出 `EVICT`。
- LRU policy 收到 `on_insert()` / `on_remove()` / `on_access()`。

### 2.10 Metrics 与 Event Sink

输出：

- `BatchAwareRequestMetrics`
- `IterationMetrics`
- `CacheEventStats`
- optional `cache_events`

默认：

```text
StatsOnlyCacheEventSink
```

含义：

- 保留 event stats。
- 不保留 event payload。
- 大 trace 默认不会在内存里堆 cache event 明细。

需要明细时：

- 小测试使用 `InMemoryCacheEventSink`，但默认最多 100000 条。
- 正式大 trace dump 使用 `CsvCacheEventWriter` streaming 写出。

## 3. 有限 HBM 下发生的信号

### 3.1 Request Build 信号

| 信号 | 触发条件 | 输出 |
| --- | --- | --- |
| accepted request | tokenization 和 config guard 通过 | `SimulationRequest` |
| rejected request | `prompt_tokens > max_prompt_tokens` | `RejectedTraceRecord` / `rejected_requests.csv` |
| hard failure | JSON/schema/config/model mismatch | `ValueError` |

### 3.2 Cache Event 信号

| 信号 | 触发条件 | 说明 |
| --- | --- | --- |
| `LOOKUP_HIT` | resident contiguous prefix block 命中 | 更新 LRU recency |
| `LOOKUP_MISS` | prefix 第一个 miss 后的 suffix blocks | 不更新 resident cache |
| `MATERIALIZE` | finished request 的 miss block 写入 HBM | finish-time visible |
| `EVICT` | materialize 时 resident 超 capacity | victim 由 stateful eviction policy 选择 |

### 3.3 Replay Metrics 信号

| 输出 | 说明 |
| --- | --- |
| `scheduler_wait_ms` | first scheduled time - arrival time |
| `ttft_ms` | finish time - arrival time |
| `hbm_hit_tokens` | 当前 HBM lookup 命中 token 数 |
| `miss_tokens` | 当前 request 需要 prefill compute 的 token 数 |
| `scheduled_iteration_count` | request 被切成多少个 prefill iteration |
| `cache_event_stats` | cache event 聚合统计 |

## 4. 与 vLLM / vLLM-Ascend 的主要区别

| 主题 | HitFloor 当前行为 | vLLM / vLLM-Ascend 行为 | 影响 |
| --- | --- | --- | --- |
| 真实推理 | 不部署模型，用 latency backend 估算 iteration duration | 执行真实 kernel、attention、MLP、decode | 无法直接反映 kernel overlap、硬件瓶颈和真实 queue |
| KV 存储 | 只保存 hash-only block metadata | 保存 physical KV tensors / slots / block table | 无法模拟真实显存碎片、slot allocation、pinned/refcount |
| 路由 | 使用 trace 中 `instance_uuid`，固定路由 | 真实 gateway / router 选择实例 | 不评估路由策略 |
| 机器侧排队 | 不建模真实 admission queue | 实例服务有真实请求队列和资源竞争 | `scheduler_wait_ms` 只是 replay scheduler wait，不是完整服务排队 |
| Prefix lookup | 保守地对本轮可能调度的 frontier 做 lookup | scheduler 内部查询 computed/cached blocks | 基本方向一致，但不是源码逐行复刻 |
| Block visibility | finish-time materialization | vLLM 可能在运行中让 full blocks 可见 | 长 prefill 请求的 reuse 可能被低估 |
| cached tokens | HBM lookup 当前按 `prompt_blocks` 命中 token_count 求和 | vLLM 使用 full block、`prompt_tokens - 1`、CP/MTP/hybrid 对齐等规则 | partial-block / MTP / CP 场景可能不一致 |
| Eviction | materialize 时超 capacity，policy 选 victim | BlockPool / KVCacheManager 在 allocation/cache 阶段维护可驱逐队列 | 当前可做策略对比，但不模拟 physical allocation |
| Decode / TPOT | 不建模 | decode batch 与 prefill 共享或分离资源 | PD 混部和长 decode 场景会低估干扰 |
| KV load latency | 默认 0 | 多级缓存 / external KV transfer 有真实 load cost | DDR/remote cache 还不能评估 TTFT |
| 多级 cache | 只有 HBM | 可有 HBM/DDR/remote/pooling | DDR/SSD/Mooncake 尚未实现 |
| 并行 replay | 多实例逻辑隔离，当前串行执行 | 多实例真实并发 | 仿真结果语义不受影响，但大 trace 性能仍可优化 |

## 5. 代码 Review 发现

### Resolved by EO-H: CacheBlockConversionPolicy 已贯穿 HBM lookup metrics

位置：

```text
src/hitfloor/cache/cache_block_conversion.py
src/hitfloor/instance/request.py
src/hitfloor/request/block_hasher.py
src/hitfloor/cache/hbm_lru.py
```

现状：

- `CacheBlockConversionPolicy` 能计算 `max_cache_hit_length = prompt_tokens - 1`、`effective_block_size`、`speculative_drop_blocks` 和 `cached_tokens`。
- `build_simulation_request()` 会保存 `block_conversion_result`。
- 但 `build_prefix_blocks()` 仍会按 token ids 构造所有 prompt blocks，包括最后 partial block。
- `HBMCache.lookup_prefix()` 返回所有 resident contiguous `prompt_blocks`，并按 block `token_count` 汇总 hit/miss。

风险：

- partial-block hit 可能被计入。
- 完整 prompt 二次命中时，最后 token / 最后 block 可能被计入。
- MTP/EAGLE/EAGLE3 的 one-block drop 不一定体现在 replay hit metrics。
- CP / hybrid cache group 的 cached-token 统计可能没有完全作用于实际 lookup 结果。

建议：

- 新增 `CachedTokenAccounting` 或把 `CacheBlockConversionPolicy` 接入 `LookupMetrics.from_result()` / cache lookup result。
- 增加 partial block、`prompt_tokens - 1`、MTP drop、PCP/DCP、hybrid LCM 的 replay 集成测试。
- 在修复前，不要宣称 HitFloor 已经完全对齐 vLLM cached_tokens usage。

EO-H 结果：

- 已新增 `account_prefix_lookup()` / `AccountedLookupResult`。
- 已让 batch-aware replay 和 infinite replay 使用 accounted metrics。
- 已区分 `miss_tokens` 与 `materialization_blocks`。
- 已补充 partial block、`prompt_tokens - 1`、speculative drop、PCP/DCP 和 eviction 后 raw match 限制测试。

### Post-EO: Finish-time materialization 会低估长 prefill 期间的 block reuse

位置：

```text
src/hitfloor/cache/materialization.py
src/hitfloor/replay/event_loop.py
```

现状：

- request 完成 prefill 后，miss blocks 一次性 materialize。
- 运行中的 full blocks 不可见。

风险：

- 对 128K/200K 长 prompt，如果 prefill 时间达到几十秒，真实 vLLM 中较早完成的 full blocks 可能已经可复用。
- 当前模式可能低估高并发长 prompt trace 的 KV hit。

建议：

- 保持当前 `batch_aware_hbm_lru` frozen 语义。
- 若要改进，新增 `ProgressiveChunkMaterializationPolicy` 和新 replay/cache mode，例如 `batch_aware_hbm_lru_progressive`。
- 用户已接受该项作为后续演进，不阻塞工程优化 EO-H。

### Post-EO: Decode / TPOT 未建模

位置：

```text
src/hitfloor/scheduler/vllm_like.py
src/hitfloor/latency/profile.py
```

现状：

- `scheduled_decode_tokens` 恒为 0。
- `ServingLatencyProfile` 只记录 `decode_mode = not_modeled_in_current_replay`。

风险：

- PD 混部或 prefill/decode 共享实例时，decode 会影响 prefill TTFT。
- TPOT 和 decode KV growth 影响吞吐、排队和 cache pressure。

建议：

- 后续新增 decode-aware scheduler / replay mode。
- 不在当前 prefill-only mode 中静默混入 decode。
- 用户已接受该项作为后续演进，不阻塞工程优化 EO-H。

### P2: Request build 仍不是 true streaming

位置：

```text
src/hitfloor/experiment/request_builder.py
src/hitfloor/replay/event_loop.py
```

现状：

- 已避免持有全量 `TraceRecord`。
- 但仍持有全部 accepted `SimulationRequest`，并按实例分组 replay。

风险：

- 对 11G trace 和更长 prompt，accepted request 的 prefix block metadata 仍可能占较多内存。

建议：

- 后续评估 per-instance shard build。
- 后续评估 streaming tokenizer + rolling block hash。
- 后续评估多实例并行 replay，但必须保持 deterministic output。

### P2: Physical KV slot / pinned / refcount 未建模

位置：

```text
src/hitfloor/cache/hbm_lru.py
```

现状：

- HBMCache 只保存 hash-only metadata。
- 不模拟 physical block table、free queue、pinned/refcount、copy-on-write。

风险：

- 无法发现真实 allocation failure。
- 无法评估 pinned blocks 对 eviction 的影响。

建议：

- 若未来需要贴近 vLLM BlockPool，可新增 physical slot mode，不改变当前 `batch_aware_hbm_lru`。

## 6. 测试结果

最近一次工程优化收口验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest
152 passed

.venv/bin/python -m ruff check src tests
All checks passed

.venv/bin/python -m ruff format --check src tests
122 files already formatted

git diff --check
passed
```

覆盖重点：

- EO-A golden replay regression。
- 多实例隔离。
- finish-time materialization。
- zero-miss fast finish。
- HBM LRU hit/miss/materialize/evict event stats。
- profile schema / ConfigGuard。
- block size / cache block conversion pure module。
- profile-aware request build。
- materialization policy interface。
- ServingLatencyProfile。
- 大 trace 下 tokenizer 长度拒绝和 event sink 安全。
- Step5 finite HBM runner。
- Step6 capacity sweep runner / CLI。

## 7. 工程收口状态

已完成：

- `batch_aware_hbm_lru` 作为有限 HBM LRU baseline replay mode。
- HBM stateful eviction policy interface。
- stats-only cache event default。
- streaming `cache_events.csv` writer。
- tokenizer-stage long request rejection。
- profile schema / ConfigGuard / block conversion foundation。
- `ServingLatencyProfile` latency composition interface。
- 核心模块和外围 report/CLI 边界基本清晰。

尚未完成：

- vLLM cached_tokens usage 语义完全贯穿 replay lookup metrics。
- progressive full-block visibility。
- decode / TPOT。
- KV load latency。
- 多级 cache backend。
- gateway simulation。
- 实例侧真实排队 simulation。
- physical KV slots / pinned / refcount。
- true streaming request build。
- 多实例并行 replay。

## 8. 收口建议

建议工程优化阶段可以有条件收口，条件是：

```text
当前核心仿真器继续作为 fixed-routing, multi-instance isolated, prefill-only,
finite HBM LRU replay baseline 使用。
```

进入下一阶段前必须明确：

- Batch EO-H 已完成，cached-token accounting 已贯穿 replay lookup metrics。
- 如果下一阶段研究长 prompt 高复用场景，应优先设计 progressive materialization mode。
- 如果下一阶段研究 PD 混部、吞吐或 TPOT，应新增 decode-aware replay，不要在当前 mode 上打补丁。

最终判断：

```text
HitFloor 当前核心仿真器已经具备稳定骨架；
但它仍是离线近似仿真器，不是 vLLM/vLLM-Ascend 的逐行为仿真。
```
