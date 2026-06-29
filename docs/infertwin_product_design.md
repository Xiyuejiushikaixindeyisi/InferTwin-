# InferTwin 产品形态设计文档

## 1. 产品定位

InferTwin 是一个面向 TOB 大型推理服务集群的离线仿真平台。

它的核心目标是提供一套可扩展的离线仿真骨架，用于复现实验条件下的大模型服务行为：

```text
trace
-> request build
-> gateway / instance / scheduler / cache / latency simulation
-> structured metrics
-> outer capabilities
```

可以类比面向 Kimi、火山等大规模 API 服务场景的离线实验平台：

- 请求量大。
- 多租户。
- 多模型。
- 多规格实例。
- 多层 cache。
- 网关路由。
- 实例侧 batching / queueing。
- 需要稳定、可复现、可解释的仿真结果。

## 2. 两类产品

InferTwin 当前产品分为两层：

1. 核心仿真器。
2. 外围能力。

### 2.1 核心仿真器

核心仿真器负责模拟推理服务内部过程，并输出结构化结果。

它负责：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template 选择。
- prefix block hash。
- fixed-routing / future gateway routing。
- instance replay。
- scheduler iteration。
- cache lookup / materialization / eviction。
- cache event stats。
- latency backend 调用。
- request / iteration / sweep metrics。

它不负责：

- 直接渲染 CSV / Markdown。
- 做产品级 hit floor search。
- 生成 dashboard。
- 在 CLI 中重算分析逻辑。
- 把某个外围产品的字段语义反向写进 replay core。

### 2.2 外围能力

外围能力消费核心仿真器输出的 typed result。

外围能力可以包括：

- HBM Cache Capacity Sweep Report。
- InferTwin 表。
- `capacity_sweep.csv`。
- `summary.md`。
- CLI / scripts wrapper。
- Notebook / dashboard。
- 未来 P90 target matching。
- 未来 hit floor search。
- 未来策略推荐。

外围能力的原则：

- 只消费核心仿真器输出。
- 不重算核心 replay 语义。
- 不修改 request / scheduler / cache / latency 的语义。
- 如需新语义，必须新增 replay mode、cache backend、policy、adapter 或 result schema。

## 3. 当前核心仿真器能力

Step1-Step8 与工程优化阶段已完成核心离线 replay 骨架、单实例 DDR/CPU pooling hit accounting 和 KV load latency accounting。

当前已实现：

- CSV trace reader。
- strict OpenAI-style request parser。
- tokenizer / chat template registry。
- GLM-5 tokenizer profile。
- hash-only prefix block hasher。
- `SimulationRequest` build。
- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill replay。
- first-schedule-time prefix cache lookup。
- zero-miss fast-finish。
- finish-time materialization。
- infinite HBM prefix cache。
- finite HBM LRU cache。
- single-instance DDR/CPU LRU prefix cache tier。
- tiered prefix cache backend：HBM contiguous hit -> DDR contiguous hit -> miss。
- `batch_aware_hbm_ddr_lru` streaming cache mode。
- stateful eviction policy。
- streaming cache event writer。
- stats-only cache event sink。
- fitted TTFT latency backend。
- `InstanceProfile / InstanceLatencyProfile` schema。
- true streaming replay 中按 `instance_uuid` 选择 request build context、scheduler setup 和 fitted TTFT backend。
- `ModelRegistry` 作为 model 到 profile / tokenizer / default latency 的索引。
- instance 到 model / deployment / optional latency profile 的绑定校验。
- model-owned runtime defaults，包括 default cache metadata、block size、eviction policy 和 deployment-derived scheduler 参数。
- calibration failure fallback policy schema，当前仅为未来 external calibration harness 预留。
- 实例级 / 模型默认 `kv_load` latency profile，可通过 zero、token-linear、byte-linear mode 控制 DDR/CPU hit 的 KV load latency。
- HBM capacity sweep runner。
- `RunSpec` / profile schema / `ConfigGuard` foundation。
- profile-aware request build path。
- tokenizer-stage long request rejection。
- block size / cache block conversion pure module。
- vLLM-like cached_tokens accounting across batch-aware and infinite replay。
- `MaterializationPolicy` interface with default `FinishTimeMaterializationPolicy`。
- `ServingLatencyProfile` replay-facing latency composition interface。
- `KVLoadLatencyProfile` replay-facing KV load latency component。
- true streaming request shard build。
- per-instance streaming replay。
- streaming capacity sweep runner。
- streaming benchmark harness。
- tier-aware capacity sweep metrics，包括 HBM / DDR / miss tokens。
- tier-aware `cache_events.csv`，可观察 DDR `store` / `lookup_hit`。

当前标准核心结果包括：

- request metrics。
- iteration metrics。
- cache event stats。
- `CapacitySweepResult` / `CapacitySweepRow` typed result。

当前 true streaming path：

