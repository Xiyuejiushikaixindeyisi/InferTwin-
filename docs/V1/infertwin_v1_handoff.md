# InferTwin V1 交接说明

本文用于向同事快速介绍 InferTwin V1：它是什么、已经能仿真什么、和真实 vLLM / vLLM-Ascend / Mooncake 推理服务有什么差异、当前结果应如何理解，以及后续可以怎么扩展。

参考文档：

- `docs/infertwin_product_design.md`
- `docs/V1/infertwin_v1_vs_real_serving_comparison.md`
- `docs/V1/infertwin_v1_error_analysis.md`
- `docs/core_simulator_technical_plan.md`

## 1. InferTwin 是什么

InferTwin 是一个面向 TOB 大型推理服务集群的离线仿真平台。

它不部署真实模型，也不依赖真实 GPU/NPU 执行推理，而是读取真实或合成 trace，在离线环境中 replay 推理服务的核心链路：

```text
trace
-> request build
-> tokenizer / chat template
-> prefix block hash
-> per-instance scheduler replay
-> cache lookup / store / eviction
-> latency accounting
-> typed metrics / reports
```

InferTwin 的目标不是替代真实线上压测，而是在没有显卡或不方便部署模型的情况下，快速回答这些问题：

- 给定 trace 和模型配置，prefix cache hit 大概是多少。
- 不同 HBM / DDR cache 容量会如何影响 hit-rate 和 P90 TTFT。
- 单实例或多实例固定路由下，请求在各实例内如何 replay。
- DDR/CPU pooling hit 和 KV load latency 会如何影响 TTFT。
- 长 prompt / chunked prefill 场景中，chunk 生成后可见性如何影响后续命中。

## 2. 核心仿真器与外围能力

InferTwin 分为两层：

1. 核心仿真器。
2. 外围能力。

核心仿真器负责 replay 语义：

- trace schema guard。
- request build。
- tokenizer / chat template。
- prefix block hash。
- scheduler replay。
- cache lookup / materialization / store / eviction。
- latency backend。
- per-instance isolation。
- typed metrics / typed result。

外围能力只消费核心仿真器输出，不反向修改 replay 逻辑，例如：

- HBM capacity sweep report。
- `capacity_sweep.csv`。
- `summary.md`。
- HitFloor 表。
- 未来 P90 target matching。
- 未来 dashboard / notebook / 策略推荐。

如果后续外围能力需要新的仿真语义，应该新增 replay mode、cache backend、policy、adapter 或 schema，而不是在 report / CLI 中重算核心逻辑。

## 3. V1 已完成能力

InferTwin V1 已完成核心离线 replay 骨架，并完成 Step7、Step8、Step9 的核心能力。

当前已支持：

- strict trace parser。
- OpenAI-style request params parser。
- tokenizer / chat template registry。
- model registry 和 instance-model binding。
- fixed-routing 多实例隔离 replay。
- true streaming 大 trace 主路径。
- vLLM-like continuous batching / chunked prefill replay。
- hash-only prefix block hasher。
- vLLM-like cached tokens accounting。
- HBM LRU prefix cache。
- DDR/CPU LRU prefix cache tier。
- tiered prefix cache：HBM hit -> DDR hit -> miss。
- cache event streaming writer。
- stateful eviction policy interface。
- fitted TTFT backend。
- `ServingLatencyProfile`。
- `KVLoadLatencyProfile`。
- deterministic shared-link FIFO KV load wait accounting。
- progressive full-block materialization。
- chunk-level TTFT timeline。
- capacity sweep runner。

推荐大 trace 使用 streaming path。legacy in-memory path 仅适合小 trace、debug 和回归测试。

## 4. 当前输入和输出

### 4.1 输入

当前主要输入是已路由 trace CSV：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 请求 ID |
| `tenant_id` | 租户 ID |
| `instance_uuid` | 已路由到的实例 |
| `request_params` | OpenAI-style request JSON |
| `service_start_time` | 模型服务开始处理请求的时间 |

`request_params` 中应包含模型名、messages、tools、max_tokens 等字段。tokenizer / chat template 会根据模型 profile 选择。

当前核心 replay 不做 gateway routing。没有 `instance_uuid` 的 trace 应先由外围 normalizer 或未来 gateway simulator 显式处理。

### 4.2 输出

核心输出是 typed metrics，包括：

- request metrics。
- iteration metrics。
- cache event stats。
- capacity sweep typed result。

常用外围输出包括：

- `capacity_sweep.csv`。
- `summary.md`。
- `cache_events.csv`。

典型 capacity sweep 结果会按 trace 和 instance 输出：

