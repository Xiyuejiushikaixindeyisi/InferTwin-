# Pre-Batch-C Code Review

## Review Scope

本次评审发生在 Batch C 开发前，输入包括：

- Batch A/B 当前代码。
- AIConfigurator 手册笔记。
- Markov-Infer-Sim 手册笔记。
- 公司内部模型部署方法笔记。

评审对象：

- 当前 HitFloor 代码结构。
- scheduler / latency / cache / request 的接口设计。
- `RequestState` / `ScheduledSlice` / `BatchShape` 数据结构。
- 一条请求进入 HitFloor 后的处理流程。

Batch C 代码尚未开始。

## Findings

### High: Batch C 必须处理 100% prefix hit 的 zero-miss 请求

代码位置：

- [state.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/state.py:51)
- [state.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/state.py:70)
- [vllm_like.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/vllm_like.py:86)
- [batch_shape.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/batch_shape.py:20)

问题：

`RequestState.set_cache_lookup()` 允许 `cached_tokens == prompt_tokens` 且 `miss_tokens == 0`。此时 `remaining_prefill_tokens()` 返回 0。`VllmLikeBatchScheduler` 会跳过该请求，不会生成 `ScheduledSlice`，而 `ScheduledSlice` 又要求 `scheduled_prefill_tokens > 0`。

如果 Batch C 不显式处理这种请求，会出现两种风险：

- full-hit 请求永远不能 finish。
- empty schedule 被误判为 config error。

部署文档强调 prefix cache 是核心收益来源，真实 trace 与 synthetic trace 都可能出现 100% prefix hit。Batch C 不能假设每个请求都有 miss tokens。

建议：

- 在 Batch C 的 cache lookup helper 中，lookup 后立即检查 `state.remaining_prefill_tokens() == 0`。
- 对 zero-miss 请求走 fast-finish path，不进入 scheduler。
- Formula backend 阶段可令 compute TTFT 增量为 0。
- 后续 HBM/DDR load backend 接入后，再为 full-hit 请求叠加 KV load time。
- request metrics 仍要输出 `hbm_hit_tokens == prompt_tokens`、`miss_tokens == 0`、`scheduler_wait_ms` 和 `ttft_ms`。

### High: conservative waiting lookup 不能直接调用当前 scheduler

代码位置：

- [vllm_like.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/vllm_like.py:59)
- [vllm_like.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/vllm_like.py:64)
- [state.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/state.py:66)

问题：

当前 scheduler 在考虑 waiting 队首时会调用 `_tokens_for_request()`，进而调用 `remaining_prefill_tokens()`。这要求 request 已完成 cache lookup。

但我们已经决定：

```text
只对 scheduler 本轮可能考虑的 waiting 队首请求 lookup，不提前 lookup 整个 waiting 队列。
```

因此 Batch C 不能简单把整条 waiting queue 交给 scheduler。否则 scheduler 可能触达尚未 lookup 的 request 并抛错。

建议：

- 在 replay engine 中实现一个 bounded pre-lookup helper。
- helper 只从 waiting 队首开始，按 token budget / seq budget / FCFS 语义向后准备本轮可能被考虑的请求。
- 如果队首无法调度，不能越过队首 lookup 后续请求。
- 不建议在 Batch C 把 cache lookup callback 塞进 scheduler；这会让 scheduler 依赖 cache，破坏职责边界。

### Medium: `BatchShape` 代码注释仍容易让人误解为 simulator input

代码位置：

- [batch_shape.py](/home/zhangxiyue/HitFloor/src/hitfloor/scheduler/batch_shape.py:42)

问题：

当前 `BatchShape` docstring 写的是：

```text
Stable boundary between scheduler replay and latency backends.
```

经过 AIConfigurator / MkSim / 内部署文档重审后，`BatchShape` 应被理解为 scheduler output，而不是外部 simulator input。外部 simulator 需要 adapter/converter 转为 uniform workload。

这不是 Batch C 的功能 bug，但容易让后续开发把 `BatchShape` 直接传给 AIConfigurator/MkSim。

建议：

- Batch C 前或 Batch C 中修改 docstring：

```text
Scheduler output for one replay iteration. External simulators must consume it through an adapter/converter.
```

- Formula backend 可以继续直接使用 `BatchShape`。

