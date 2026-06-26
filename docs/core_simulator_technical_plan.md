# InferTwin 核心仿真器技术路线与代码实现方案

## 1. 文档定位

本文是 InferTwin 核心仿真器的主技术路线文档。

旧 `docs/implementation_plan.md` 已归档到：

```text
docs/archive/implementation_plan.md
```

原因：

- 旧文档以“输出目标 P90 TTFT 对应的 hit floor”为主线。
- hit floor search 是外围能力，不是核心仿真器。
- InferTwin 当前核心定位已经升级为大型推理服务集群离线仿真平台。

## 2. 开发阶段划分

核心仿真器后续开发分为两类阶段：

1. 工程优化阶段。
2. 实际开发阶段。

### 2.1 工程优化阶段

目标：

- 提升仿真器与真实 vLLM 推理框架的一致性。
- 提升大 trace 下的稳定性和性能。
- 明确哪些近似是为了性能，哪些是能力边界。
- 保证各仿真模块之间有序工作。
- 保证实例之间 replay 互不影响。

工程优化阶段可以不引入新产品能力，但必须输出：

- 当前仿真行为。
- 与真实推理服务的差异。
- 误差风险。
- 是否需要新 replay mode / backend / schema。
- benchmark 和 E2E 验证。

工程优化阶段已完成，调研、代码方案、执行记录和收口 review 已归档到：

```text
docs/archive/engineering_optimization/
```

收口结论：

- 当前核心仿真器具备进入 Step7 的条件。
- `batch_aware_hbm_lru` 可作为 fixed-routing、multi-instance isolated、prefill-only、finite HBM LRU baseline。
- vLLM-like cached_tokens usage accounting 已通过 EO-H 贯穿 replay lookup metrics。
- progressive block visibility 是必须补齐的核心能力，但可放到 Step7 之后作为新 replay/cache mode 实现。
- Decode / TPOT 建模保持 pending；只有在存在明确 Decode 建模需求且目标部署是 PD 混部时开启。

Step7 前的 Model Registry & Instance Model Binding 配置治理专项也已完成并归档到：

```text
docs/archive/pre_step7_model_registry/
```

收口结论：

- `ModelRegistry` 已作为 `model_name -> ModelProfile / tokenizer profile / default_latency` 索引。
- `InstanceProfile` 已作为 `instance_uuid -> model/deployment/optional latency_profile` 绑定表。
- `InstanceLatencyBackendResolver` 已支持 instance profile、model default 和 legacy global backend 三层解析。
- `latency_fallback` 已有 calibration failure policy schema，但尚未接入真实 external calibration harness。
- 该专项未改变 replay、cache、scheduler、tokenizer 或 request build 语义。

### 2.2 实际开发阶段

目标：

- 开发新的核心仿真器能力。
- 例如 multi-tier cache、KV load latency、queue simulation、gateway simulation。

实际开发阶段必须先声明：

```text
本阶段开发的是核心仿真器能力。
```

如果开发的是 CSV、summary、dashboard、search、CLI wrapper，则应声明为外围能力。

## 3. 当前核心仿真器代码结构

核心代码路径：

```text
src/infertwin/
  config/                # config loader, RunSpec/Profile schema and validation
  trace/                 # trace schema and CSV reader
  request/               # request parser, tokenizer registry, chat template, block hash
  instance/              # SimulationRequest and early replay utilities
  scheduler/             # vLLM-like scheduling schema and policy
  cache/                 # prefix cache backend, events, eviction policy, block conversion
  latency/               # fitted TTFT backend, serving latency profile, latency schema
  replay/                # batch-aware replay engine and metrics
  streaming/             # request sharding, streaming replay, streaming sweep
  experiment/            # request builder, runner, sweep orchestration
  external/              # external simulator adapter boundaries
  report/                # outer report/export
  cli/                   # package CLI
```

核心模块：

