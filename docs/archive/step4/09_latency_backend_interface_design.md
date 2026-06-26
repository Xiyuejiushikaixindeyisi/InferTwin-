# Step4 Latency Backend Interface Redesign

## 背景

Batch C 原计划直接使用：

```text
BatchShape -> LatencyBackend.estimate_iteration() -> duration_ms
```

在阅读 AIConfigurator 和 Markov-Infer-Sim 手册后，需要调整接口设计。

核心发现：

- 两个 simulator 的 `batch_size` 都是请求数 / in-flight sequences。
- 两个 simulator 都不是 token-level cache simulator。
- 两个 simulator 都不建模 DDR KV load。
- 两个 simulator 都更偏向 uniform workload shape，而 HitFloor scheduler 可能产生 heterogeneous batch。
- AIConfigurator 可以通过 `ctx_tokens` 建模 IFB/chunked prefill。
- MkSim 核心不内建 chunk loop，需要外层拆 chunk。

因此不能把 HitFloor 当前 `BatchShape` 直接当作外部 simulator input。

## 设计目标

新的接口应满足：

1. replay core 不依赖 AIConfigurator / MkSim。
2. scheduler output 保留 per-request-slice 信息。
3. latency backend input 显式表达 simulator 需要的 workload shape。
4. heterogeneous batch 转换策略必须显式配置，不能隐式猜。
5. Formula backend 继续可用于 Batch C 开发和测试；Batch D 默认切换为拟合型 TTFT 函数 backend。
6. 外部 adapter 延后，但接口不应阻碍后续接入。

## Batch D 默认 backend：FittedTTFTLatencyBackend

Batch D 默认不接入大型 TTFT 仿真器，而是使用拟合型 TTFT 函数 backend。

推荐名称：

```text
FittedTTFTLatencyBackend
```

配置名：

```text
fitted_ttft
```

第一版函数固定为：

```text
token_linear_v1
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

其中：

- `scheduled_prefill_tokens` 来自 `BatchShape.scheduled_prefill_tokens`，即本 scheduler iteration 内全部 uncached prefill token 总数。
- `ms_per_uncached_token` 是由硬件、模型、并行策略、推理框架和 batch/chunk 配置共同决定的超参数。
- `intercept_ms` 表示固定启动 / 框架开销，可先设为 0。

该 backend 不建模：

- queue time。
- HBM / DDR KV load time。
- decode TPOT 对 prefill 的干扰。

HitFloor replay 仍负责：

- scheduler wait。
- chunked prefill。
- cache lookup timing。
- finish-time materialization。

如果后续需要 batch size、context length 或 piecewise 关系，不应修改 `token_linear_v1` 含义，应新增函数版本或新 backend。

## 关键术语

### SchedulerBatchShape

当前 `BatchShape` 更准确地说是 scheduler output：

```text
instance_uuid
iteration_id
start_time_ms
request_slices[]
scheduled_decode_tokens
```

每个 `request_slices[]` 包含 request-level token slice。

### SimulatorPrefillInput

新增建议：用于 latency backend 的 uniform prefill workload input。

字段建议：

```text
model_name
hardware_name
backend_name
batch_size
isl
prefix_tokens
scheduled_prefill_tokens
ctx_tokens
osl
request_ids
source_shape_key
conversion_strategy
```

含义：

- `batch_size`: 请求数。
- `isl`: simulator 看到的 input sequence length。
- `prefix_tokens`: 每请求已缓存 / 已完成、无需本轮 compute 的 context tokens。
- `scheduled_prefill_tokens`: 每请求本轮需要计算的 prefill tokens。
- `ctx_tokens`: context token budget。AIConfigurator 可用，MkSim adapter 可忽略或用于 chunk loop。
- `osl`: output sequence length。TTFT-only 也要显式配置。
- `request_ids`: 参与该 simulator input 的 request ids。
- `conversion_strategy`: heterogeneous batch 转换策略。

约束：

```text
isl = prefix_tokens + scheduled_prefill_tokens
scheduled_prefill_tokens > 0
batch_size > 0
```

### LatencyEstimateInput

如果一个 scheduler iteration 需要转换成多个 simulator inputs，则 latency backend 应接收：

```text
LatencyEstimateInput:
  shape: BatchShape
  simulator_inputs: tuple[SimulatorPrefillInput, ...]
