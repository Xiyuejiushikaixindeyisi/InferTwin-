# 工程优化代码编写方案

## 1. 本阶段声明

本阶段开发的是：

```text
核心仿真器工程优化
```

不是外围能力。

不开发：

- hit floor search。
- P90 target matching。
- GB / GiB 到 block 转换工具。
- 部署脚本导入 profile 工具。
- dashboard / notebook / report 美化。
- DDR / SSD / Mooncake pooling 的真实实现。

## 2. 总目标

工程优化阶段的目标是让 HitFloor 更接近真实 vLLM / vLLM-Ascend 推理服务，同时不破坏 Step1-Step6 已完成的 replay 能力。

必须保持：

- `batch_aware_hbm_lru` 默认语义不变。
- fixed-routing, multi-instance isolated replay 不变。
- finish-time materialization 默认不变。
- 现有 capacity sweep 外围能力继续只消费 typed results。
- 现有测试基线继续通过。

例外：

- Batch EO-H 是已识别并经审批的 cached_tokens 口径修正。它可以更新 `batch_aware_hbm_lru` 的 golden expected 值，但必须明确记录变更原因，不能静默修改。

新增语义必须通过新类型、新 policy、新 replay/cache mode 或 config guard 承载，不能静默改变已有字段含义。

## 3. vLLM 启发下的代码组织原则

参考 vLLM 的分层方式：

```text
Scheduler
-> KVCacheManager
-> KVCacheCoordinator
-> SingleTypeKVCacheManager
-> BlockPool
```

HitFloor 工程优化后应保持对应边界：

```text
replay/        # replay event loop and instance clock
scheduler/    # scheduling decision and batch shape
cache/        # cache backend, block metadata, materialization, eviction
config/       # RunSpec, profiles, guard
latency/      # latency profile and backend
experiment/   # orchestration, request build, sweep
report/       # outer export only
```

规则：

- `report/` 不能计算 cached_tokens。
- `cli/` 不能承载核心逻辑。
- `experiment/` 可以编排，但不应持有 cache/scheduler 细节。
- `replay/event_loop.py` 不应继续膨胀为所有策略的集合点。
- 需要新增语义时，优先新增 policy/backend/schema，而不是给已有函数加隐式分支。

## 4. 防破坏保护线

进入任何代码修改前，先建立 replay 保护线。

### 4.1 Golden E2E

新增一个小型 golden E2E 测试，固定：

- synthetic trace。
- `batch_aware_hbm_lru`。
- `hbm_capacity_blocks`。
- fitted TTFT 参数。
- expected request metrics。
- expected trace / instance metrics。
- expected event stats。

验收：

```text
默认模式优化前后输出一致。
```

### 4.2 Replay Determinism

新增或强化测试：

- 同一输入重复运行，输出完全一致。
- 多实例 replay 互不影响。
- instance 输出排序稳定。
- capacity sweep 复用 request build 后，不污染下一次 replay。

### 4.3 Config Guard

任何尚不支持但会改变 replay 语义的配置必须失败或 guard：

- speculative enabled 且 drop blocks 未被 conversion module 接管。
- CP / DCP / PCP 与不支持 cache manager 组合。
- runtime block size 无法确定。
- request model 与 RunSpec model 不一致且未命中 alias。
- hybrid cache group 缺少 block size / group 信息。

## 5. Batch EO-A：Replay Baseline 与 Golden Test

### 目标

先锁住 Step1-Step6 的已完成行为，为后续工程优化提供回归保护。

### 代码范围

建议新增：

```text
tests/golden/
  test_batch_aware_hbm_lru_golden.py
```

可复用已有 synthetic builder，不新增真实 trace 文件时，也可在测试中构造最小 records。

### 测试覆盖

- fixed-routing 多实例隔离。
- finite HBM LRU。
- zero-miss fast-finish。
- finish-time materialization。
- cache event stats。
- p90 TTFT 聚合。

### 验收标准

- `pytest tests/golden/test_batch_aware_hbm_lru_golden.py` 通过。
- full pytest 通过。
- golden expected 值写死，不使用宽松近似，避免 replay 语义漂移。