```text
CSV trace
-> per-instance request shards
-> per-instance streaming replay
-> streaming metric aggregation
-> CapacitySweepResult
```

旧 in-memory path 仍保留，用于小 trace、debug 和回归等价测试。大 trace 建议使用 streaming path。

## 4. 当前不建模内容

当前核心仿真器仍不建模：

- 真实模型推理。
- 真实物理 KV tensor。
- physical KV slot allocation。
- pinned / refcount。
- progressive block visibility / progressive block materialization。
- compute/load overlap。
- KV load queue、shared bandwidth backpressure、priority 和 load completion event。
- DDR hit promotion。
- SSD / remote cache tier。
- Decode / TPOT 建模。
- decode KV growth。
- gateway routing。
- 实例侧真实排队。
- cross-instance KV pooling。
- 完整多规格实例集群。

其中：

- single-instance DDR/CPU pooling hit accounting 已在 Step7 完成。
- DDR/CPU 命中的 KV load latency accounting 已在 Step8 完成；Step8 v1 默认不建模 overlap、promotion、load queue/backpressure 或 load completion event。
- progressive block visibility 是 V1 必须补齐的核心能力，放到 Step9 作为独立 replay/cache mode 处理。
- Decode / TPOT 建模进入 V2 pending。只有在出现明确 Decode 建模需求，且目标模型部署形态是 PD 混部时，才开启 decode-aware scheduler / replay mode 设计。

这些内容不是被否定，而是待设计、待实现的核心仿真器能力。

### 4.1 V1 / V2 边界

V1 核心仿真器准出范围：

1. Step7：单实例池化，已完成。单个实例可以在 DDR/CPU 侧额外 KV cache 存储中命中，并输出 DDR hit accounting。
2. Step8：KV load latency，已完成。为 DDR/CPU 等非 HBM 命中增加加载时延建模。
3. Step9：progressive chunk visibility，下一阶段。chunk 生成后即可成为后续请求的 KV cache hit 候选，不再等待整个 prompt prefill 完成；TTFT prefill 时间由多个 uncached-token chunk 组合。

V2 之后再处理：

- 复杂 Hybrid 模型，例如 Qwen3.6、DeepSeekV4 等打破 full-attention block 假设的模型。
- gateway simulation。
- 实例侧排队。
- 多实例池化 / 跨实例 KV 命中。
- Decode / TPOT 建模。
- V1 准出后的新一轮工程优化。

V1 准出前，不新增新的外围能力。外围能力只能在核心 replay/cache/latency 语义稳定后消费 typed result。

## 5. 输入形态

InferTwin 输入分为两层：

1. 当前已实现输入。
2. 目标用户输入形态。

当前代码仍保留实验 YAML，用于显式指定 scheduler、latency、cache、capacity sweep 等参数。目标形态会逐步把模型、硬件、部署和实例信息沉淀为 profile，让用户侧输入尽可能轻量。

### 5.1 当前已实现输入

#### 5.1.1 Trace CSV

当前已支持 routed trace，即 CSV 中包含 `instance_uuid`：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 请求 ID |
| `tenant_id` | 租户 ID |
| `instance_uuid` | 已路由到的实例 |
| `request_params` | OpenAI-style request JSON 字符串 |
| `service_start_time` | 模型服务开始处理请求的时间 |

未来 gateway simulation 阶段可以支持不含 `instance_uuid` 的 trace。

#### 5.1.2 Request Params

`request_params` 是完整 request dict：

```json
{
  "model": "...",
  "messages": [],
  "tools": [],
  "max_tokens": 32000,
  "stream": true
}
```

解析约束：

- `messages` 必须是 list。
- `tools` 必须是 list。
- top-level 没有 `system` 字段。
- system prompt 来自 `messages[0].role == "system"`。
- tokenizer / chat template 根据 `model` 选择。

#### 5.1.3 实验 YAML

当前实验 YAML 仍负责显式指定：

- `requested_block_size`。
- scheduler config。
- latency backend。
- cache capacity。
- eviction policy。
- capacity sweep candidates。
- trace path / output path。
- cache event dump 选项。
- streaming shard path / rejected path / sorted trace guard。

这些字段后续会收敛为 `RunSpec` + profile 引用，但在代码完成迁移前仍保留当前实验 YAML。

当前大 trace streaming mode：

```yaml
simulation:
  mode: capacity_sweep_streaming
streaming:
  shard_root: reports/example/streaming_shards
  rejected_path: reports/example/rejected_requests.csv
  require_sorted_trace: true
```

