# Engineering Optimization Core Simulator Review

评审时间：2026-06-26

评审对象：

- `src/infertwin/`
- `tests/`
- `scripts/`
- `configs/`
- `docs/archive/engineering_optimization/`
- 工程优化后主文档与全局记忆

本次评审目标：

1. 基于 `ruff` 和测试结果，评估工程优化后的核心仿真器质量。
2. 从功能完善度、代码结构、测试覆盖、函数质量、性能、可维护性、可扩展性等方面给出审查意见。
3. 明确当前骨架是否可以作为后续 InferTwin 扩展基础，以及进入下一阶段前建议优先处理的问题。
4. 表明未完成方案。

Follow-up 更新：

- 2026-06-26：已清理未接入主链路、coverage 为 0 的 scaffold / legacy 源码模块。
- 本次清理只移除无活跃 import、无 package export 的历史骨架文件，不改变 `batch_aware_hbm_lru` replay 能力。

## 1. 主要发现

### P1. Progressive block visibility 仍未实现，长 prefill 高复用场景会低估命中

证据：

- `BatchAwareReplayEngine` 只在 request 状态变为 `FINISHED` 后 materialize blocks：`src/infertwin/replay/event_loop.py:248-263`。
- cache lookup 发生在 scheduler frontier 准备阶段，使用当前 cache resident 状态：`src/infertwin/replay/event_loop.py:324-375`。
- 当前 `MaterializationPolicy` 默认仍为 `FinishTimeMaterializationPolicy`，即 finish-time materialization。

影响：

- 对 128K/200K 长 prompt，如果 prefill 持续几十秒，真实 vLLM / vLLM-Ascend 可能在 full block 完成后逐步让 block 可见。
- 当前 InferTwin 会等整个 request prefill finish 后才让 miss blocks 可见，因此可能低估长 prefill 场景中的 block reuse。
- 这会影响长 prompt、高并发、P80/P90 block reuse interval 较短场景下的 hit rate 和 TTFT 估计。

结论：

- 这是必须补齐的核心能力。
- 用户已确认可以放到 Step7 之后实现。
- 不应修改现有 `batch_aware_hbm_lru` 的 frozen 语义，应新增 `ProgressiveChunkMaterializationPolicy` 和新 replay/cache mode，例如 `batch_aware_hbm_lru_progressive`。

### P2. Request build 仍不是 true streaming，大 trace 下内存压力仍需专项处理

证据：

- request build 已经不再先持有全量 `TraceRecord`，但仍会把 accepted `SimulationRequest` append 到列表：`src/infertwin/experiment/request_builder.py:141-178`。
- 每个 `SimulationRequest` 仍持有 prefix block hash 链。
- replay 内每个实例还会构造 `states_by_id`、`requests_by_id`、`lookup_by_id` 等 per-instance 状态：`src/infertwin/replay/event_loop.py:97-107`。

收口状态：

- 工程优化 EO-G 已收掉最危险的一半：trace reader 不再一次性持有全量 `TraceRecord`，tokenizer 阶段能拒绝超长 prompt，cache event 默认使用 stats-only sink。
- EO-G 没有收口 true streaming replay。当前核心 runner 仍需要 accepted `SimulationRequest` 集合，以保证固定路由、多实例隔离、capacity sweep build-once/reuse-requests 和 deterministic sort。
- 因此该问题不是当前 replay 正确性 blocker，但仍是 11G 级大 trace 的专项架构任务。

影响：

- 对 11G CSV、几万条请求、单请求 32K 到 200K tokens 的 trace，内存风险已经降低，但没有消失。
- 当前 design 适合工程优化后的可复现 replay，不是最终大 trace streaming replay。

建议：

- 后续做 per-instance sharding、spooling 或 streaming request build。
- 继续保持 deterministic sort 和实例隔离。
- 在实现 true streaming / 并行 replay 前，先定义 output ordering、metric aggregation 和 capacity sweep reuse 不变量。

### P2. Decode / TPOT 未建模，PD 混部场景不能输出强结论

证据：

- `ServingLatencyProfile` 当前只组合 `queue_ms + ttft_ms + kv_load_ms`，并明确记录 decode / TPOT 不建模：`src/infertwin/latency/profile.py:107-137`。
- `LatencyResult.details` 中写入 `decode_mode` 与 `tpot_mode = not_modeled_in_current_replay`：`src/infertwin/latency/profile.py:162-179`。

影响：

- 对 PD 分离场景，decode 对 prefix cache hit 和 prefill TTFT 的影响通常较小，可以接受当前 prefill-only baseline。
- 对 PD 混部模型，decode batch 可能占用 iteration / device 资源，TPOT 和 decode KV growth 也会影响 cache pressure。