- cache capacity。
- HBM hit tokens / rate。
- DDR hit tokens / rate。
- miss tokens / rate。
- P90 TTFT。
- compute wait。
- KV load wait。
- KV load ms。

## 5. InferTwin 如何建模 TTFT

InferTwin V1 不执行真实模型 kernel。TTFT 来自 replay timeline 和 latency profile。

progressive mode 下，请求级 TTFT 口径是：

```text
ttft_ms = compute_wait_ms
        + kv_load_wait_ms
        + uncached_prefill_compute_ms
        + unattributed_ttft_ms
```

含义：

- `compute_wait_ms`：请求已进入 engine，但等待 chunked prefill scheduler 选中的时间。
- `kv_load_wait_ms`：DDR/CPU hit 产生的 deterministic transfer queue wait。
- `uncached_prefill_compute_ms`：未命中 token 的 prefill compute，由 fitted TTFT backend 给出。
- `unattributed_ttft_ms`：replay 粒度残差，不是物理建模结果。

当前不建模：

- 真实模型算子。
- 真实 kernel shape 非线性。
- Decode / TPOT。
- gateway / server 入口排队。
- compute/load overlap。
- 真实 Mooncake / HCCL / RDMA / DMA backpressure。

## 6. InferTwin 如何统计 Prefix Cache Hit

InferTwin 的 prefix cache hit 是 hash-only block replay。

核心规则对齐 vLLM：

```text
max_cache_hit_length = prompt_tokens - 1
effective_block_size = runtime_block_size * PCP * DCP
cached_tokens = full matched effective blocks, with optional MTP/EAGLE one-block drop
```

当前统计：

- `hbm_hit_tokens`
- `ddr_hit_tokens`
- `miss_tokens`
- `kv_hit_tokens`
- `kv_hit_rate`
- `kv_load_tokens`
- `kv_load_bytes`

Step9 progressive mode 下，scheduled chunk finish 后 newly completed full blocks 可以进入 cache，后续请求可以命中。partial block 仍不可见。

## 7. 与真实推理服务的主要区别

| 链路 | InferTwin V1 | 真实 vLLM / vLLM-Ascend / Mooncake |
| --- | --- | --- |
| 模型执行 | 不部署模型，使用 fitted/profile latency | 执行真实 kernel |
| batch | vLLM-like prefill replay | prefill + decode + preemption + PP/LoRA/spec decode |
| cache block | hash metadata | 真实 KV tensor block |
| block 管理 | HBM/DDR LRU residency | BlockPool、refcount、free queue、slot allocation、preemption |
| prefix hit | full block hash replay | KVCacheManager / coordinator / connector lookup |
| DDR/CPU tier | 同实例 metadata tier | CPU offload、layer-wise load/save、async stream |
| 通信链路 | token/byte linear + FIFO accounting | RDMA/HCCL/DMA/CPU copy/TransferEngine |
| remote pooling | 未实现 | Mooncake 可支持 remote/global KV |
| gateway | 未实现 | 真实服务有入口路由 |
| Decode / TPOT | 未实现 | 真实请求生命周期的一部分 |

核心差异可以概括为三点：

1. TTFT 是拟合和 replay 组合，不是真实硬件执行。
2. KV cache 是 hash metadata，不是真实物理存储。
3. 通信是 deterministic abstraction，不是真实 TransferEngine。

## 8. 结果误差如何理解

### 8.1 Prefix Cache Hit

在这些条件下，prefix hit 可信度较高：

- full-attention 模型。
- tokenizer / chat template 正确。
- runtime block size 正确。
- CP / MTP / EAGLE 配置正确。
- fixed-routing trace。
- HBM-only 或同实例 DDR/CPU tier。

粗略误差：

| 场景 | hit-rate 误差 |
| --- | --- |
| full-attention、HBM-only、配置正确 | 0-5pp |
| full-attention、HBM + DDR、配置正确 | 0-10pp |
| 长 prefill、高复用、progressive mode | 0-10pp |
| runtime block size / CP / MTP 配错 | 10pp-50pp+ |
| remote pooling 显著 | 会低估 remote hit 部分 |
| Hybrid / sparse attention | V1 不建议给强结论 |

### 8.2 TTFT

TTFT 是 fitted latency replay，适合看趋势，不应直接当线上真值。

粗略误差：

| 场景 | TTFT 误差 |
| --- | --- |
| HBM-only、TTFT profile 已校准、prefill 主导 | 5%-20% |
| HBM + DDR、KV load profile 已校准 | 10%-30% |
| 未校准 TTFT profile | 20%-60% |
| DDR-heavy、高并发 transfer | 30%-100%+ |
| PD 混部或 decode-heavy | 20%-100%+ |