CLI：

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <config.yaml>
```

边界：

- 该模式是显式 opt-in，不改变旧 `capacity_sweep`。
- 第一版要求 trace 已排序。
- streaming path 不构造全量 accepted request list。
- streaming path 不构造 per-instance pending request list。

### 5.2 目标用户输入形态

为了让用户轻量使用 InferTwin，目标形态中用户侧原则上只需要选择：

- `model_name`，例如 `glm-v5.1`。
- `requested_block_size`。
- trace 路径。
- 输出路径。
- capacity sweep 候选容量。

更具体的模型、硬件、部署和实例信息应统一沉淀在 profile config 中。用户可以使用默认 profile，也可以手动指定更高级的 profile。

### 5.3 RunSpec

`RunSpec` 表示一次仿真实验的用户输入。

职责：

- 记录 trace 路径和输出路径。
- 记录用户选择的 `model_name`。
- 记录 `requested_block_size`。
- 记录本次实验模式，例如 single replay 或 capacity sweep。
- 记录 capacity sweep 候选。
- 引用 model / deployment / instance profile。
- 控制是否 dump cache event 明细。

目标结构：

```yaml
run:
  trace_path: data/samples/sample_trace.csv
  output_dir: reports/example
  mode: capacity_sweep
  model_name: glm-v5.1
  requested_block_size: 16
  capacity_sweep:
    hbm_capacity_blocks: [1024, 2048, 4096]
  profiles:
    model: configs/models/glm-v5.1.yaml
    deployment: configs/deployments/glm-v5.1_ascend_tp8.yaml
    instances: null
  output:
    dump_cache_events_for_capacities: []
```

`RunSpec` 是实验入口，不承载模型固有参数和部署启动参数。

### 5.4 Model / Deployment / Hardware / Instance Profiles

为避免同一个模型在不同硬件、不同并行策略、不同 PD 配比下出现配置冲突，InferTwin 目标形态采用四层 profile。

#### 5.4.1 ModelProfile

`ModelProfile` 只记录模型固有信息和默认 profile 引用。

目录：

```text
configs/models/<model_name>.yaml
```

每个模型一个 YAML 文件，文件名使用模型名称。

示例：

```text
configs/models/glm-v5.1.yaml
```

目标结构：

```yaml
model_name: glm-v5.1
aliases:
  - glm-v5
tokenizer:
  profile: glm-v5
  chat_template: tokenizers/glm-v5/chat_template.jinja
request_schema:
  style: openai_chat
  tools_supported: true
defaults:
  deployment_profile: configs/deployments/glm-v5.1_default.yaml
kv_cache:
  block_size_default: 16
  storage_formula: null
```

#### 5.4.2 HardwareProfile

`HardwareProfile` 记录硬件信息，供 InferTwin、TTFT adapter、KV load latency adapter 和容量换算使用。

目录：

```text
configs/hardware/<hardware_name>.yaml
```

目标结构：

```yaml
hardware_name: ascend-...
hbm_gb: ...
memory_bandwidth_gbps: ...
interconnect:
  type: ...
```

#### 5.4.3 DeploymentProfile

`DeploymentProfile` 记录模型如何部署在某类硬件上。

目录：

```text
configs/deployments/<deployment_name>.yaml
```

目标结构：

```yaml
deployment_name: glm-v5.1_ascend_tp8
model_name: glm-v5.1
hardware_profile: configs/hardware/ascend-....yaml
pd_separation:
  enabled: false
  ratio: null
parallelism:
  tensor_parallel_size: ...
  pipeline_parallel_size: ...
  expert_parallel_size: ...
launch_args:
  max_num_seqs: ...
  max_model_len: ...
  max_num_batched_tokens: ...
  gpu_memory_utilization: ...
features:
  multi_tier_cache:
    enabled: false
  pooling:
    enabled: false
  sparse_attention:
    enabled: false
  speculative:
    enabled: false
    method: null
    speculative_drop_blocks: 0
env:
  HCCL_BUFFSIZE: ...
```

#### 5.4.4 ModelRegistry

`ModelRegistry` 是全局已登记模型表，负责把模型名映射到模型 profile、tokenizer/chat profile 和默认 latency profile。

目录：

```text
configs/models/registry.yaml
```

当前第一版 schema：

```yaml
models:
  glm-v5.1:
    model_profile_path: configs/models/glm-v5.1.yaml
    tokenizer_profile: glm-v5
    chat_template_profile: glm-v5
    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-example
      fitted_ttft:
        profile: glm-v5.1_default_ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.01
        calibrated_from: default_registry
        calibration_window_requests: 500
      kv_load:
        ddr_ms_per_cached_token: 0.0
        remote_ms_per_cached_token: 0.0
```

语义：

- registry key 必须与 `ModelProfile.name` 一致。
- `tokenizer_profile` 必须与 `ModelProfile.tokenizer_profile` 一致。
- `default_latency.model_name` 必须匹配 `ModelProfile.name` 或 aliases。
- registry 是索引和默认值来源，不改变 replay、scheduler、cache 语义。

#### 5.4.5 InstanceProfile

`InstanceProfile` 记录 trace 中 `instance_uuid` 与模型、部署 profile、实例级 latency profile 的关系。

目录：

```text
configs/instances/<cluster_name>.yaml
```

当前第一版 schema 已支持：

```yaml
instances:
  name: local-fixed-route-latency-example
  latency_profiles:
    instance-a-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-fast
      fitted_ttft:
        profile: instance-a-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.010
        calibrated_from: synthetic
        calibration_window_requests: 500
      kv_load:
        ddr_ms_per_cached_token: 0.0
        remote_ms_per_cached_token: 0.0
  items:
    instance-a:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
