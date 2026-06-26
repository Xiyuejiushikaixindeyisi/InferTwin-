# HitFloor 产品形态设计文档

## 1. 产品定位

HitFloor 是一个面向 TOB 大型推理服务集群的离线仿真平台。

它的核心目标不是只服务某一个模型服务、某一张 hit floor 表，或者某一个 P90 TTFT 目标求解器，而是提供一套可扩展的离线仿真骨架，用于复现实验条件下的大模型 API 服务行为：

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

HitFloor 当前产品分为两层：

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
- HitFloor 表。
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

Step1-Step6 已完成核心离线 replay 骨架。

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
- zero-miss / full-prefix-hit fast-finish。
- finish-time materialization。
- infinite HBM prefix cache。
- finite HBM LRU cache。
- stateful eviction policy。
- streaming cache event writer。
- stats-only cache event sink。
- fitted TTFT latency backend。
- HBM capacity sweep runner。

当前标准核心结果包括：

- request metrics。
- iteration metrics。
- cache event stats。
- capacity sweep rows。

## 4. 当前不建模内容

当前核心仿真器仍不建模：

- 真实模型推理。
- 真实物理 KV tensor。
- physical KV slot allocation。
- pinned / refcount。
- progressive block materialization。
- DDR / SSD / multi-tier cache。
- KV load latency。
- TPOT。
- decode KV growth。
- gateway routing。
- 实例侧真实排队。
- cross-instance KV pooling。
- 多规格实例集群。

这些内容不是被否定，而是待设计、待实现的核心仿真器能力。

## 5. 当前输入

### 5.1 Trace CSV

当前已支持 routed trace，即 CSV 中包含 `instance_uuid`：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 请求 ID |
| `tenant_id` | 租户 ID |
| `instance_uuid` | 已路由到的实例 |
| `request_params` | OpenAI-style request JSON 字符串 |
| `service_start_time` | 模型服务开始处理请求的时间 |

未来 gateway simulation 阶段可以支持不含 `instance_uuid` 的 trace。

### 5.2 Request Params

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

### 5.3 Config

当前核心配置包括：

- tokenizer root / default profile / cache scope。
- block size。
- scheduler config。
- latency backend。
- cache capacity。
- eviction policy。
- capacity sweep candidates。

## 6. 当前输出

核心仿真器输出 typed result。

外围 report/export 当前可生成：

```text
request_metrics.csv
iteration_metrics.csv
cache_events.csv
capacity_sweep.csv
summary.md
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

## 7. 核心指标口径

### 7.1 Prefix Hit

HitFloor 当前统计有效连续 prefix hit。

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

Step6 v1 中 DDR 字段保留但恒为 0。

### 7.2 TTFT

当前 TTFT：

```text
ttft_ms = finish_time_ms - arrival_time_ms
```

默认 latency backend：

```text
FittedTTFTLatencyBackend
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

这是一种工程近似，不代表真实模型推理过程。

## 8. 核心仿真器长期扩展路线

HitFloor 的仿真平台具有良好的可扩展性。后续核心仿真器建议按以下顺序开发。

### 8.1 多级 Cache Backend

目标：

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
待设计，待实现。
```

### 8.2 KV Load Latency

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

### 8.3 Instance Queue Simulation

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

### 8.4 Gateway Simulation

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

### 8.5 实例集群仿真

目标：

- 支持多模型。
- 支持多规格实例。
- 支持不同硬件、不同 TTFT 拟合公式。
- 支持全局维护 model / instance / hardware profile 表。

设计方向：

```text
InstanceProfile:
  instance_uuid
  model_name
  hardware_name
  scheduler_config
  cache_config
  latency_profile
```

对于不同规格实例，只要具备不同 TTFT 拟合公式和 cache 配置，即可进入统一 replay。

状态：

```text
待设计，待实现。
```

### 8.6 Cache 管理与稀疏注意力

目标：

- 支持 full-prefix cache 之外的 cache 管理。
- 支持稀疏注意力、sliding window、sink token、hybrid attention 等场景。

设计方向：

```text
FullPrefixCacheManager
SparseAttentionCacheManager
HybridAttentionCacheCoordinator
```

要求：

- 不改变当前 full-prefix contiguous cache 的语义。
- 新增 cache manager / cache coordinator。
- 新增 metrics 和验收数据。

状态：

```text
待设计，待实现。
```

### 8.7 Mooncake 多实例池化

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

用户给定一段 trace，HitFloor 使用不同 `hbm_capacity_blocks` 进行 replay，得到每个容量下的 KV cache hit 和 P90 TTFT。

CLI：

```bash
PYTHONPATH=src python -m hitfloor.cli.main sweep \
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

## 10. Notes 索引

外部仿真器和部署形态学习笔记保存在 `docs/notes/`，不作为核心产品形态主文档展开。

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
```

## 11. 产品结论

HitFloor 当前最核心的价值是：

```text
提供一个可复现、可扩展、可解释的大型推理服务集群离线仿真骨架。
```

HitFloor 表、capacity sweep、hit floor search、dashboard 都是该骨架之上的外围能力。

后续工程优化和 Step7 之后的开发必须先声明：

```text
本阶段是在开发核心仿真器，还是开发外围能力。
```

这条声明是产品设计和工程治理的一部分。