## 6. Batch EO-B：Profile Schema / RunSpec / ConfigGuard

### 目标

把产品形态中的输入分层落成 typed schema，但第一批不改变 replay 默认行为。

### 建议新增文件

```text
src/hitfloor/config/
  run_spec.py
  profiles.py
  guard.py
  validation.py

configs/models/
  glm-v5.1.yaml

configs/hardware/
  ascend-a3-example.yaml

configs/deployments/
  glm-v5.1-vllm-ascend-prefill.yaml

configs/instances/
  local-fixed-route-example.yaml
```

### 核心类型

```text
RunSpec
ModelProfile
HardwareProfile
DeploymentProfile
InstanceProfile
ParallelProfile
SchedulerProfile
CacheFeatureProfile
SpeculativeProfile
ConfigGuardIssue
ConfigGuardResult
```

### 字段原则

`RunSpec`：

- `trace_path`
- `output_dir`
- `mode`
- `model_name`
- `requested_block_size`
- `capacity_candidates`
- `model_profile`
- `hardware_profile`
- `deployment_profile`
- `instance_profile`

`ModelProfile`：

- model name and aliases。
- tokenizer profile。
- chat template profile。
- max model len。
- attention/cache family。
- optional hybrid cache groups。

`DeploymentProfile`：

- engine type: vllm / vllm-ascend。
- `max_num_seqs`。
- `max_num_batched_tokens`。
- `enable_chunked_prefill`。
- `prefill_context_parallel_size`。
- `decode_context_parallel_size`。
- speculative method and drop blocks。
- KV transfer / pooling flags。

`HardwareProfile`：

- device name。
- HBM size。
- optional KV dtype defaults。
- communication settings such as `HCCL_BUFFSIZE`。

### 不做

- 不解析部署脚本。
- 不从 GB 自动推导 block 数。
- 不接外部 simulator。
- 不改变现有 YAML runner。

### 验收标准

- profile YAML 能被加载成 typed dataclass。
- 缺字段给出清晰错误。
- 不支持组合给出 `ConfigGuardResult`。
- 旧实验配置仍能跑通。

## 7. Batch EO-C：Block Size / Cache Block Conversion Module

### 目标

新增纯计算模块，把 cached_tokens 语义集中到一个地方。

### 建议新增文件

```text
src/hitfloor/cache/block_size.py
src/hitfloor/cache/cache_block_conversion.py
tests/unit/cache/test_cache_block_conversion.py
```

### 核心类型

```text
BlockSizeInput
BlockSizeResolution
CacheBlockConversionInput
CacheBlockConversionResult
BlockSizeResolver
CacheBlockConversionPolicy
CachedTokensCalculator
```

### 输出字段

```text
requested_block_size
runtime_block_size
effective_block_size
max_cache_hit_length
matched_blocks
speculative_drop_blocks
cached_blocks
cached_tokens
unsupported_reason
```

### 基础规则

```text
max_cache_hit_length = prompt_tokens - 1
matched_blocks = floor(max_cache_hit_length / effective_block_size)
cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)
cached_tokens = cached_blocks * effective_block_size
```

### 需要覆盖的 case

- prompt tokens 为 0 / 1 / 小于 block size。
- full prompt hit 仍丢最后一个 token 所在 block。
- `runtime_block_size != requested_block_size`。
- PCP / DCP 放大 effective block size。
- MTP / EAGLE / EAGLE3 drop one block。
- hybrid group LCM 对齐。
- unsupported CP + sliding window / mamba / hybrid 组合进入 guard。

### 不做

- 不改 request builder。
- 不改 replay。
- 不改 report。

### 验收标准

- calculator 是纯函数，多次运行 deterministic。
- 计算结果和 `docs/notes/cached_tokens_calculation_logic.md` 一致。
- 所有 unsupported case 不伪造结果。

## 8. Batch EO-D：Profile-Aware Request Build 接入

### 目标

把 typed profile 和 block conversion 接入 request build，但保持默认配置行为兼容。

### 建议修改文件

```text
src/hitfloor/experiment/request_builder.py
src/hitfloor/request/model_resolver.py
src/hitfloor/request/block_hasher.py
src/hitfloor/instance/request.py
```