```

实验 config 中通过下面字段启用：

```yaml
instance_latency:
  profile_path: configs/instances/local-fixed-route-latency-example.yaml
  require_all_trace_instances: true

model_registry:
  profile_path: configs/models/registry.yaml

latency_fallback:
  on_calibration_failure: use_model_default
```

当前语义：

- 未配置 `instance_latency` 时，streaming runner 使用全局 latency backend。
- 配置 `instance_latency.profile_path` 后，streaming runner 按 `instance_uuid` 选择 TTFT backend。
- 如果 instance 声明了 `latency_profile`，使用实例专属 fitted TTFT backend。
- 如果 instance 没有 `latency_profile`，且配置了 `model_registry.profile_path`，使用该实例 `model_name` 对应的 model default latency。
- trace 中出现实例表未声明的 `instance_uuid` 时 fail-fast。
- 启用 model registry 后，instance 必须声明 `model_name`，且该 model 必须存在于 registry。
- 多个实例可以共享同一个 deployment，但拥有不同 TTFT 超参数。
- `kv_load` 字段已参与 Step8 replay-facing latency composition；默认 `mode=zero` 时不增加时延，`token_linear` / `byte_linear` 可让 DDR/CPU hit tokens 或 bytes 参与 KV load latency。
- `latency_fallback` 只用于未来 external calibration failure；request build、tokenizer、scheduler、cache、replay 错误不能 fallback。
- 动态每 500 条请求重新拟合 TTFT 尚未实现；当前只保留 schema 和策略字段。

目标结构会进一步扩展为：

```yaml
cluster_name: example_cluster
instances:
  - instance_uuid: instance-a
    deployment_profile: configs/deployments/glm-v5.1_ascend_tp8.yaml
  - instance_uuid: instance-b
    deployment_profile: configs/deployments/glm-v5.1_ascend_tp8.yaml
```

当前 fixed-routing replay 可以在没有 `InstanceProfile` 时，把 trace 中所有 `instance_uuid` 视为使用同一个全局 latency/backend 配置。启用 model registry 和 instance runtime 后，streaming path 已支持实例绑定模型，并按模型默认运行参数选择 tokenizer、scheduler chunk、block size、default cache metadata 和 TTFT fallback。

这仍不是完整 heterogeneous instance cluster simulation：当前 capacity sweep 会用 sweep candidate 覆盖模型默认 HBM capacity；Step7 已补齐单实例 DDR/CPU pooling hit accounting，Step8 已补齐 DDR/CPU hit 的 KV load latency accounting，但 SSD / remote tier、Hybrid cache group、gateway 和实例侧排队仍需后续核心能力补齐。

### 5.5 模型名解析与冲突处理

`request_params.model` 是 trace 中真实请求的模型字段。

`RunSpec.model_name` 是用户声明本次仿真的期望模型。

目标规则：

- 单模型 replay 中，`request_params.model` 必须与 `RunSpec.model_name` 相同，或命中 `ModelProfile.aliases`。
- 如果不一致，InferTwin 必须显式失败或进入 `config_guard`，不能静默用 `RunSpec.model_name` 覆盖 trace。
- 多模型 trace 不应使用单个 `RunSpec.model_name` 覆盖所有请求；未来需要 model routing / instance profile 明确指定。
- tokenizer / chat template 最终由解析后的 `ModelProfile` 决定。

### 5.6 启动参数和高级特性

启动参数应参考 `docs/notes/simulator_integration_guide.md` 中的部署脚本整理。

固定启动参数包括：

- `max_num_seqs` / `max-num-seqs`。
- `max_model_len` / `max-model-len`。
- `max_num_batched_tokens` / `max-num-batched-tokens`。
- `gpu_memory_utilization` / `gpu-memory-utilization`。
- chunked prefill 相关参数。
- prefix cache 相关参数。
- MTP 相关参数。

这些参数归属于 `DeploymentProfile.launch_args`。

MTP / EAGLE / EAGLE3 会影响可复用 block 数量。若部署开启 speculative decoding，InferTwin 应显式记录 `speculative_drop_blocks`，并在 cached_tokens accounting 中使用该信息：

```text
cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)
```

当前 vLLM `use_eagle()` 路径下，`mtp` / `eagle` / `eagle3` 可视为 one-block drop，即 `speculative_drop_blocks = 1`。

工程优化 EO-H 已将 `speculative_drop_blocks` 贯穿到 replay-facing cached_tokens accounting。注意：这只影响 usage cached_tokens 统计，不代表 InferTwin 已经模拟 speculative decode 的真实 draft/verify 流程。

通信缓冲参数包括：

- `HCCL_BUFFSIZE`。
- 其他 HCCL / 通信相关环境变量。

模型相关设置包括：

- tokenizer profile。
- chat template。
- tool calling / tools 解析约束。
- 模型 request schema 兼容边界。

这些字段第一版可以只作为配置记录，不要求立即接入 replay。

### 5.7 KV Cache GB 到 Block 数转换信息

核心 replay 当前仍使用 `hbm_capacity_blocks`。为了让用户能用更直观的存储容量配置实验，InferTwin 后续应提供 GB / GiB 到 block 数的转换能力。

该能力属于外围能力，不属于核心 replay。

profile 需要为转换提供足够信息：

```yaml
kv_cache:
  num_layers: ...
  num_kv_heads: ...
  head_dim: ...
  kv_dtype_bytes: ...
  block_size_default: 16