```

但 Batch C 第一版为了简单，可以先让 `FormulaLatencyBackend` 继续直接吃 `BatchShape`，同时新增 adapter conversion 层文档和类型留到 interface refactor。

建议更稳的做法是：

```text
BatchShape
-> BatchShapeConverter
-> tuple[SimulatorPrefillInput, ...]
-> SimulatorLatencyBackend
```

## batch size 统一口径

两个 simulator 均确认：

```text
batch_size = 请求数
```

HitFloor 内部继续保留：

```text
BatchShape.batch_size = len(request_slices)
```

但要注意：

- `max_num_batched_tokens` 是 token budget，不是 batch size。
- `ctx_tokens` 是 context token budget，不是 batch size。
- `scheduled_prefill_tokens` 是 token 数，不是 batch size。

## chunked prefill 统一口径

HitFloor Step4 的主控逻辑仍然是：

```text
HitFloor scheduler controls chunked prefill
```

原因：

- Batch C 要做 finish-time materialization。
- prefix cache 可见性必须由 HitFloor replay 控制。
- AIConfigurator 内部 IFB/chunking 和 HitFloor scheduler 同时开启会双重建模。

因此：

- HitFloor 的每个 scheduler iteration 是 latency estimation 的时间推进单位。
- AIConfigurator adapter 第一版应避免再让内部 IFB 重新切同一段 prefill。
- MkSim adapter 可把每个 chunk 映射成一次 prefill workload。

## context 拆分问题

重审前 `ScheduledSlice` 有：

```text
computed_tokens_before
cached_tokens
scheduled_prefill_tokens
```

但 simulator interface 需要区分：

```text
cached_prefix_tokens
previous_chunk_tokens
scheduled_prefill_tokens
```

原因：

- `cached_prefix_tokens`: 来自 prefix cache hit。
- `previous_chunk_tokens`: 同一请求前面 iteration 已经算完。
- 两者在本轮都无需重复 compute。
- 但 HitFloor 指标和 cache 解释需要区分。

建议在 Batch C 前修改 `ScheduledSlice`，新增：

```text
cached_prefix_tokens
previous_chunk_tokens
```

并保留：

```text
computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens
```

这样外部 adapter 可以选择：

- 把二者都作为 `prefix_tokens` 传给 simulator。
- 报告中仍能解释 cache hit 与 chunk carry-over。

当前 Batch A/B 修正状态：

- 已将 `ScheduledSlice.cached_tokens` 替换为 `cached_prefix_tokens`。
- 已新增 `previous_chunk_tokens`。
- 已在 `ScheduledSlice` 中校验：

```text
computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens
```

- 已在 `VllmLikeBatchScheduler._slice_for()` 中填充：

```text
cached_prefix_tokens = request.cached_tokens
previous_chunk_tokens = computed_before - request.cached_tokens
```

- 已新增 scheduler 单测覆盖 cached prefix 与 previous chunk 的拆分。

## heterogeneous batch 转换策略

HitFloor scheduler 可能产生 heterogeneous batch。两个 simulator 都更偏向 uniform shape。

需要显式配置：

```text
heterogeneous_strategy
```

可选值建议：

### 1. `strict_uniform`

要求所有 request slice 满足：

```text
scheduled_prefill_tokens 相同
computed_tokens_before 相同
cached_prefix_tokens 相同
```

否则失败。

优点：

- 语义最干净。
- 不引入估算偏差。

缺点：

- 现网 trace 中可能失败频繁。

### 2. `group_by_shape`

按 shape 分组：

```text
(scheduled_prefill_tokens, computed_tokens_before, cached_prefix_tokens)
```

每组生成一个 `SimulatorPrefillInput`。

同一 scheduler iteration 的多个 group duration 合并策略必须显式配置：

- `max`: 假设组间并行，取最长。
- `sum`: 假设组间串行，求和。

第一版不建议默认使用，因为合并语义可能影响 TTFT 口径。

### 3. `max_shape_padding`

用 batch 内最大值构造一个 uniform shape。

优点：

- 一个 iteration 一个 simulator call。

缺点：

- 可能高估。
- prefix/miss 关系容易失真。

### 4. `formula_per_slice`

Formula backend 对 per-slice 直接聚合，不要求 uniform shape。

优点：

- 适合 Batch C 开发和单测。
- 不阻塞 replay engine。

缺点：

- 不能代表 AIConfigurator/MkSim 最终精度。

## Batch C 推荐策略

Batch C 继续使用 Formula backend，但需要先修改内部 schema，使其不阻碍后续外部 adapter：

1. `ScheduledSlice` 增加：
   - `cached_prefix_tokens`
   - `previous_chunk_tokens`
2. `BatchShape` 保持 scheduler output，不改名以减少代码 churn，但文档中明确它不是 simulator input。
3. 新增或预留 `conversion_strategy` 配置，但 Batch C 不实现外部 simulator conversion。
4. Formula backend 使用 per-slice 聚合，不要求 uniform shape。
5. Batch C 的 `IterationMetrics` 记录：
   - `batch_size`
   - `scheduled_prefill_tokens`
   - `max_query_len`
   - `total_context_tokens`
   - 后续可加 `heterogeneous=True/False`

这样 Batch C 可以继续开发，同时不会错误绑定 AIConfigurator/MkSim。

## AIConfigurator Adapter 设计

Adapter 名称建议：

```text
AIConfiguratorLatencyBackend
```

接口职责：

```text
SimulatorPrefillInput -> duration_ms
```

配置建议：

```yaml
latency:
  backend: aiconfigurator
  model_path: zai-org/GLM-5
  system_name: ascend_910c
  backend_name: sglang
  backend_version: ascend_v1
  database_mode: SILICON
  mode: agg
  tp_size: 1
  pp_size: 1
  attention_dp_size: 1
  osl_for_ttft_backend: 1
  heterogeneous_strategy: strict_uniform