| 模块 | 职责 |
| --- | --- |
| `config/loader.py` | 当前 YAML 加载；未来承接 `RunSpec` / profile loading |
| `config/run_spec.py` / `config/profiles.py` / `config/guard.py` | `RunSpec`、profile schema 与 config guard foundation |
| `experiment/request_builder.py` | 从 config 构造 `SimulationRequest` |
| `replay/event_loop.py` | fixed-routing, multi-instance isolated replay |
| `scheduler/vllm_like.py` | iteration-level request slice 选择 |
| `scheduler/planning.py` | chunked prefill token selection helper |
| `scheduler/queue.py` | waiting queue abstraction |
| `cache/hbm_lru.py` | finite HBM prefix cache |
| `cache/cache_block_conversion.py` | vLLM-like cached_tokens pure conversion |
| `cache/cached_token_accounting.py` | raw lookup 到 usage cached_tokens accounting |
| `cache/eviction.py` | stateful eviction policy |
| `cache/event_sink.py` | event sink and stats |
| `latency/fitted_ttft.py` | token-linear fitted TTFT backend |
| `latency/profile.py` | `ServingLatencyProfile` latency composition interface |
| `latency/instance_resolver.py` | `instance_uuid` 到实例级 latency backend 的解析 |
| `streaming/` | true streaming request shard build、request source、streaming replay、streaming sweep |
| `external/` | AIConfigurator、MkSim、Ramulator2 等 adapter 边界 |
| `experiment/sweep.py` | HBM capacity sweep runner and aggregation |

外围模块：

| 模块 | 职责 |
| --- | --- |
| `report/sweep.py` | 导出 `capacity_sweep.csv` / `summary.md` |
| `report/summary.py` | 单次 replay summary |
| `report/tables.py` | CSV writer |
| `cli/main.py` | 解析 CLI，调用 runner/report |
| `scripts/` | local wrapper |

## 4. Replay 工作流

### 4.1 当前主工作流

当前 Step1-Step6 主工作流：

```text
CSV trace
-> TraceRecord
-> parse request_params
-> tokenizer + chat template
-> prompt token ids
-> BlockSizeResolver / CacheBlockConversionPolicy
-> prefix block hash
-> SimulationRequest
-> BatchAwareReplayEngine
-> per-instance replay
-> cache lookup
-> vLLM-like cached_tokens accounting
-> scheduler iteration
-> latency estimate
-> finish-time materialization
-> metrics
```

当前工作流已经支持 profile-aware request build metadata 和 vLLM-like cached_tokens accounting。legacy YAML 仍可直接指定 `cache.block_size_tokens`，此时它会作为 legacy `requested_block_size/runtime_block_size/effective_block_size` 使用。

### 4.1.1 True Streaming 工作流

大 trace 场景可以显式使用 true streaming path：

```text
CSV trace
-> StreamingRequestShardBuilder
-> per-instance JSONL request shards
-> JsonlRequestSource
-> StreamingBatchAwareReplayEngine
-> InstanceLatencyBackendResolver selects backend per shard.instance_uuid when configured
-> CapacitySweepStreamingMetricAggregator
-> StreamingCapacitySweepRunner
-> CapacitySweepResult
-> report/export
```

CLI：

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <config.yaml>
```

config mode：

```yaml
simulation:
  mode: capacity_sweep_streaming