parallelism:
  tensor_parallel_size: ...
```

第一版建议只支持“显式 KV cache 容量”转换，而不是从整卡 HBM 自动扣除模型权重、runtime buffer 和碎片：

```text
input: kv_cache_gb, model/deployment/hardware profile, requested_block_size
output: hbm_capacity_blocks
```

建议基础公式：

```text
bytes_per_token_per_rank =
  2 * num_layers * num_kv_heads_per_rank * head_dim * kv_dtype_bytes

bytes_per_block =
  requested_block_size * bytes_per_token_per_rank

hbm_capacity_blocks =
  floor(kv_cache_bytes / bytes_per_block)
```

其中：

- `2` 表示 K 和 V。
- `num_kv_heads_per_rank` 需要结合模型 GQA/MQA 参数和并行策略确定。
- `kv_dtype_bytes` 来自 KV precision。
- GB / GiB 口径必须在工具参数和输出中显式标注。

未来可参考 KV Cache Size Calculator 的输入口径。该页面公开展示的输入项包括 model family、model、tokens per sequence、sequences、KV precision、indexer precision、是否包含 draft KV cache、是否包含 linear-attention state：

```text
https://kvcache.ai/tools/kv-cache-calculator/
```

当前 Step6 v1 仍只接受 `hbm_capacity_blocks`。GB 到 block 转换工具实现后，可以作为便捷外围入口生成 `hbm_capacity_blocks`，但 replay core 仍以 block 数为准。

## 6. 当前输出

核心仿真器输出 typed result。

外围 report/export 当前可生成：

```text
request_metrics.csv
iteration_metrics.csv
cache_events.csv
capacity_sweep.csv
summary.md
benchmark.json
```

其中 Step6 的标准输出是：

```text
capacity_sweep.csv
summary.md
```

`capacity_sweep.csv` 是 long-format：

```text
hbm_capacity_blocks,scope,instance_uuid,...,kv_hit_rate,p90_ttft_ms
```

`scope` 当前支持：

- `trace`
- `instance`

### 6.1 Streaming Benchmark

true streaming benchmark 是开发和容量压测辅助工具，不是核心 replay 语义本身。

命令：

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

输出指标包括：

- `requests_per_second`
- `iterations_per_second`
- `cache_events_per_second`
- `peak_traced_memory_mb`
- `max_rss_mb`
- `total_elapsed_ms`

大规模 benchmark 不进入默认 pytest。

## 7. 核心指标口径

### 7.1 Prefix Hit

InferTwin 当前统计有效连续 prefix hit。

规则：

```text
从 prompt 起点开始连续查 block。
遇到第一个 miss 后，后续 block 即使存在也不计入 effective hit。
```

核心字段：

- `hbm_hit_tokens`
- `ddr_hit_tokens`
- `miss_tokens`
- `effective_hit_rate`
- `kv_hit_rate`

HBM-only mode 中 DDR 字段为 0；`batch_aware_hbm_ddr_lru` mode 中 DDR 字段记录同实例 DDR/CPU tier 的 effective hit tokens。

### 7.2 Block Size 术语

后续技术路线和代码接口应区分三层 block size：

- `requested_block_size`：用户输入或启动参数中的 block size。
- `runtime_block_size`：真实运行时生效的 block size，可能被模型或平台代码覆盖。
- `effective_block_size`：用于 `cached_tokens` 统计的最终 block size，可能包含 PCP / DCP 倍数和 hybrid cache group LCM 对齐。

工程优化阶段已新增 `BlockSizeResolver`、`CacheBlockConversionPolicy` 和 replay-facing cached_tokens accounting。当前 HBM LRU replay 可以记录并使用 `requested_block_size`、`runtime_block_size`、`effective_block_size` 和 `speculative_drop_blocks`。unsupported profile 组合仍应通过 `ConfigGuard` 拒绝或显式标记。

### 7.3 Speculative Decoding 对 Prefix Hit 的影响

当前 HBM LRU replay 已在 cached_tokens accounting 层支持 speculative drop block 口径。如果 `DeploymentProfile.features.speculative.enabled = true`，prefix hit 会先得到连续匹配的 `matched_blocks`，再根据部署参数扣减不可复用 block：

```text
cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)
```

影响范围：

- `hbm_hit_tokens` 应基于 `cached_blocks` 计算。
- 被 speculative decoding 丢弃的 matched blocks 不应计入可复用 cache hit。
- zero-miss fast-finish 必须基于 accounted `cached_blocks` / `miss_tokens` 判断。
- materialization 后哪些 block 可见，需要在 speculative replay/cache mode 中单独定义。

边界：

- 已实现的是 usage cached_tokens accounting。
- 尚未实现 speculative decoding 的真实 draft/verify decode 流程。
- 如果 deployment profile 启用的 speculative 行为无法映射为 `speculative_drop_blocks`，应进入 `ConfigGuard`，不能静默猜测。

### 7.4 TTFT

当前 TTFT：

```text
ttft_ms = finish_time_ms - arrival_time_ms
```

长期分解口径：

```text
request TTFT =
  queue_waiting_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