结论：

- 当前保持 pending 是合理的。
- 只有出现明确 Decode 建模需求，且目标部署明确是 PD 混部时，才建议新增 decode-aware scheduler / replay mode。
- 届时输入 trace 应新增每条请求输出 token 数。

### P2. 若干 scaffold / legacy 模块曾是 0% coverage，已清理

证据来自 coverage：

| 模块 | 覆盖率 | 说明 |
| --- | ---: | --- |
| `cache/block.py` | 0% | 当前未接入核心 replay |
| `cache/policy.py` | 0% | 当前未接入核心 replay |
| `cache/simulator.py` | 0% | 当前未接入核心 replay |
| `experiment/metrics.py` | 0% | 未接入 aggregation 主链路的旧 helper |
| `experiment/search.py` | 0% | hit floor search 仍是外围未来能力 |
| `instance/batcher.py` / `instance/event.py` / `instance/instance.py` | 0% | 早期 scaffold / legacy 边界 |
| `latency/lookup.py` | 0% | 当前未接入 latency 主链路 |

处理结果：

- 已删除上述未接入主链路的 scaffold / legacy 源码文件，包括额外发现的 `experiment/metrics.py` 未使用 helper。
- 删除前已确认这些文件没有从 package `__init__` 暴露，且没有被 `src/`、`tests/`、`scripts/` 的现行 replay 路径引用。
- 历史文档中的提及保留为历史记录，不作为当前代码入口。

影响：

- 减少新同事误读成本。
- 不改变当前核心 replay 正确性。
- 如果未来需要 lookup table latency、generic cache simulator、hit floor search，应按新的产品边界和 schema 重新引入，而不是复活旧 scaffold。

### P3. 核心文件规模可接受，但需要持续监控职责膨胀

当前行数：

| 文件 | 行数 | 评价 |
| --- | ---: | --- |
| `replay/event_loop.py` | 456 | 核心状态机集中，当前可接受 |
| `experiment/sweep.py` | 442 | sweep orchestration 偏大但职责仍清楚 |
| `experiment/runner.py` | 354 | runner 仍在可维护范围 |
| `config/profiles.py` | 392 | profile schema 集中，当前可接受 |
| `cache/hbm_lru.py` | 233 | 清晰 |
| `cache/cached_token_accounting.py` | 167 | 清晰 |

结论：

- 文件规模不构成本轮 blocker。
- 后续如果继续在 `event_loop.py` 中加入 progressive visibility、decode-aware replay、multi-tier cache，必须新增 policy / mode / backend，不能继续把逻辑堆进同一个状态机函数。

## 2. 结论摘要

工程优化后的核心仿真器质量明显优于 Step1-Step5 阶段，已经具备作为后续 InferTwin 扩展基础的条件。

当前已形成稳定 baseline：

```text
fixed-routing
multi-instance isolated
prefill-only
finite HBM LRU
vLLM-like cached_tokens accounting
fitted / serving latency profile
stats-safe cache events
profile-aware request build foundation
```

可以进入 Step7，但进入下一阶段时必须继续遵守：

```text
先声明：本阶段开发核心仿真器，还是外围能力。
```

当前不建议在进入 Step7 前继续阻塞于 Decode / TPOT；但 progressive block visibility 已被确认为必须修改，应在 Step7 后作为独立核心能力优先设计。

## 3. 客观检查结果

### 3.1 Ruff Check

命令：

```bash
.venv/bin/python -m ruff check src tests scripts
```

结果：

```text
All checks passed!
```

### 3.2 Ruff Format

命令：

```bash
.venv/bin/python -m ruff format --check src tests scripts
```

结果：

```text
117 files already formatted
```

### 3.3 Pytest

命令：

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

结果：

```text
152 passed
```

### 3.4 Coverage

命令：

```bash
PYTHONPATH=src .venv/bin/python -m pytest --cov=infertwin --cov-report=term-missing
```

结果：

```text
152 passed
TOTAL 2914 statements, 211 missed, 93% coverage
```

清理后没有剩余“有语句但 0% coverage”的现行源码模块。

核心模块覆盖情况：