```

边界：

- `capacity_sweep_streaming` 是显式 opt-in。
- 旧 `capacity_sweep` 仍保留内存 list path，适合小 trace、debug 和回归等价测试。
- streaming path 不构造全量 accepted `SimulationRequest` list。
- streaming path 不构造 per-instance pending request list。
- request finish 后释放 active state。
- 如果配置 `instance_latency.profile_path`，每个 instance shard 使用自己的 fitted TTFT backend。
- 如果同时配置 `model_registry.profile_path`，缺少实例专属 `latency_profile` 的 instance 可以使用该 model 的 `default_latency`。
- latency backend 解析优先级是 instance profile -> model default -> legacy global backend。
- `latency_source_by_instance` 输出到 `CapacitySweepResult.config_details` 和 summary，用于解释每个实例的 TTFT 参数来源。
- 如果未配置 `instance_latency`，保持全局 latency backend。
- `latency_fallback` 只声明 future calibration failure policy，不捕获 request build、tokenizer、scheduler、cache 或 replay 错误。
- report/export 仍只消费 `CapacitySweepResult`，不参与 replay 计算。

### 4.2 目标主工作流

根据产品形态，后续目标工作流应升级为：

```text
RunSpec
-> load ModelProfile / HardwareProfile / DeploymentProfile / InstanceProfile
-> validate profiles
-> ConfigGuard check
-> CSV trace
-> TraceRecord
-> parse request_params
-> resolve request_params.model against RunSpec.model_name and ModelProfile.aliases
-> tokenizer + chat template
-> prompt token ids
-> BlockSizeResolver
-> CacheBlockConversionPolicy
-> prefix block hash with effective block semantics
-> SimulationRequest
-> BatchAwareReplayEngine
-> per-instance replay
-> cache lookup
-> account raw resident lookup into usage cached_tokens
-> scheduler iteration
-> latency estimate
-> materialization (current default: finish-time materialization)
-> metrics
```

其中：

- `RunSpec` 是一次实验入口。
- profile validation 必须先于 request build。
- `ConfigGuard` 可以阻止不支持的部署组合进入 replay。
- `BlockSizeResolver` 输出 `runtime_block_size`。
- `CacheBlockConversionPolicy` 输出 `effective_block_size`、`matched_blocks`、`cached_blocks` 和 `cached_tokens`。
- `account_prefix_lookup()` 将 raw cache resident blocks 转成 replay-facing usage cached_tokens。
- report/export 只能展示这些 typed result，不参与计算。

实例内状态机：

```text
pending -> waiting -> running -> finished
```

每个 instance 独立：

- independent `WaitingQueue`。
- independent `HBMCache`。
- independent iteration clock。
- independent request states。

全局输出最后按时间 / instance / request deterministic sort。

## 5. 当前仿真内容

当前核心仿真器已经模拟：

- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching。
- chunked prefill。
- first-schedule-time prefix cache lookup。
- bounded waiting lookup frontier。
- zero-miss fast-finish。
- finish-time materialization。
- HBM LRU block lifecycle。
- lookup hit / lookup miss / materialize / evict events。
- vLLM-like cached_tokens accounting。
- fitted TTFT prefill latency。
- instance latency profiles for true streaming replay。
- HBM capacity sweep。
- true streaming request shard build。
- per-instance streaming replay。
- streaming capacity sweep。
- streaming benchmark for throughput / memory observation。

## 6. 当前不建模内容

当前不建模：

- 真实模型推理。
- 真实物理 KV tensor。
- physical KV slots。
- pinned/refcount。
- progressive block visibility。
- DDR / SSD / remote cache。
- KV load latency。
- TPOT。
- decode batch。
- gateway routing。
- 真实机器侧排队。
- 完整 heterogeneous instance cluster simulation。第一版实例级 fitted TTFT backend 已完成，但 per-instance scheduler/cache/deployment replay 仍未完成。
- cross-instance KV pooling。

已实现但仍有限制：

- runtime block size override、CP / DCP / PCP、MTP / EAGLE / EAGLE3、hybrid cache group LCM 对 cached_tokens 的影响，已进入 block conversion 与 accounting foundation；但仍不是 physical KV block manager 的逐行为仿真。

这些内容不是外围能力，而是未来核心仿真器能力。

## 7. 与真实 vLLM 推理服务的核心差异

当前主要差异有两类。

### 7.1 没有真实推理

InferTwin 不部署真实模型，不执行真实 attention / MLP / decode kernel。

当前使用：

```text
FittedTTFTLatencyBackend
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

风险：

- 无法表达不同 batch shape 的非线性。
- 无法表达 cached prefix 对 attention shape 的影响。
- 无法表达 decode 对 prefill 的干扰。
- 无法表达硬件占用、kernel overlap、host/device transfer 等细节。

当前缓解：

- `BatchShape` 和 `ShapeKey` 已存在。
- `ShapeMemo` 已存在。
- 后续可以用外部 TTFT simulator / production logs 拟合更复杂 profile。

### 7.2 没有真实物理存储

InferTwin cache 当前只保存：

```text
block_key + metadata
```

不保存：

- token ids。
- KV tensor。
- physical block table。
- refcount。
- pinned state。
- device memory fragmentation。

风险：

- 无法评估真实 memory pressure。
- 无法表达 physical block allocation failure。
- 无法表达 shared block refcount / copy-on-write。