### 设计

新增 request build 上下文：

```text
RequestBuildContext:
  run_spec
  model_profile
  deployment_profile
  block_size_resolution
  conversion_policy
```

`SimulationRequest` 可新增字段：

```text
requested_block_size
runtime_block_size
effective_block_size
block_conversion_result
```

兼容要求：

- 对旧 config，如果没有 profile，走 legacy context。
- legacy context 中三种 block size 相等。
- 因此默认 Step6 synthetic 输出不变。

### 验收标准

- request model alias resolution 可测。
- legacy config output 不变。
- profile config output 可解释。
- report 不重新计算 conversion，只读取 typed result。

## 9. Batch EO-E：Materialization Policy 接口

### 目标

为未来 progressive block visibility 做接口准备，但不立即改变默认 replay。

### 建议新增文件

```text
src/hitfloor/cache/materialization.py
tests/unit/cache/test_materialization_policy.py
```

### 核心接口

```text
MaterializationPolicy
FinishTimeMaterializationPolicy
ProgressiveChunkMaterializationPolicy
```

第一批只实现：

```text
FinishTimeMaterializationPolicy
```

并让现有 cache backend 通过该 policy 调用 materialize。

### Progressive 设计草案

未来 progressive policy 可以在每个 scheduled chunk finish 时：

- 根据 request 已完成 token 数计算 newly full blocks。
- 只 materialize full blocks。
- 同一 iteration finish 后才对下一轮 lookup 可见。
- partial block 不可见。

### 防破坏要求

- `batch_aware_hbm_lru` 仍绑定 finish-time policy。
- progressive 必须使用新 mode，例如：

```text
batch_aware_hbm_lru_progressive
```

### 验收标准

- 默认 E2E 输出不变。
- finish-time policy 单测覆盖 request finish 后才可见。

## 10. Batch EO-F：ServingLatencyProfile 接口

### 目标

把 TTFT、KV load、queue、TPOT 等未来 latency 组成收敛到一个核心 profile 接口，但第一批仍只调用现有 fitted TTFT。

### 建议新增文件

```text
src/hitfloor/latency/profile.py
tests/unit/latency/test_serving_latency_profile.py
```

### 核心类型

```text
ServingLatencyProfile
TTFTComponent
KVLoadComponent
QueueComponent
TPOTComponent
CalibrationPolicy
```

第一版行为：

```text
ttft = fitted_ttft_backend.estimate(batch_shape)
kv_load_time = 0
queue_time = scheduler_wait_ms only as metric, not added twice
tpot = unsupported / not modeled
```

### 未来接口

- 每 N 条请求重新拟合参数，默认 N=500。
- AIConfigurator / MkSim 提供 TTFT 校准样本。
- Ramulator2 提供 KV load latency 校准样本。
- production logs 可作为拟合来源。

### 验收标准

- 旧 `FittedTTFTLatencyBackend` 仍可直接使用。
- 新 profile wrapper 不改变默认 TTFT。
- 不支持 TPOT 时不输出强结论。

## 11. Batch EO-G：大 Trace 性能与事件安全

### 目标

增强大 trace 运行稳定性，但不改变 replay 语义。

### 代码范围

建议新增或修改：

```text
src/hitfloor/cache/event_sink.py
src/hitfloor/experiment/request_builder.py
src/hitfloor/replay/event_loop.py
scripts/benchmark_replay.py
tests/integration/test_large_trace_smoke.py
```

### 优化项

- event sink 默认 stats-only。
- event dump 必须显式指定 capacity / output。
- cache event writer 保持 streaming。
- request build 支持按 instance 分组后的只读复用。
- replay result 不保存无必要大对象。
- benchmark 输出 request/s、events/s、peak basic counters。

### 暂不做

- true streaming request build。
- 多进程 parallel replay。
- 真实硬件 benchmark。

### 验收标准

- 小规模 smoke test 进入 pytest。
- 大规模 benchmark 只保留脚本，不进默认 pytest。
- benchmark 不模拟真实硬件，只压测 HitFloor state machine。

## 12. Batch EO-H：vLLM cached_tokens accounting 贯穿 replay lookup