```

转换：

```text
batch_size = simulator_input.batch_size
isl = simulator_input.isl
prefix = simulator_input.prefix_tokens
ctx_tokens = simulator_input.scheduled_prefill_tokens * batch_size
osl = osl_for_ttft_backend
```

注意：

- 如果用 `ctx_tokens`，必须避免 AIConfigurator 内部再次拆分已由 HitFloor 拆好的 chunk。
- 若 `cli_estimate` 无法直接传 `prefix`，应使用 lower-level SDK `RuntimeConfig`。
- 输出优先读 `ttft` 或 `context_latency`。

## MkSim Adapter 设计

Adapter 名称建议：

```text
MkSimLatencyBackend
```

接口职责：

```text
SimulatorPrefillInput -> duration_ms
```

配置建议：

```yaml
latency:
  backend: mksim
  mksim_root: /path/to/Markov-Infer-Sim
  task_template: configs/task/pd_fusion.yaml
  model_path: configs/models/glm_5.json
  deploy_strategy: pd_fusion
  deploy_config_path: configs/deployment/pd_fusion.yaml
  chip_path:
    - configs/chips/ascend_910c.yaml
  op_config: configs/op_config/op_config.yaml
  osl_for_ttft_backend: 1
  heterogeneous_strategy: strict_uniform
```

转换：

```text
batch_size = simulator_input.batch_size
seq_len = simulator_input.isl
prefix_cache = simulator_input.prefix_tokens
out_len = osl_for_ttft_backend
```

输出：

- 读取 `*_plan_metrics.csv` 中的 `ttft_ms`。
- 或使用 internal `Task(...).execute()` 读取 `result.metrics.ttft_ms`。

注意：

- MkSim 核心不内建 chunk loop。HitFloor 每个 scheduler iteration 对应一次 prefill shape。
- MkSim 单次调用不支持多 heterogeneous shape。
- MkSim 不建模 DDR KV load。

## Batch C Formula Backend 设计调整

Formula backend 在 Batch C 中继续作为默认 backend。Batch D 默认 backend 已调整为 `FittedTTFTLatencyBackend` / `fitted_ttft`。

建议 formula 输入仍为 `BatchShape`，但计算时使用：

```text
duration_ms =
    fixed_overhead_ms
  + prefill_token_ms * total_scheduled_prefill_tokens
  + batch_overhead_ms * batch_size
  + context_token_ms * total_context_tokens
```

这与现有实现一致。

但 shape key 后续可以加入：

```text
heterogeneous_signature
```

避免两个 total 相同但 per-slice 分布不同的 batch 被错误 memoize。

Batch C 是否立刻修改 shape key：

- 如果只用 formula backend，当前 exact fields 可能足够开发。
- 若要为外部 adapter 预留，建议在 Batch C 前补充 `slice_signature`。

## 对 Batch C 方案的修正

Batch C 整体路线不变，但需要修改数据结构和接口：

### 必改

1. `ScheduledSlice` 增加：
   - `cached_prefix_tokens`
   - `previous_chunk_tokens`
2. `VllmLikeBatchScheduler._slice_for()` 填充上述字段：
   - `cached_prefix_tokens = request.cached_tokens`
   - `previous_chunk_tokens = computed_before - request.cached_tokens`
3. `BatchAwareReplayEngine` 仍使用 `BatchShape`，不直接生成 simulator-specific input。
4. 文档中明确：`BatchShape` 是 scheduler output，不是 AIConfigurator/MkSim input。

### 可选

1. `ShapeKey` 增加 per-slice signature。
2. 新增 `SimulatorPrefillInput` 类型，但 Batch C 暂不使用。
3. 新增 converter 接口，但不实现 external adapter。

## 建议审批项

进入 Batch C 开发前，需要确认：

1. 已执行，待代码 review：先修改 Batch A/B 的 `ScheduledSlice`，加入 `cached_prefix_tokens` 和 `previous_chunk_tokens`。
2. 是否同意 Batch C 仍以 Formula backend 为唯一实际 backend。
3. 是否同意 Batch C 不实现 `SimulatorPrefillInput`，只在文档中冻结外部 adapter 设计。
4. 是否同意 `BatchShape` 保持现名，但在文档中定义为 scheduler output。
5. 是否同意外部 simulator adapter 后续默认从 `strict_uniform` 开始，避免一开始引入估算偏差。