当前缓解：

- 用 `hbm_capacity_blocks` 控制逻辑容量。
- 用 event stats 检查 lookup/materialize/evict。
- 后续新增 physical slot / refcount mode，而不是改变当前 HBM-only mode。

## 8. 工程优化重点

### 8.1 Profile Schema / RunSpec / ConfigGuard

产品形态已经确认 InferTwin 目标输入分为：

```text
RunSpec
ModelProfile
HardwareProfile
DeploymentProfile
InstanceProfile
```

技术路线必须先落地 schema 和 validator，再开发依赖 profile 的核心能力。

建议新增核心类型：

```text
RunSpec
ModelProfile
HardwareProfile
DeploymentProfile
InstanceProfile
ConfigGuard
ConfigGuardResult
```

建议目录：

```text
src/infertwin/config/
  run_spec.py
  profiles.py
  guard.py
  loader.py
```

职责：

- 加载 `RunSpec`。
- 加载并解析 profile 引用。
- 校验 `request_params.model`、`RunSpec.model_name` 和 `ModelProfile.aliases`。
- 校验 deployment profile 中的并行策略、speculative decoding、CP、runtime block size override 等字段。
- 为核心 replay 提供 typed config，而不是裸 dict。
- 对不支持组合生成 `ConfigGuardResult`。

`ConfigGuardResult` 建议结构：

```text
ConfigGuardResult:
  code
  severity
  blocked
  affected_profile
  affected_field
  reason
  suggestion
```

`severity`：

- `error`：阻止 replay。
- `warning`：允许 replay，但需要在 report 中显式展示风险。
- `info`：记录配置推断信息。

第一版必须直接阻止：

- 单模型 replay 中 `request_params.model` 与 `RunSpec.model_name` 不一致且未命中 alias。
- `speculative.enabled = true` 且 `speculative_drop_blocks > 0`，但 block conversion module 尚未启用。
- PCP / DCP 与 unsupported cache manager 组合。
- profile 缺少核心字段，导致 block conversion 或 tokenizer 选择无法确定。

边界：

- schema / validator 是核心仿真器能力。
- deployment script import 是外围能力，它只能生成 profile config，不能绕过 validator。
- GB / GiB 到 block 转换是外围能力，它只能消费 profile 并输出 `hbm_capacity_blocks`。

### 8.2 Progressive Block Visibility

当前 InferTwin：

```text
request prefill finish 后，miss blocks 才 materialize。
```

这叫 finish-time materialization。

真实 vLLM 的 prefix caching 更细：

- vLLM 文档说明 prefix cache 使用 full block。
- scheduler 会调用 `get_computed_blocks()` 查已计算 blocks。
- `allocate_slots()` 中满 block 可以进入 cache block map。
- running request 追加 token 后，如果 block full，会加入 cache。

参考：

- vLLM docs: <https://docs.vllm.ai/en/latest/design/prefix_caching/>
- vLLM source: <https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/kv_cache_manager.py>
- vLLM source: <https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/block_pool.py>

初步判断：

```text
vLLM 只缓存 full blocks；
full block 可能在 request 运行过程中进入 prefix cache；
不一定需要等整个 request prefill 完成。
```

为什么重要：

- 现网中 P80 KV block reuse 间隔可能在 1 min 左右。
- 128K 长请求 prefill 可达 30s 左右，更长请求会更久。
- 如果 InferTwin 等整个 TTFT 完成才让 blocks 可见，长请求场景可能低估 KV hit。

可行性评估：

- 可以新增 chunk/block-level materialization mode。
- 在每个 scheduled chunk finish 后，将 newly completed full blocks materialize。
- partial block 仍不能 cache。
- 需要重新定义 event timing 和 lookup frontier。
- 不应改变 `batch_aware_hbm_lru` 的 frozen finish-time 语义，应新增 replay/cache mode。

评审结论：

```text
这是必须修改的地方，但可以放到 Step7 之后进行。
```

实现要求：

- 不直接修改现有 `batch_aware_hbm_lru` 的 finish-time materialization 语义。
- 新增 `ProgressiveChunkMaterializationPolicy` 或等价 policy。
- 新增 replay/cache mode，例如 `batch_aware_hbm_lru_progressive`。
- 明确 raw cache events、usage cached_tokens、materialization timing 三者口径。