### 目标

把 EO-C 已实现的 `CacheBlockConversionPolicy` 从 request build metadata 贯穿到 replay lookup metrics，使最终输出的 `hbm_hit_tokens`、`miss_tokens` 和 `effective_hit_rate` 使用 vLLM-like cached_tokens 口径。

本批次只修正 accounting，不引入新的 materialization 时机，也不引入 decode / TPOT。

### 背景问题

当前链路中：

```text
CacheBlockConversionPolicy
  -> 能计算 max_cache_hit_length / effective_block_size / speculative_drop_blocks / cached_tokens

HBMCache.lookup_prefix()
  -> 只判断 prefix block hash 是否 resident
  -> 按 resident contiguous blocks 的 token_count 汇总 hit/miss
```

因此可能出现：

- partial block 被计入 hit。
- 完整 prompt 二次命中时，最后 token 或最后一个 block 被计入 hit。
- MTP / EAGLE / EAGLE3 的 one-block drop 未体现在 replay hit metrics。
- PCP / DCP / hybrid LCM 虽然有 pure conversion result，但没有成为最终 replay metric 的裁决层。

### 建议新增或修改文件

```text
src/hitfloor/cache/cached_token_accounting.py
src/hitfloor/replay/metrics.py
src/hitfloor/replay/event_loop.py
tests/unit/cache/test_cached_token_accounting.py
tests/unit/replay/test_cached_token_accounting_replay.py
tests/golden/test_batch_aware_hbm_lru_golden.py
```

### 核心设计

新增 accounting 层：

```text
Raw PrefixLookupResult
+ SimulationRequest.block_conversion_result
+ SimulationRequest.prompt_tokens
-> AccountedLookupResult
```

建议类型：

```text
CachedTokenAccountingInput
AccountedLookupResult
CachedTokenAccountant
```

职责边界：

- `HBMCache` 仍只负责 hash-only resident lookup、LRU touch 和 cache event。
- `CacheBlockConversionPolicy` 仍只负责 vLLM-like cached token 上限计算。
- `CachedTokenAccountant` 负责把 raw resident lookup 和 conversion result 合成为最终 replay metrics。
- `report/`、`cli/`、`scripts/` 不得重新计算 cached_tokens。

### Accounting 规则

对每条 request：

```text
raw_hbm_hit_tokens = sum(raw hbm hit block token_count)
vllm_cached_token_cap = request.block_conversion_result.cached_tokens
accounted_hbm_hit_tokens = min(raw_hbm_hit_tokens, vllm_cached_token_cap)
accounted_ddr_hit_tokens = 0  # Step 当前仍无 DDR
accounted_miss_tokens = prompt_tokens - accounted_hbm_hit_tokens - accounted_ddr_hit_tokens
```

实现时必须按 block 边界选择 accounted hit blocks：

```text
accounted_hbm_hit_blocks =
  raw hbm hit blocks whose cumulative token_count <= vllm_cached_token_cap
```

如果存在 partial block 或 MTP drop 导致 resident block 不应计入 usage cached_tokens，该 block 可以仍然是 raw lookup hit，但不能计入 `hbm_hit_tokens` / `effective_hit_rate`。

### miss_tokens 与 materialization blocks 的区别

EO-H 必须明确区分：

```text
miss_tokens
  = 需要计算或重算的 token 数，用于 TTFT accounting。

materialization_blocks
  = finish-time materialization 时需要写入 HBM 的 blocks。
```

这两个量不一定完全等价。例如 MTP drop 的最后一个 resident block 可能不计入 usage cached_tokens，因此会增加 `miss_tokens`，但它已经 resident，不一定需要再次 materialize。

建议：

- 在 `LookupMetrics` 中新增 `materialization_blocks`。
- 保留或迁移现有 `miss_blocks` 调用点，避免 event loop 误用 accounted miss blocks。
- `BatchAwareReplayEngine` materialize 时使用 `materialization_blocks`，TTFT 使用 `miss_tokens`。

### Cache event 语义

EO-H 不要求改变现有 cache event 的物理含义：