| 模块 | 覆盖率 | 评价 |
| --- | ---: | --- |
| `cache/hbm_lru.py` | 100% | 有限 HBM LRU 覆盖充分 |
| `cache/cache_block_conversion.py` | 100% | cached-token pure conversion 覆盖充分 |
| `cache/cached_token_accounting.py` | 94% | EO-H 关键 accounting 覆盖较好 |
| `cache/event_sink.py` | 97% | 大 trace event safety 覆盖充分 |
| `replay/event_loop.py` | 96% | 核心 batch-aware replay 状态机覆盖较好 |
| `replay/metrics.py` | 99% | replay metric 构造覆盖充分 |
| `scheduler/vllm_like.py` | 91% | scheduler 主路径覆盖较好 |
| `scheduler/queue.py` | 98% | waiting queue abstraction 覆盖充分 |
| `experiment/runner.py` | 95% | runner 主路径覆盖较好 |
| `experiment/sweep.py` | 90% | capacity sweep orchestration 覆盖较好 |
| `experiment/request_builder.py` | 91% | profile-aware request build 覆盖较好 |
| `config/profiles.py` | 91% | profile schema 覆盖较好 |
| `config/guard.py` | 98% | guard foundation 覆盖充分 |
| `latency/profile.py` | 88% | ServingLatencyProfile 覆盖可接受 |

## 4. 功能完善度评审

### 已完成能力

- routed CSV trace reader。
- strict OpenAI-style request parser。
- tokenizer / chat template registry。
- GLM-5 tokenizer profile。
- hash-only prefix block generation。
- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill approximation。
- first-schedule-time cache lookup。
- bounded waiting lookup frontier。
- HBM LRU block lifecycle。
- stateful eviction policy。
- stats-only event sink 和 streaming cache event writer。
- profile schema / RunSpec / ConfigGuard foundation。
- profile-aware request build。
- tokenizer-stage long request rejection。
- BlockSizeResolver / CacheBlockConversionPolicy。
- EO-H replay-facing cached_tokens accounting。
- ServingLatencyProfile。
- capacity sweep runner 和 report/export。

### 未完成能力

- progressive block visibility。
- DDR / SSD / multi-tier cache。
- KV load latency。
- gateway routing simulation。
- instance-side real queue simulation。
- decode-aware scheduler / TPOT。
- physical KV slots / pinned / refcount。
- heterogeneous instance cluster。
- cross-instance KV pooling。
- production AIConfigurator / MkSim / Ramulator2 adapter。
- GB / GiB 到 block 数转换外围工具。
- hit floor search / P90 target matching 外围能力。

## 5. 代码结构评审

整体结构清晰，核心与外围边界比 Step1-Step5 更明确：

- `cache/` 负责 cache backend、events、eviction、materialization、block conversion、cached-token accounting。
- `scheduler/` 负责 vLLM-like scheduling、planning helper、waiting queue。
- `replay/` 负责 event loop 和 replay metrics。
- `latency/` 负责 fitted TTFT、formula backend、ServingLatencyProfile。
- `experiment/` 负责 request build、runner、capacity sweep orchestration。
- `report/` 负责 CSV / Markdown 外围导出。
- `cli/` 和 `scripts/` 没有承担核心 replay 语义。

值得肯定的结构改进：

- `account_prefix_lookup()` 把 raw cache resident 与 usage cached_tokens 解耦：`src/infertwin/cache/cached_token_accounting.py:37-93`。
- `LookupMetrics` 保留 raw 与 accounted 信息，并新增 `materialization_blocks`：`src/infertwin/replay/metrics.py:18-56`。
- `BatchAwareReplayEngine` materialize 使用 `materialization_blocks`，避免 usage miss tokens 与 raw miss blocks 混淆：`src/infertwin/replay/event_loop.py:248-263`。
- `CapacitySweepRunner` 对 token invariant 做显式检查：`src/infertwin/experiment/sweep.py:305-312`。

## 6. 测试覆盖评审

测试质量较好，尤其是核心语义测试：

- golden replay regression。
- finite HBM LRU hit/miss/materialize/evict。
- materialization policy。
- cached-token accounting。
- profile schema / ConfigGuard。
- request builder rejection path。
- ServingLatencyProfile。
- capacity sweep runner / CLI。
- benchmark smoke test。

当前测试缺口：

- progressive block visibility 尚未设计，因此无测试。
- decode-aware replay 尚未设计，因此无测试。
- multi-tier cache / KV load latency 尚未设计，因此无测试。
- stale scaffold 模块无测试，建议归档或标注。
- large trace 只有 smoke / benchmark harness，尚无真实 11G 级别性能验收。

## 7. 函数质量与可维护性评审

总体评价：良好。

优点：

- 核心计算逻辑大多有 typed dataclass 和明确 schema。
- 错误路径多数显式失败，不静默吞关键配置错误。
- report/export 没有重算核心 replay 语义。
- EO-H 后 raw event 和 usage metrics 的口径分离是重要维护性提升。

风险：