### Medium: `ShapeKey` 对 heterogeneous slice 分布不敏感

代码位置：

- [schema.py](/home/zhangxiyue/HitFloor/src/hitfloor/latency/schema.py:10)
- [schema.py](/home/zhangxiyue/HitFloor/src/hitfloor/latency/schema.py:23)
- [formula.py](/home/zhangxiyue/HitFloor/src/hitfloor/latency/formula.py:74)

问题：

当前 `ShapeKey` 只包含 aggregate fields：

```text
batch_size
scheduled_prefill_tokens
scheduled_decode_tokens
max_query_len
total_context_tokens
```

两个 per-slice 分布不同但 aggregate 相同的 batch 会得到同一个 `ShapeKey`。

对当前 Formula backend 来说，这是可接受的，因为公式也只依赖这些 aggregate fields。  
但对 AIConfigurator/MkSim adapter 来说，heterogeneous distribution 会影响转换策略，不能共享同一个 memo key。

建议：

- Batch C 可暂不修改，只要外部 adapter 不接入。
- 在 external adapter 前，为 simulator-specific memo key 增加 `slice_signature` 或基于 `SimulatorPrefillInput` 的 key。

### Medium: Batch C 需要明确 P-side TTFT 假设

代码位置：

- [request.py](/home/zhangxiyue/HitFloor/src/hitfloor/instance/request.py:20)
- [request.py](/home/zhangxiyue/HitFloor/src/hitfloor/instance/request.py:50)

问题：

`SimulationRequest.start_time_ms` 来自 trace 的 `service_start_time`。公司部署文档显示真实性能路径可能是 PD 分离：

```text
router/service -> P node prefill -> KV transfer -> D node decode
```

Batch C 只建模 TTFT/prefill，更接近 P-side replay。如果 trace 中的 `service_start_time` 不是 P 节点开始 prefill 的时间，Batch C 的 `ttft_ms = finish_time - arrival_time` 口径可能和线上 TTFT 不完全一致。

建议：

- Batch C 文档和 metrics 中明确：

```text
arrival_time_ms = trace.service_start_time
Batch C assumes this is the prefill service arrival/start timestamp.
```

- 如果后续 trace 能提供 router arrival、P arrival、D arrival，应扩展 trace schema，而不是在 Batch C 中猜。

### Low: 当前代码结构适合 Batch C，但 replay 包应独立于 legacy `instance.replay`

代码位置：

- [replay.py](/home/zhangxiyue/HitFloor/src/hitfloor/instance/replay.py:38)
- [runner.py](/home/zhangxiyue/HitFloor/src/hitfloor/experiment/runner.py:49)

观察：

现有 Phase1 runner 仍使用 `InfiniteHBMReplayEngine`，这是兼容路径。Batch C 应新增 `src/hitfloor/replay/`，不要继续扩展 `src/hitfloor/instance/replay.py`。

建议：

- 保持 legacy Phase1 不动。
- Batch C 新增 `BatchAwareReplayEngine`。
- Batch D 再通过 runner config 显式切换 replay mode。

### Low: 多级缓存与池化不应提前进入 Batch C

代码位置：

- [infinite_hbm.py](/home/zhangxiyue/HitFloor/src/hitfloor/cache/infinite_hbm.py:23)

观察：

公司部署文档明确区分：

- 多级缓存：单实例内显存/内存/SSD。
- 广义池化：多实例共享 KV pool。

当前 `InfiniteHBMCache` 是每实例独立无限 HBM，符合 Batch C 固定路由、多实例隔离 replay 范围。Batch C 不应为了未来 DDR/pooling 提前引入复杂 cache 抽象。

建议：

- Batch C 继续使用 `InfiniteHBMCache`。
- 但 engine 内部不要把缓存逻辑散落到多处；集中在 lookup/materialization helper，方便后续替换为 finite HBM/DDR/pool。

## Code Structure Review

当前结构整体可以继续 Batch C：

```text
request/     tokenizer, chat template, request parsing, block hashing
cache/       prefix cache implementation
scheduler/   Step4 scheduler config/state/batch shape/vLLM-like scheduling
latency/     formula backend and memo
instance/    legacy Phase1 replay/request
experiment/  current runner
report/      current CSV/summary writers
```

建议 Batch C 新增：

```text
src/hitfloor/replay/
  __init__.py
  event_loop.py
  metrics.py
```