如果要提升 TTFT 可信度，需要采样线上 TTFT 或外部仿真器结果，校准 fitted backend 和 KV load profile。

## 9. 当前最适合使用的场景

InferTwin V1 适合：

- 大 trace 离线 replay。
- fixed-routing 多实例分析。
- full-attention 模型 prefix cache hit 分析。
- HBM / DDR cache 容量 sweep。
- P90 TTFT 趋势分析。
- 单实例 DDR/CPU pooling 收益分析。
- 长 prompt / code agent trace 的复用分析。
- 没有显卡时快速做离线实验。

当前不适合：

- 真实线上 TTFT 绝对值承诺。
- decode-heavy / PD 混部吞吐评估。
- gateway routing 策略评估。
- remote pooling / cross-instance hit 评估。
- Hybrid Mamba / sparse attention cache group 准确仿真。
- 真实通信链路和硬件微架构研究。

## 10. 后续发展方向

建议后续按“核心仿真器先稳定，外围能力再消费”的原则推进。

核心仿真器方向：

- 真实 KV transfer timeline backend。
- compute/load overlap policy。
- DDR hit promotion 和 load completion event。
- remote pooling / Mooncake global store adapter。
- physical KV slot backend。
- decode-aware scheduler / TPOT 建模。
- gateway simulation。
- instance admission queue。
- Hybrid / sparse attention cache manager。
- 更细粒度 layer / page / chunk 级 KV load。

外围能力方向：

- HitFloor 表。
- P90 target matching。
- 自动 hit floor search。
- deployment script -> profile config。
- GB/GiB -> block capacity conversion。
- dashboard / notebook。
- trace distribution modeling。
- routing policy evaluation report。

## 11. 更长期的发展想象

InferTwin 的潜力不止是做一个 prefix cache hit 仿真工具。它已经具备一个大型推理服务集群离线数字孪生的雏形：用真实 trace、模型 profile、实例配置、cache backend、scheduler replay 和 latency profile，把复杂线上系统拆成可替换、可验证、可扩展的仿真模块。

后续可以从以下方向继续演进。

### 11.1 面向稀疏注意力的 cache 管理研究

稀疏注意力模型会改变传统 KV cache 的组织方式和复用方式。InferTwin 后续可以接入新的 cache manager，研究更适合稀疏注意力模型的 cache 管理策略。

可能方向：

- sparse-aware block layout。
- 不同层、不同 head、不同 attention pattern 的 cache residency policy。
- 针对局部窗口、全局 token、检索 token 的差异化保活策略。
- 面向长上下文 agent trace 的 cache reuse policy。
- 对比 LRU、LFU、TTL、semantic-aware、session-aware 等淘汰算法。

这会让 InferTwin 不只是复现现有系统，而是可以帮助设计下一代稀疏注意力推理框架。

### 11.2 路由策略仿真

当前 V1 使用 fixed-routing trace，即 trace 中已经有 `instance_uuid`。未来可以新增 gateway / routing layer，在没有实例 ID 或需要重放不同路由策略时，由 InferTwin 自己决定请求发往哪个实例。

可研究的问题：

- 按负载路由。
- 按 prefix cache locality 路由。
- 按租户 / session / model 路由。
- 按 HBM / DDR cache 状态路由。
- 按 TTFT / P90 SLO 路由。
- 多模型、多规格实例下的路由策略。

这样可以基于真实 trace 设计更适合公司业务的路由策略，而不必在线上冒险试错。

### 11.3 实例侧排队策略

V1 主要建模 vLLM-like scheduler 内部的 chunked prefill replay，不建模实例入口真实排队。后续可以新增 instance admission queue layer，研究实例侧排队策略如何影响吞吐、P90 TTFT、公平性和缓存命中。

可研究的问题：

- FCFS、priority、tenant-aware queue。
- 长短请求混排。
- prompt length aware admission。
- session-aware admission。
- cache hit aware admission。
- chunked prefill 阶段的 request chunk 排队和调度。

这部分对提升吞吐和稳定性非常重要，尤其适合大规模 API 服务场景。

### 11.4 Gateway 设计评估

InferTwin 可以扩展出一个 gateway simulator，用于评估当前网关设计是否合理，并为更大规模集群做准备。

可评估内容：

- gateway 是否放大了某些实例的热点。
- gateway 是否破坏了 prefix cache locality。
- gateway 是否让租户之间互相干扰。
- gateway 是否能在实例故障、扩缩容、模型迁移时保持稳定。
- gateway 策略对 P90 / P99 TTFT 和 cache hit 的影响。