### 8.3 Decode / TPOT 建模

当前 InferTwin 只做 prefill TTFT。

真实服务中：

- decode batch 会占用 iteration。
- TPOT 会影响用户体验。
- PD 混部模型中 decode 和 prefill 会互相干扰。
- decode KV cache 会持续增长。

风险：

- 不考虑 decode 会高估 prefill 可用资源。
- 不考虑 TPOT 会低估混部服务尾延迟。
- 不考虑 decode cache growth 会低估 cache pressure。

评审结论：

```text
pending
```

当前不立即实现 Decode / TPOT，原因：

- PD 分离会逐步成为主流。
- prefix cache 主要发生在输入 token。
- 从现网数据看，输出 token 数通常远小于输入 token；agent 场景中甚至可接近 100:1。

开启条件：

- 有明确的 Decode 建模需求。
- 目标模型部署形态明确是 PD 混部。

满足条件后，再新增 decode-aware scheduler / replay mode，并要求输入 trace 增加每条请求的输出 token 个数。

### 8.4 Latency Profile 管理

当前 fitted TTFT 公式过于简单。

EO-F 已新增核心类：

```text
ServingLatencyProfile
```

职责：

- 管理 TTFT 计算。
- 管理 KV load time。
- 管理 queue time。
- 管理 TPOT。
- 管理 decode token count。
- 管理部署形态和启动参数。
- 管理外部 simulator 校准结果。

当前落地状态：

- `ServingLatencyProfile` 已实现 `BatchLatencyBackend`，可被 replay 直接调用。
- 当前 duration 组合口径是 `queue_ms + ttft_ms + kv_load_ms`。
- 默认 `ttft_ms` 来自 `FittedTTFTLatencyBackend`。
- 默认 `queue_ms = 0`，`kv_load_ms = 0`，并在 `LatencyResult.details` 中显式标记未建模。
- `TPOT` / `decode` 当前只记录为 `not_modeled_in_current_replay`，不进入 prefill iteration duration。
- `latency.backend: fitted_ttft` 保持旧语义；`latency.backend: serving_latency_profile` 是显式新入口。
- true streaming runner 已支持 `instance_latency.profile_path`，可按 `instance_uuid` 选择 fitted TTFT backend。
- `ModelRegistry` 已提供 `model_name -> ModelProfile / tokenizer profile / default_latency` 索引。
- `InstanceLatencyBackendResolver` 已支持 instance 专属 profile、model default latency 和 legacy global backend 三层解析。
- `latency_fallback.on_calibration_failure` 已有 schema，默认 `fail`，显式 `use_model_default` 只为未来 external calibration harness 失败预留。
- `InstanceLatencyProfile.kv_load` 已保留 DDR / remote KV-load 超参数，当前默认 0 且不参与 TTFT。

未来目标数据组成：

```text
ServingLatencyProfile:
  model_profile
  hardware_profile
  deployment_profile
  instance_profile
  ttft_model
  kv_load_model
  queue_model
  tpot_model
  calibration_policy
```

TTFT 未来建议：

```text
request miss tokens
-> split into chunks
-> estimate each chunk duration
-> compose request TTFT
```

KV load time 未来建议：

```text
kv_load_time ~= f(cache_tier, hit_tokens, kv_bytes, hardware)
```

动态拟合建议：

```text
每 N 条请求重新校准 profile，默认 N=500，可配置。
```

外部来源：

- AIConfigurator / MkSim 用于 TTFT / prefill 计算标定。
- 开源 `aiconfigurator_git` 只用于测试、学习和校准实验；公司内 `AIConfigurator` 才是未来生产 adapter 名称。
- Ramulator2 用于 DDR / memory access latency 标定。
- production logs 用于拟合和校验。