- `LOOKUP_HIT` 表示 block metadata resident 且 prefix chain 命中。
- `LOOKUP_MISS` 表示 block metadata 不 resident。
- `MATERIALIZE` 表示 finish-time 写入 HBM metadata。
- `EVICT` 表示 HBM metadata eviction。

最终 report 中的 hit tokens 使用 accounted metrics，而不是简单用 `LOOKUP_HIT` event token_count 求和。若后续需要同时展示 raw cache hit 与 usage cached_tokens，可新增字段，不能复用旧字段改含义。

### 测试覆盖

必须覆盖：

- prompt tokens 小于 block size：cached tokens 为 0。
- prompt tokens 等于 block size：由于 `prompt_tokens - 1`，cached tokens 为 0。
- prompt tokens 大于 block size 且有 partial block：partial block 不计入 hit。
- 完全相同 prompt 二次请求：按 `prompt_tokens - 1` 和 block 向下取整统计。
- MTP / EAGLE / EAGLE3：resident matched blocks 需要 drop one effective block。
- PCP / DCP：effective block size 放大后，hit tokens 按放大后的 block 倍数统计。
- HBM LRU capacity eviction 后，raw resident hit 小于 vLLM cap 时，以 raw resident hit 为准。
- `miss_tokens` 影响 fitted TTFT；`materialization_blocks` 仍只驱动 finish-time materialization。

### 不做

- 不实现 progressive block visibility。
- 不实现 decode / TPOT。
- 不实现 physical KV slots、pinned/refcount。
- 不实现 DDR / SSD / Mooncake pooling。
- 不改变 `capacity_sweep.csv` schema，除非后续单独审批。

用户已接受：

- 默认 finish-time materialization 可能低估长 prefill 期间的 block reuse，这点可在后续新增 `ProgressiveChunkMaterializationPolicy` 和新 replay/cache mode 时处理。
- Decode / TPOT 仍未建模，这点可在后续新增 decode-aware scheduler / replay mode 时处理。

这两点不阻塞 EO-H 收口。

### 验收标准

- `CacheBlockConversionPolicy` 的 `cached_tokens` 能影响 replay 输出的 `hbm_hit_tokens` / `miss_tokens` / `effective_hit_rate`。
- golden test 明确更新，并解释旧值为什么偏松。
- raw cache event stats 与 accounted replay metrics 的口径差异写入测试或 docstring。
- `pytest` 全量通过。
- `ruff check src tests` 通过。
- `ruff format --check src tests` 通过。

## 13. 建议执行顺序

```text
EO-A: Replay golden tests
EO-B: Profile schema / RunSpec / ConfigGuard
EO-C: Block size / cache block conversion pure module
EO-D: Profile-aware request build integration
EO-E: Materialization policy interface
EO-F: ServingLatencyProfile interface
EO-G: Large trace performance and event safety
EO-H: vLLM cached_tokens accounting across replay lookup
```

审批建议：

- EO-A 到 EO-C 可以作为第一轮工程优化开发。
- EO-D 需要在 EO-C review 通过后开发，因为它会接入 request build。
- EO-E / EO-F 是接口性优化，不建议和 EO-D 混在同一批。
- EO-G 可在任意核心语义稳定后做，但需要用 EO-A golden tests 兜底。
- EO-H 应作为工程优化收口前的核心语义修正；它不引入 progressive materialization 或 decode / TPOT。

## 14. 每批代码评审重点

每批 review 必查：

- 是否声明核心仿真器 / 外围能力边界。
- 是否改变 `batch_aware_hbm_lru` 默认输出。
- 是否新增了 schema 而不是在 report 中重算。
- 是否有正常路径、错误路径、unsupported guard 测试。
- 是否有 deterministic tie-break。
- 是否保留多实例隔离。
- 是否避免全量 token ids / KV tensor 存储。
- 是否避免 cache events 大量堆内存。

## 15. 最终收口验证

工程优化阶段完成后，至少运行：

```text
ruff format --check src tests scripts
ruff check src tests scripts
pytest
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
python scripts/benchmark_replay.py --requests 10000 --instances 4
```

验收结论需要写入：

```text
docs/engineering_optimization/
```

随后归档到：

```text
docs/archive/engineering_optimization/
```