这会把 InferTwin 从“实例内 replay”推进到“集群级服务治理仿真”。

### 11.5 更细粒度的存储与通信仿真

V1 的 KV load 是 token / byte linear profile，并用 deterministic FIFO 表达 shared-link wait。未来可以继续细化到 page、layer、chunk，甚至读写结构和读写锁。

可能方向：

- page-level KV load。
- layer-wise KV transfer。
- read/write lock simulation。
- async load completion event。
- promotion policy。
- local DDR / remote DRAM / SSD tier。
- Mooncake TransferEngine adapter。
- Ramulator2 calibration adapter。
- bandwidth sharing / priority / backpressure。

这会让 InferTwin 从 cache hit replay 进一步接近存储系统和通信系统研究。

### 11.6 芯片和硬件方向评估

InferTwin 与真实推理执行解耦，因此可以通过配置不同硬件 profile、模型 profile 和 latency profile，离线评估不同芯片或硬件方案对真实业务 trace 的影响。

可研究的问题：

- 更大 HBM 是否更值得。
- 更高 DDR / CPU / remote memory 带宽是否更值得。
- 不同 KV cache 存储层级的收益。
- 不同 block size 对命中和 TTFT 的影响。
- 不同模型结构对 cache 和带宽的压力。
- 某类业务 trace 对芯片设计的真实需求。

这类实验不再被“没有显卡”“没有足够机器”“线上不能试”限制，可以显著减少实验时间。

### 11.7 离线数据生产与产品化分析

InferTwin 可以在离线环境中生成大量结构化数据：

- 每条请求的 hit / miss / TTFT 组成。
- 每个实例的 cache residency。
- 每个租户的请求分布。
- 每个 session 的复用模式。
- 每种 capacity 下的 P90 / P99。
- 每种策略下的 cache event。

这些数据可以进一步支撑：

- dashboard。
- notebook 分析。
- SLO 风险预警。
- cache 策略推荐。
- 路由策略推荐。
- 容量规划。
- 成本收益评估。

InferTwin 的价值不仅是“跑一次仿真”，而是能持续生产高质量离线实验数据。

### 11.8 新推理框架设计

当 InferTwin 具备更完整的 cache manager、scheduler、transfer backend 和 trace modeling 能力后，它可以成为新推理框架设计的实验平台。

尤其是在稀疏注意力模型、超长上下文、agent trace、code agent trace 这些场景中，现有推理框架未必是最优解。InferTwin 可以帮助验证：

- 新 cache layout。
- 新 block organization。
- 新路由策略。
- 新 chunk scheduler。
- 新 storage hierarchy。
- 新 KV transfer protocol。
- 新 request admission policy。

这意味着可以先在 InferTwin 中验证设计，再决定是否进入真实推理框架开发。

### 11.9 扩展到视觉和视频生成模型

InferTwin 当前主要面向 LLM / 多模态 LLM 的 text request replay。未来可以尝试扩展到视觉模型和视频生成模型。

可能方向：

- image generation request trace。
- video generation request trace。
- 多模态输入 token / patch / frame 建模。
- 视觉模型 KV / feature cache 建模。
- 视频生成中的长序列 cache / memory replay。
- 不同生成阶段的 latency profile。

这会把 InferTwin 从 LLM serving simulator 扩展为更通用的生成式 AI 服务仿真平台。

## 12. 交接时最重要的注意事项

1. InferTwin 的长期目标可以很大，但每个阶段必须先声明是核心仿真器还是外围能力。
2. 不要把外围能力写进核心 replay。
3. 不要在 report / CLI 中重算 cache hit 或 TTFT。
4. 如果要改变语义，新增 mode / backend / policy / adapter / schema。
5. 大 trace 默认走 streaming path。
6. tokenizer、chat template、runtime block size 是 prefix hit 正确性的生命线。
7. TTFT 绝对值必须标注“fitted / calibrated / uncalibrated”。
8. Step9 progressive mode 是长 prefill 场景的推荐模式。
9. Hybrid / sparse attention 不要沿用 full-attention 结论。
10. Mooncake / Ramulator2 当前是 calibration source，不是默认 online replay。
11. 当前 V1 最可靠的是 prefix hit 和 capacity sweep 趋势，不是线上 TTFT 绝对值。

## 13. 一句话介绍

InferTwin 是一个面向大型推理服务集群的离线 replay 仿真平台：它用真实 trace、模型 profile 和可插拔 latency/cache 组件，在不部署模型的情况下重放请求、统计 prefix cache hit、评估 cache 容量和 TTFT 趋势，并为后续 gateway、pooling、cache 管理和新推理框架研究提供可扩展仿真骨架。