当前实现：

```text
queue_waiting_ms = 0
uncached_prefill_compute_ms = fitted_ttft(uncached_tokens)
kv_load_ms = KVLoadLatencyProfile(ddr_hit_tokens, ddr_hit_bytes)
```

`queue_waiting_ms` 不是实例静态超参数，后续应由 queue simulation 给出，不放入实例 latency profile。

`kv_load_ms` 默认 `mode=zero` 时为 0；Step8 已支持 `token_linear` / `byte_linear`，可让 DDR/CPU hit 进入 TTFT。实例 latency profile 可配置：

```text
ddr_ms_per_cached_token
remote_ms_per_cached_token
```

当前只实现 DDR/CPU tier 的本实例 hit latency。remote 命中尚未实现；未来 remote tier 接入后，建议使用稳定口径：

```text
kv_load_ms =
  ddr_hit_tokens * ddr_ms_per_cached_token
  + remote_hit_tokens * remote_ms_per_cached_token
```

默认 latency backend：

```text
FittedTTFTLatencyBackend
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

这是一种工程近似，不代表真实模型推理过程。

## 8. 核心仿真器长期扩展路线

InferTwin 的仿真平台具有良好的可扩展性。后续核心仿真器建议按以下顺序开发。

### 8.1 Block Size / Cache Block Conversion Module

目标：

- 将用户输入的 `requested_block_size`、部署启动参数、runtime block size、CP、speculative decoding 和 hybrid cache group 信息转换为 prefix cache lookup 使用的 effective block 语义。
- 让 InferTwin 的 `cached_tokens` 统计尽可能贴近 vLLM / vLLM-Ascend。

设计方向：

```text
BlockSizeResolver
CacheBlockConversionPolicy
CachedTokensCalculator
```

输入：

- `RunSpec.requested_block_size`。
- `DeploymentProfile.launch_args`。
- runtime logs 中的 block size override。
- PCP / DCP。
- speculative method: `mtp` / `eagle` / `eagle3`。
- cache group block sizes。

输出：

- `runtime_block_size`。
- `effective_block_size`。
- `matched_blocks`。
- `cached_blocks`。
- `cached_tokens`。
- `config_guard` / unsupported reason。

关键语义：

```text
max_cache_hit_length = prompt_tokens - 1
matched_blocks = floor(max_cache_hit_length / effective_block_size)
cached_tokens = matched_blocks * effective_block_size
```

当 MTP / EAGLE / EAGLE3 启用时：

```text
speculative_drop_blocks = 1
cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)
cached_tokens = cached_blocks * effective_block_size
```

该模块是核心仿真器能力，因为它会改变 KV cache hit 统计结果。GB / GiB 到 block 数转换仍是外围能力，因为它只生成容量输入，不改变 replay 语义。

参考笔记：

```text
docs/notes/cached_tokens_calculation_logic.md
```

状态：

```text
已完成第一版：EO-C/EO-D/EO-H 已实现 resolver、conversion policy、profile-aware request build metadata 和 replay-facing accounting。
后续仍需补充更多 vLLM / vLLM-Ascend runtime log 解析和 unsupported manager 组合 guard。
```

### 8.2 多级 Cache Backend

长期目标：

```text
HBM -> DDR -> SSD / remote store
```

设计方向：

- 参考 Mooncake Store / disaggregated KV cache。
- 新增 cache tier schema。
- 新增 multi-tier lookup result。
- 新增 tier-level event。
- 新增 promotion / demotion 规则。
- 保持 HBM-only backend 语义不变。

状态：

```text
Step7 已完成 single-instance HBM -> DDR/CPU hit accounting。
SSD / remote store、promotion / demotion、cross-instance pooling 仍待设计、待实现。
```

### 8.3 KV Load Latency

目标：

```text
ttft =
  scheduler_wait
  + prefill_compute_time(miss_tokens)
  + kv_load_time(hit_tokens_by_tier)