不要把 Batch C 放进 `instance/replay.py`，否则 Phase1 replay 和 Step4 replay 会混在一起。

## Interface Review

### Scheduler Interface

当前 scheduler interface 是可接受的：

```text
schedule(instance_uuid, iteration_id, start_time_ms, waiting, running) -> ScheduleResult
```

优点：

- 不依赖 cache。
- 不依赖 latency backend。
- 只输出 `BatchShape`。

注意：

- Batch C 必须在调用 scheduler 前处理必要 lookup。
- Batch C 必须处理 zero-miss request。

### Latency Interface

Formula backend 可以继续使用：

```text
estimate_iteration(BatchShape) -> LatencyResult
```

但这是 formula-only 接口，不应被视为外部 simulator 的最终接口。

外部 simulator 后续应走：

```text
BatchShape -> converter -> SimulatorPrefillInput -> adapter
```

### Cache Interface

当前 `InfiniteHBMCache.lookup_prefix()` / `materialize()` 足够 Batch C 使用。

需要注意：

- Batch C lookup timing 应是 first-schedule-time。
- materialize timing 应是 request finish_time。
- same iteration 内不能边 schedule 边 materialize。

## Data Structure Review

### RequestState

当前表达能力基本足够 Batch C：

- arrival time
- prompt tokens/blocks
- cache lookup state
- cached/miss tokens
- computed progress
- first scheduled time
- finish time

需要 Batch C 补充的是外部字典：

```text
request_miss_blocks: dict[str, tuple[PrefixBlock, ...]]
request_hit_tokens: dict[str, int]
```

不要把所有 metrics 字段继续塞进 `RequestState`，否则它会变成 report 对象。

### ScheduledSlice

当前修正后表达能力正确：

```text
cached_prefix_tokens
previous_chunk_tokens
scheduled_prefill_tokens
computed_tokens_before
computed_tokens_after
```

这能支撑：

- prefix cache hit。
- chunk carry-over。
- simulator converter 后续扩展。

### BatchShape

当前可作为 scheduler output。

需要改进的只是命名/注释，不需要在 Batch C 前重命名。

## Request Flow Review

当前已实现路径：

```text
TraceRecord
-> parse request_params
-> tokenizer + chat template
-> prompt token ids
-> hash-only PrefixBlock
-> SimulationRequest
-> Phase1 InfiniteHBMReplayEngine
```

Batch C 目标路径应是：

```text
SimulationRequest
-> RequestState(WAITING)
-> first-schedule-time cache lookup
-> zero-miss fast finish OR scheduler admission
-> BatchShape
-> FormulaLatencyBackend
-> finish_time
-> apply_scheduled_tokens
-> request finish
-> materialize miss blocks
-> request/iteration metrics
```

部署方法带来的关键约束：

- batch size 是 request slice 数。
- `max_num_batched_tokens` 是 token budget。
- chunked prefill 是跨 iteration progress。
- prefix cache hit 与 previous chunks 必须分开解释。
- P/D 分离、D-side TPOT、多级缓存和 pooling 不进入 Batch C。

## Must Fix Before Batch C Code

1. 在 Batch C 设计中加入 zero-miss fast finish。
2. 在 Batch C 设计中明确 bounded waiting lookup helper。
3. 修改 `BatchShape` docstring，避免误导外部 simulator input 设计。

## Can Defer

1. `ShapeKey` 增加 slice signature。
2. `SimulatorPrefillInput` 代码实现。
3. AIConfigurator / MkSim adapter。
4. finite HBM / DDR / SSD。
5. Mooncake pooling / cross-instance cache。
6. PD ratio optimizer。
7. Decode TPOT / MTP / graph capture。

## Overall Verdict

当前 HitFloor 的 Batch A/B 基础层可以继续支撑 Batch C，但 Batch C 代码开发前必须把 zero-miss request 和 conservative waiting lookup 写进执行方案，并避免把 `BatchShape` 误用为外部 simulator input。

公司内部署方法没有推翻 Step4 的整体路线；它确认了当前 Step4 应保持为：

```text
fixed-routing, per-instance isolated, P-side-like, infinite-HBM, batch-aware prefill replay
```

多级缓存、池化、PD 分离和 D-side decode 应作为后续阶段扩展，而不是混入 Batch C。