接入说明索引：

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/cached_tokens_calculation_logic.md
```

设计原则：

- `ServingLatencyProfile` 是核心仿真器能力。
- `ServingLatencyProfile` 应引用 profile typed config，不重复解析裸启动参数。
- 外部 simulator adapter 是依赖，不应污染 replay 数据结构。
- report 只展示 profile 信息，不参与计算。

### 8.5 大 Trace 性能

true streaming 专项已完成第一版大 trace path：

- CSV 逐行 request build。
- per-instance JSONL shard。
- tokenizer-stage long request rejection sidecar。
- `RequestSource` abstraction。
- per-instance streaming replay。
- streaming metric aggregation。
- streaming capacity sweep runner。
- selected capacity raw cache event dump。
- streaming benchmark harness。

当前入口：

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <config.yaml>
```

benchmark：

```bash
.venv/bin/python scripts/benchmark_streaming_replay.py \
  --requests 10000 \
  --instances 4 \
  --prompt-words 256 \
  --reuse-period 64 \
  --capacities 128,512 \
  --output-dir reports/streaming_benchmark \
  --output-json reports/streaming_benchmark/benchmark.json
```

仍有限制：

- trace 必须按 `(service_start_time, instance_uuid, request_id)` 排序；external sort 仍是后续任务。
- JSONL shard 仍会保存 prefix block hash chain；长 prompt 下磁盘体积会变大。
- exact percentile 仍保存 TTFT list；百万级 request 需要显式 quantile policy。
- 多实例 replay 当前串行。

约束保持不变：

- 不牺牲 deterministic output。
- 不牺牲实例隔离。
- 不让 report/export 参与 replay。

### 8.6 Block Size / Cache Block Conversion

工程优化阶段已完成第一版 Block Size / Cache Block Conversion 和 cached_tokens accounting。

真实 vLLM / vLLM-Ascend 中，cache hit 统计使用的 block 语义可能被部署形态改变：

- 即使 prompt 全命中，也会设置 `max_cache_hit_length = prompt_tokens - 1`。
- partial-block prefix hit 不计入 `cached_tokens`。
- cache hit 计算应使用 `runtime_block_size`，而不只是 CLI / RunSpec 中的 `requested_block_size`。
- PCP / DCP 会放大 unitary full-attention lookup 的 effective block size。
- MTP / EAGLE / EAGLE3 会丢弃最后一个 matched block。
- hybrid Mamba / multi cache group 场景可能需要按各 group block size 的最小公倍数对齐。

已新增核心模块：

```text
BlockSizeResolver
CacheBlockConversionPolicy
account_prefix_lookup / AccountedLookupResult
```

职责：

- 从 `RunSpec`、`DeploymentProfile` 和 runtime logs 推导 `runtime_block_size`。
- 根据 PCP / DCP 推导 `effective_block_size`。
- 根据 `DeploymentProfile.features.speculative` 处理 `speculative_drop_blocks`。
- 根据 hybrid cache groups 做 LCM 对齐。
- 对 unsupported manager + CP 组合给出 `config_guard`。
- 输出 `matched_blocks`、`cached_blocks`、`cached_tokens`。

基础公式：

```text
max_cache_hit_length = prompt_tokens - 1
matched_blocks = floor(max_cache_hit_length / effective_block_size)
cached_tokens = matched_blocks * effective_block_size
```

MTP / EAGLE / EAGLE3：

```text
speculative_drop_blocks =
  deployment_profile.features.speculative.speculative_drop_blocks

default speculative_drop_blocks = 1 for mtp / eagle / eagle3
cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)
cached_tokens = cached_blocks * effective_block_size
```

该模块会改变 KV cache hit 统计结果，因此属于核心仿真器能力。GB / GiB 到 block 数转换只是生成容量输入，属于外围能力。

参考笔记：

```text
docs/notes/cached_tokens_calculation_logic.md
```

## 9. 实际开发阶段路线

多级能力暂不展开完整技术方案，但核心开发顺序已确认：

已完成前置地基：

- Profile schema / RunSpec / ConfigGuard foundation。
- Block size / cache block conversion module。
- vLLM-like cached_tokens accounting。
- ServingLatencyProfile interface。

后续核心开发建议顺序：

1. 多级 cache backend。
2. KV load latency。
3. instance queue simulation。
4. gateway simulation。
5. 实例集群仿真。
6. cache 管理。
7. progressive block visibility。
8. decode-aware replay / TPOT，仅在明确 PD 混部需求时开启。