```

设计方向：

- HBM hit 可以近似为 0 或极低。
- DDR / SSD / remote hit 需要显式 load latency。
- Ramulator2 可作为 DDR 访问仿真器。
- 端到端 restore path 可能还包括 transfer、HBM write、promotion。

状态：

```text
待设计，待实现。
```

### 8.4 Instance Queue Simulation

排队建议分两层：

1. 真实机器侧排队。
2. continuous batching / chunked prefill 导致的 scheduler 内部排队。

第一层：

- 请求到达实例但尚未 tokenizer / admission。
- 可采用传统调度算法。
- 可建模 admission control、tenant fairness、priority queue。

第二层：

- 请求已经进入实例 replay。
- 受 `max_num_batched_tokens`、`max_num_seqs`、chunked prefill 影响。
- 可以参考 vLLM scheduler 内部实现。

状态：

```text
待设计，待实现。
```

### 8.5 Gateway Simulation

目标：

```text
trace request
-> gateway policy
-> selected instance_uuid
-> instance replay
```

设计方向：

- 支持 trace 不包含 `instance_uuid`。
- 使用仿真器内置策略进行路由。
- 参考 llm-d 等 gateway / routing 设计。
- 支持 cache-aware routing、load-aware routing、tenant-aware routing。

gateway layer 不应修改实例内 scheduler/cache/latency 语义。

状态：

```text
待设计，待实现。
```

### 8.6 实例集群仿真

目标：

- 支持多模型。
- 支持多规格实例。
- 支持不同硬件、不同 TTFT 拟合公式。
- 支持全局维护 model / hardware / deployment / instance profile 表。

设计方向：

```text
InstanceProfile:
  cluster_name
  instances:
    - instance_uuid
      deployment_profile
```

`DeploymentProfile` 负责承载 model、hardware、scheduler、cache、latency profile 等配置。对于不同规格实例，只要具备不同 deployment profile，即可进入统一 replay。

状态：

```text
第一版已完成：true streaming replay 支持按 instance_uuid 选择 fitted TTFT backend。
仍待实现：per-instance scheduler config、per-instance cache capacity、动态 per-instance TTFT refit、完整多规格 deployment replay。
```

### 8.7 Cache 管理与稀疏注意力

目标：

- 支持 full-prefix cache 之外的 cache 管理。
- 支持稀疏注意力、sliding window、sink token、hybrid attention 等场景。
- 参考 Omini cache 的 cache 管理和稀疏注意力设计；具体论文、代码或内部资料待补充后再进入技术方案。

设计方向：

```text
FullPrefixCacheManager
SparseAttentionCacheManager
HybridAttentionCacheCoordinator
OminiCacheStyleCacheManager
```

要求：

- 不改变当前 full-prefix contiguous cache 的语义。
- 新增 cache manager / cache coordinator。
- 新增 metrics 和验收数据。
- 如果稀疏注意力改变“哪些 block 可复用、哪些 token 需要加载、哪些 block 应淘汰”的定义，必须新增 cache manager 或 replay mode，不能复用当前 HBM LRU 字段并改变语义。

状态：

```text
待设计，待实现。
```

### 8.8 Mooncake 多实例池化

目标：

```text
instance-local HBM
-> pooled DDR / remote memory
-> other instance KV
```

设计方向：

- 跨实例 cache lookup。
- pooling index。
- remote KV availability。
- remote KV load latency。
- pooling capacity 和 eviction。
- 一致性、可见性、租户隔离。

状态：

```text
待设计，待实现。
```

## 9. 外围能力登记

每实现一个外围能力，应在本节追加：

- 背景。
- 产品目标。
- CLI。
- 输入输出。
- 使用方法。
- 与核心仿真器的边界。

### 9.1 HBM Cache Capacity Sweep Report

状态：

```text
已实现，Step6 v1 通过验收。
```

背景：

用户给定一段 trace，InferTwin 使用不同 `hbm_capacity_blocks` 进行 replay，得到每个容量下的 KV cache hit 和 P90 TTFT。

CLI：

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep \
  --config configs/experiments/step6_capacity_sweep.yaml
```

输出：

```text
reports/step6_capacity_sweep/capacity_sweep.csv
reports/step6_capacity_sweep/summary.md
```

边界：

- 这是外围 report/export 能力。
- `CapacitySweepRunner` 返回结构化 `CapacitySweepResult`。
- CSV / Markdown 只序列化 typed result，不重算 replay 语义。

验收文档：

```text
docs/archive/step6/03_acceptance_e2e.md
```

### 9.2 P90 Target Matching / Hit Floor Search

状态：

```text
未实现，未来外围能力。
```

说明：

“输出目标 P90 TTFT 对应的 hit floor” 是外围能力，不是核心仿真器本身。它应消费核心 simulator 的 structured metrics，而不改变 replay、cache、latency 语义。

### 9.3 Deployment Script to Profile Config

状态：

```text
未实现，未来外围能力。
```