- `replay/event_loop.py` 已是核心状态机中心，后续不要继续塞 progressive / decode / multi-tier 分支。
- `experiment/sweep.py` 继续扩展 parallel sweep 或 event aggregation 时，应新增 execution backend 或 aggregation helper。
- `config/profiles.py` 后续字段会继续增多，必要时应按 model / hardware / deployment / instance schema 拆分。

## 8. 性能评审

已完成优化：

- request build 不再持有全量 `TraceRecord`。
- waiting queue 抽象替代了主路径 `list.pop(0)`。
- cache event 默认 stats-only，避免大 trace 下堆内存。
- `InMemoryCacheEventSink` 有事件数上限。
- capacity sweep 复用 request build。

仍需关注：

- accepted `SimulationRequest` 仍全部常驻内存。
- prefix block hash 链仍常驻内存。
- 多实例 replay 当前串行。
- event detail 虽已默认关闭，但指定 capacity dump 时仍可能生成大文件。
- true streaming tokenizer / rolling block hash 尚未实现。

建议：

- 下一次大 trace 专项前定义 benchmark 基线：requests/s、iterations/s、cache_events/s、peak memory、capacity sweep elapsed time。
- 在并行 replay 前先固定 deterministic output 不变量。

## 9. 可扩展性评审

当前骨架适合作为后续扩展基础。

已经具备的扩展点：

- `PrefixCache` backend。
- `HBMEvictionPolicy` / `HBMEvictor`。
- `MaterializationPolicy`。
- `BatchLatencyBackend`。
- `ServingLatencyProfile` component。
- `RunSpec` / profile schema / ConfigGuard。
- `account_prefix_lookup()` accounting layer。
- external adapter boundaries。
- `CapacitySweepRunner` 与 report/export 分离。

建议后续扩展方式：

- multi-tier cache：新增 cache backend / tier lookup result，不改 HBM-only mode。
- progressive materialization：新增 policy + replay/cache mode。
- decode-aware replay：新增 scheduler / replay mode，不污染 prefill-only mode。
- gateway simulation：在 fixed-routing replay 前新增 routing layer，不复用 `instance_uuid` 字段语义。
- GB to block converter：作为外围 CLI/report 能力，只输出 `hbm_capacity_blocks`。

## 10. 是否具备进入下一阶段条件

结论：

```text
具备进入 Step7 的条件。
```

理由：

- lint / format / pytest / coverage 基线良好。
- 核心 replay 主链路有 golden 和集成测试保护。
- cached_tokens accounting 已对齐 vLLM-like usage 口径。
- 核心与外围能力边界清楚。
- 工程优化文档已归档，主文档和全局记忆已更新。

进入下一阶段前建议优先处理：

1. 明确 Step7 是核心仿真器能力还是外围能力。
2. 如果 Step7 涉及长 prompt / 高复用准确性，优先设计 progressive block visibility。
3. 如果 Step7 涉及 PD 混部或 decode 资源竞争，再开启 Decode / TPOT 建模。
4. 清理或标注 0% coverage scaffold 模块，减少误读。

## 11. 未完成方案

### 11.1 必须完成但可放到 Step7 后

- progressive block visibility / progressive materialization。

建议方案：

```text
ProgressiveChunkMaterializationPolicy
batch_aware_hbm_lru_progressive
```

关键要求：

- 只 materialize full blocks。
- 同一 iteration finish 后才对下一轮 lookup 可见。
- 保持 `batch_aware_hbm_lru` finish-time 语义不变。
- 区分 raw cache event、usage cached_tokens、materialization timing。

### 11.2 Pending

- Decode / TPOT。

开启条件：

- 有明确 Decode 建模需求。
- 目标部署是 PD 混部。
- 输入 trace 增加每条请求输出 token 数。

### 11.3 后续核心能力

- multi-tier cache backend。
- KV load latency。
- instance-side queue simulation。
- gateway simulation。
- heterogeneous instance cluster。
- cross-instance KV pooling。
- physical KV slots / pinned / refcount。
- cache 管理和稀疏注意力相关策略。

### 11.4 后续外围能力

- GB / GiB 到 block 数转换工具。
- deployment script to profile config。
- hit floor search / P90 target matching。
- dashboard / notebook。

## 12. 最终判断

工程优化后的 InferTwin 核心仿真器已经从“可用骨架”提升为“可扩展核心平台”。

当前最稳的定位是：

```text
固定路由、多实例隔离、prefill-only、有限 HBM LRU、vLLM-like cached_tokens accounting 的离线 replay baseline。
```

它可以作为后续 InferTwin 扩展基础；但对长 prefill 复用、PD 混部 decode、多级 cache 和真实物理 KV 管理的结论仍需保守输出。