外围能力不进入核心开发顺序，但需要在对应 schema 稳定后实现：

- `KV Capacity Planner / GB to Block Converter`：依赖 profile schema，输出 `hbm_capacity_blocks`。
- `Deployment Script to Profile Config`：依赖 profile schema 和 validator，生成 profile YAML。
- P90 target matching / hit floor search：消费核心 typed metrics。

每个阶段必须先写产品形态，再写技术路线，再进入代码开发。

## 10. 开发状态

### 10.1 已完成

Step1-Step6 与工程优化阶段已完成：

- trace reader。
- request parser。
- tokenizer / chat template registry。
- GLM-5 profile。
- hash-only prefix block。
- fixed-routing multi-instance isolated replay。
- vLLM-like batch-aware replay。
- chunked prefill。
- fitted TTFT backend。
- infinite HBM prefix cache。
- finite HBM LRU。
- stateful eviction policy。
- event sinks。
- HBM capacity sweep。
- `capacity_sweep.csv` / `summary.md` 外围导出。
- `RunSpec` / profile schema / `ConfigGuard` foundation。
- profile-aware request build。
- tokenizer-stage long request rejection。
- block size / cache block conversion module。
- vLLM-like cached_tokens accounting across replay lookup。
- `MaterializationPolicy` interface。
- `ServingLatencyProfile` interface。
- 大 trace event safety。
- true streaming request shard build。
- per-instance streaming replay。
- streaming capacity sweep runner。
- streaming benchmark harness。
- instance latency profile schema / resolver。
- true streaming runner per-instance fitted TTFT backend selection。

### 10.2 最近验收

工程优化收口：

```text
docs/archive/engineering_optimization/08_core_simulator_closeout_review.md
docs/archive/engineering_optimization/09_eo_h_execution.md
```

True streaming 专项收口：

```text
docs/archive/true_streaming/
docs/reviews/true_streaming_core_simulator_review.md
```

验证基线：

```text
PYTHONPATH=src .venv/bin/python -m pytest: 199 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
git diff --check: passed
```

Step6 功能验收：

```text
docs/archive/step6/03_acceptance_e2e.md
```

验收结果：

| hbm_capacity_blocks | kv_hit_rate | p90_ttft_ms |
| ---: | ---: | ---: |
| 3 | 0.000000 | 15.0 |
| 4 | 0.876667 | 11.0 |
| 8 | 0.913333 | 0.0 |

### 10.3 遗留问题

- 旧 `capacity_sweep` path 仍是一次性构造 accepted request list；大 trace 应使用 `capacity_sweep_streaming`。
- streaming path 第一版要求 trace sorted；external sort / unsorted spooling 未实现。
- 多实例 replay 当前串行。
- exact percentile 仍保存 TTFT list；百万级 request 需要显式 quantile policy。
- JSONL shard 可能较大；binary / compressed shard codec 未实现。
- event 明细大文件已通过 selected capacity dump 控制，但 event sampling 未实现。
- multi-tier cache 未实现。
- KV load latency 未实现。
- gateway / queue simulation 未实现。
- 完整 heterogeneous instance cluster 未实现；当前只完成 true streaming per-instance fitted TTFT backend selection。
- progressive block visibility 必须在 Step7 后作为独立 replay/cache mode 补齐。
- Decode / TPOT 建模 pending；仅在明确 Decode 建模需求且部署为 PD 混部时开启。

## 11. Step7 进入条件

工程优化阶段和 true streaming 专项均已完成，当前具备进入 Step7 的条件。

已满足：

- 明确当前仿真器与真实 vLLM 的差异。
- 落地 profile schema / RunSpec / ConfigGuard foundation。
- 明确 progressive block visibility 必须修改，但可在 Step7 后作为独立核心能力实现。
- 落地 block size / cache block conversion module 和 replay-facing cached_tokens accounting。
- 落地 `ServingLatencyProfile`。
- 梳理性能瓶颈。
- 确认实例隔离和 replay deterministic。
- 输出并执行工程优化代码方案。
- 完成 true streaming request shard build / replay / sweep / benchmark。

进入 Step7 时仍必须先声明：

```text
本阶段是在开发核心仿真器能力，还是外围能力。
```