背景：

用户希望轻量使用 InferTwin，而不是手写完整模型、硬件和部署 config。InferTwin 可以提供一个外围工具，读取完整部署脚本并生成 profile config。

该能力依赖 `ModelProfile`、`HardwareProfile`、`DeploymentProfile`、`InstanceProfile` schema 先完成设计和校验。

输入：

- `model_name`。
- 单体部署脚本，或 PD 分离部署下的 P 节点启动脚本、D 节点启动脚本。
- 如果是 PD 分离部署，还需要 PD 配比。
- 可选输出 profile 名称。

提取信息：

- 硬件信息。
- 是否 PD 分离。
- 并行策略。
- vLLM / vLLM-Ascend 启动参数。
- speculative decoding 相关参数和 `speculative_drop_blocks`。
- HCCL / 通信缓冲参数。
- tokenizer / chat template / tool calling 等模型相关设置。
- 多级缓存、池化、稀疏注意力等高级特性开关。

输出：

```text
configs/models/<model_name>.yaml
configs/hardware/<hardware_name>.yaml
configs/deployments/<deployment_name>.yaml
configs/instances/<cluster_name>.yaml
```

使用形态可以是 package CLI 或 scripts wrapper。示例目标形态：

```bash
infertwin profile import-deployment \
  --model-name glm-v5.1 \
  --prefill-script deploy_prefill.sh \
  --decode-script deploy_decode.sh \
  --pd-ratio 1:4 \
  --output-dir configs/
```

边界：

- 这是外围能力，不属于核心 replay。
- 生成器只负责提取和序列化 profile config。
- 生成后的 profile config 被核心仿真器和外部 TTFT adapter 读取。
- 生成器不能决定 replay 语义；无法表达的新语义必须回到核心仿真器设计。
- 如果脚本中存在无法识别的关键启动参数，应显式报错或标记 unsupported，不应静默猜测。

### 9.4 KV Capacity Planner / GB to Block Converter

状态：

```text
未实现，未来外围能力。
```

背景：

核心 replay 使用 `hbm_capacity_blocks`，但用户在容量规划时更自然地使用 GB / GiB。InferTwin 应提供一个轻量工具，根据模型、硬件、部署和 block size，将显式 KV cache 容量转换为 block 数。

产品名建议为 `KV Capacity Planner`，CLI 可以保留直观命令 `infertwin capacity gb-to-blocks`。

输入：

- `model_name` 或 `ModelProfile`。
- `DeploymentProfile`，用于读取并行策略、KV precision、默认 block size 等。
- `HardwareProfile`，用于校验硬件容量信息。
- 显式 KV cache 容量，例如 `--kv-cache-gb 120`。
- `requested_block_size`，可由用户指定，也可使用 profile 默认值。
- GB / GiB 单位口径。

输出：

```text
hbm_capacity_blocks
bytes_per_block
kv_cache_bytes
calculation_summary
```

使用形态可以是 package CLI 或 scripts wrapper。示例目标形态：

```bash
infertwin capacity gb-to-blocks \
  --model-name glm-v5.1 \
  --deployment-profile configs/deployments/glm-v5.1_ascend_tp8.yaml \
  --kv-cache-gb 120 \
  --block-size 16
```

也可以生成 RunSpec 片段：

```bash
infertwin capacity gb-to-blocks \
  --deployment-profile configs/deployments/glm-v5.1_ascend_tp8.yaml \
  --kv-cache-gb 120 \
  --block-size 16 \
  --output-run-fragment configs/experiments/generated_capacity.yaml
```

边界：

- 这是外围能力，不属于核心 replay。
- 它只把容量单位转换成 `hbm_capacity_blocks`。
- 它不能改变 cache lookup、materialization、eviction、TTFT 语义。
- 第一版只接受显式 KV cache GB / GiB，不自动从整卡 HBM 扣除模型权重和 runtime overhead。
- 如果 profile 缺少必要模型参数、KV precision 或并行策略，应显式失败或标记 `config_guard`。
- draft KV cache、linear-attention state、多级缓存、池化容量等高级项应作为后续扩展，不混入第一版基础转换。

## 10. Notes 索引

外部仿真器和部署形态学习笔记保存在 `docs/notes/`，不作为核心产品形态主文档展开。

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
docs/notes/cached_tokens_calculation_logic.md
```

## 11. 产品结论

InferTwin 当前最核心的价值是：

```text
提供一个可复现、可扩展、可解释的大型推理服务集群离线仿真骨架。
```

InferTwin 表、capacity sweep、hit floor search、dashboard 都是该骨架之上的外围能力。

后续工程优化和 V1 开发必须先声明：

```text
本阶段是在开发核心仿真器，还是开发外围能力。
```

这条声明是产品设计和工程治理的一部分。

V1 准出前，不新增新的外围能力；V1 准出后，外围能力也只能消费稳定的核心 typed result，不能反向修改核心 replay 语义。
