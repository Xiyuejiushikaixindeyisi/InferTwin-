# InferTwin V1 验收记录

本文用于记录 InferTwin V1 的逐项验收结论。

验收顺序：

1. TTFT 建模。
2. batch。
3. prefix cache hit。
4. KV block management。
5. cache event。
6. DDR/CPU pooling。
7. communication。
8. replay。

当前文档会随着验收推进逐步追加。每一部分都应明确：

- 当前实现是什么。
- 与真实 vLLM / vLLM-Ascend / Mooncake 的区别。
- 是否影响 V1 正确性。
- 是否需要进入后续阶段。
- 验收结论。

## 1. TTFT 建模验收

### 1.1 vLLM v1 scheduler 不是简单的 prefill-only 公式

vLLM v1 scheduler 的核心不是“这个 request 现在是 prefill 还是 decode”，而是用 `num_computed_tokens` 追赶当前应该被计算到的位置。

可以抽象为：

```text
目标 token 边界 = num_tokens_with_spec
当前已计算位置 = num_computed_tokens
本轮 scheduler 决定推进多少 token
```

这个统一机制同时覆盖：

- prefix cache：命中的 prefix 会直接让 `num_computed_tokens` 往前跳。
- chunked prefill：长 prompt 不一次算完，而是多轮推进 `num_computed_tokens`。
- decode：prompt 完成后，新增输出 token 也继续通过同一套 token 推进逻辑调度。
- speculative decode：`num_tokens_with_spec` 可能包含 speculative token，scheduler 仍然追赶目标 token 边界。

因此，真实 vLLM scheduler 是 token-progress scheduler，不是只按 prefill token 数套公式的组件。真实 TTFT 会受 scheduler token budget、running/waiting 队列、decode 干扰、KV load 状态共同影响。

InferTwin V1 当前只复现 TTFT 相关的 prefill / chunked prefill 主路径，不建模 decode。对应实现是：

- `src/infertwin/scheduler/vllm_like.py`

验收结论：

- V1 对 vLLM scheduler 的定位是正确的。
- 当前 prefill-focused replay 能支撑 TTFT / prefix cache hit 主路径分析。
- Decode / TPOT 不在 V1 范围内，进入 V2 pending。

### 1.2 Step9 已记录 compute wait，为什么 ServingLatencyProfile 仍是 queue + compute + kv_load

这里需要区分两个层次：

1. `ServingLatencyProfile` 估算一次 scheduler iteration 被选中后，这一批 slice 的 service time。
2. Step9 的 `RequestTTFTComposer` 把一条 request 从 arrival 到 finish 的 timeline 汇总成请求级 TTFT。

`ServingLatencyProfile` 的 iteration duration 口径是：

```text
iteration_duration_ms = queue_ms
                      + uncached_prefill_compute_ms
                      + kv_load_ms
```

它描述的是本轮 batch 真正执行的时间。它不包含某个 request 在 waiting 队列中尚未被 scheduler 选中的时间。

Step9 progressive mode 下，请求级 TTFT 口径是：

```text
ttft_ms = compute_wait_ms
        + kv_load_wait_ms
        + uncached_prefill_compute_ms
        + unattributed_ttft_ms
```

其中：

- `compute_wait_ms`：请求已进入 engine，但等待 chunked prefill scheduler 选中的时间。
- `kv_load_wait_ms`：DDR/CPU hit 对应的 deterministic transfer queue wait。
- `uncached_prefill_compute_ms`：未命中 token 的 prefill compute。
- `unattributed_ttft_ms`：replay 粒度残差，不是物理建模结果。

对应实现：

- `src/infertwin/latency/profile.py`
- `src/infertwin/replay/ttft.py`

验收结论：

- `ServingLatencyProfile` 和 Step9 request-level TTFT 的职责边界清晰。
- iteration duration 负责 service time。
- request TTFT composer 负责 request timeline closure。
- 当前口径不冲突，不需要修改 V1 代码。

### 1.3 如何仿真真实系统 compute / transfer overlap

真实系统中，KV load 和 prefill compute 可能发生 overlap。尤其在 vLLM-Ascend / Mooncake / CPU offload 等路径中，KV transfer 可以通过独立 stream、异步 copy 或 layer-wise load 与部分 compute 重叠。

但 V1 默认使用保守语义：

```text
iteration_duration = queue_ms + compute_ms + kv_load_ms
```

即 `overlap_mode=none_v1`。这样做的原因是：

- V1 缺少稳定的真实 overlap profile。
- 不同部署、connector、硬件、stream 实现差异较大。
- 在没有证据时使用 overlap 可能低估 TTFT。
- 当前目标是可解释、确定性的 replay，而不是硬件级真值。

后续如果要建模真实 overlap，建议新增 overlap backend / overlap policy，而不是修改当前默认语义。

一个可行方向是把 request-level KV load 拆成 per-layer KV load：

```text
for each layer i:
    kv_load_i = load bytes/tokens for layer i
    compute_i = compute time for layer i

    transfer_finish_i = transfer_start_i + kv_load_i
    compute_start_i = max(compute_finish_{i-1}, transfer_finish_i)
    compute_finish_i = compute_start_i + compute_i

iteration_duration = compute_finish_last - iteration_start
```

这相当于从 all-KV-load 降级到 per-layer KV-load timeline，让模型可以表达：

- 第 1 层 KV load 完成后，第 1 层 compute 可以开始。
- 后续层 KV load 可以和前面层 compute 重叠。
- 最终 TTFT 由关键路径决定，而不是简单相加。

不能 overlap 的典型情况：

- connector 必须先完成整段 KV load，model runner 才能开始。
- transfer 和 compute 共用瓶颈资源，互相抢带宽。
- HBM target allocation / promotion 尚未完成。
- chunk 太小，compute 时间不足以隐藏 load。
- 真实实现没有独立 stream 或 async load。
- Mooncake / offload path 出现 queue 或 backpressure。

推荐观测方式：

- 打点 KV load start/end 和 prefill kernel start/end。
- 对比真实 TTFT 更接近 `compute + load` 还是 `max(compute, load)`。
- 固定 miss tokens，增加 DDR hit tokens，看 TTFT 增量是否小于理论 KV load 时间。
- 固定 DDR hit tokens，增加 uncached compute，看 load 是否逐渐被隐藏。
- 用 NPU/GPU profiler、vLLM logs、Mooncake TransferEngine metrics 对齐 timeline。

验收结论：

- V1 不建模 compute / transfer overlap 是合理的保守选择。
- per-layer KV load 是后续更真实 overlap 建模的可行路线。
- 该能力应进入 V2 latency refinement，通过新 backend / policy 实现。

### 1.4 calibration backend / overlap backend 的含义

`calibration backend` 解决“参数从哪里来、准不准”。

它可以使用真实线上采样、AIConfigurator、Ramulator2、Mooncake benchmark 或其他外部实验结果，拟合出 InferTwin replay 所需的 latency 参数。

简单例子：

```text
uncached_prefill_compute_ms = a + b * uncached_tokens
kv_load_ms = c + d * kv_load_bytes
```

更复杂的形式：

```text
latency = f(model, hardware, batch_size, chunk_tokens, kv_bytes, deployment)
```

`overlap backend` 解决“compute 和 KV load 如何在 timeline 上组合”。

可能模式：

```text
none_v1:              duration = compute + kv_load
max_compute_load_v1:  duration = max(compute, kv_load)
layer_pipeline_v1:    duration = layer-wise dependency critical path
measured_table_v1:    查表或插值
```

后续更完整的设计可以是：

```text
ServingLatencyProfile
  -> prefill compute backend
  -> kv load backend
  -> overlap policy/backend
  -> request TTFT composer
```

验收结论：

- `calibration backend` 和 `overlap backend` 是两个不同维度。
- calibration 负责参数校准。
- overlap 负责 latency 组合语义。
- V1 当前 `none_v1` 保守可解释，后续可新增 `layer_pipeline_v1` 等模式。

### 1.5 TTFT 建模验收结论

本轮 TTFT 建模验收通过。

结论：

- InferTwin V1 已清晰区分 iteration service time 与 request-level TTFT timeline。
- Step9 已补齐 chunk-level TTFT、compute wait、KV load wait 和 request TTFT closure。
- 当前不建模 Decode / TPOT、真实 compute/load overlap、真实 queue 是明确边界，不影响 V1 当前 replay 语义。
- 若后续要贴近真实 vLLM-Ascend / Mooncake，应新增 calibration backend、overlap backend、per-layer KV load shape 和真实 transfer timeline backend。

### 1.6 TTFT 遗留问题优先级

本小节记录 TTFT 相关遗留问题。它们不阻塞 V1 验收，但进入 V2 或更细粒度仿真前需要逐项处理。

#### P0：文档口径收紧

问题：

- `ServingLatencyProfile` 容易被误解为“被 scheduler 选中后只计算 prefill compute”。
- 实际 V1 语义是：`ServingLatencyProfile` 估算一次 scheduler iteration 的 service time，其中可以包含 `queue_ms`、`uncached_prefill_compute_ms` 和 `kv_load_ms`。
- `kv_load_ms` 不是 scheduler 选中前的 waiting time，而是本轮 iteration 为完成 DDR/CPU hit KV load 所需的服务时间。

影响：

- 不影响代码正确性。
- 影响交接和验收时对 TTFT 字段的理解。

处理方式：

- 在主文档和验收文档中统一使用“iteration service time”描述 `ServingLatencyProfile`。
- 请求级 TTFT 统一描述为：

```text
ttft_ms = compute_wait_ms
        + kv_load_wait_ms
        + kv_load_service_ms
        + uncached_prefill_compute_ms
        + unattributed_ttft_ms
```

其中当前代码中的 `kv_load_ms` 表示 `kv_load_service_ms`。

#### P1：KV load service time 与 KV load wait 字段命名进一步显式化

问题：

- 当前 typed metrics 已有 `kv_load_ms` 和 `kv_load_wait_ms`，但交接时容易把二者混在一起。
- `kv_load_wait_ms` 表示 transfer queue 等待。
- `kv_load_ms` 表示实际 load service time。

影响：

- 不影响 replay 语义。
- 影响 report 解释和后续 calibration。

推荐处理方式：

- 后续新增或别名化字段 `kv_load_service_ms`，保持 `kv_load_ms` 向后兼容。
- 文档中明确：

```text
kv_load_total_ms = kv_load_wait_ms + kv_load_service_ms
```

#### P1：真实 TTFT calibration

问题：

- 当前 fitted TTFT backend 的参数来自手工配置或默认 profile。
- 未必能代表真实模型、真实硬件、真实 batch shape。

影响：

- Prefix cache hit 不受影响。
- TTFT 绝对值可能出现 20%-60% 甚至更大误差。

推荐处理方式：

- 建立 opt-in calibration harness。
- 每个 model / hardware / deployment profile 维护 calibration metadata。
- 支持从真实线上采样、AIConfigurator、Ramulator2、Mooncake benchmark 或公司内 TTFT 仿真器拟合参数。

#### P1：compute / transfer overlap backend

问题：

- V1 默认 `overlap_mode=none_v1`，即 compute 和 KV load service time 相加。
- 真实 vLLM-Ascend / Mooncake / CPU offload 场景中，transfer 可能与部分 compute overlap。

影响：

- DDR/CPU hit 较多时，V1 可能高估 TTFT。
- 高估上限约为可被 compute 隐藏的 transfer 时间。

推荐处理方式：

- 新增 overlap policy / backend，不修改当前默认语义。
- 优先设计：

```text
overlap_mode=max_compute_load_v1
overlap_mode=layer_pipeline_v1
```

- `layer_pipeline_v1` 需要新增 per-layer KV load shape 和 per-layer compute profile。

#### P2：真实 KV transfer timeline backend

问题：

- 当前 `SharedLinkFIFOTransferQueue` 是 deterministic accounting abstraction。
- 它不是真实 Mooncake / TransferEngine / HCCL / RDMA / DMA transfer backend。

影响：

- 高并发 DDR/remote load 下，KV load wait 可能误估。
- 不影响当前 deterministic replay 正确性。

推荐处理方式：

- 新增 KV transfer timeline backend。
- 支持 priority、backpressure、load completion event、bandwidth sharing 和 remote/local tier 差异。
- Mooncake / Ramulator2 作为 adapter 或 calibration source 接入。

#### P2：Decode / TPOT 建模

问题：

- 当前 scheduler replay 聚焦 prefill / TTFT，不建模 Decode / TPOT。
- 真实 vLLM scheduler 中 decode 会与 prefill 共用部分调度和执行资源。

影响：

- 对 prefill 主导、PD 分离场景影响较小。
- 对 PD 混部或 decode-heavy 场景，TTFT 可能低估。

推荐处理方式：

- 仅当出现明确 decode 建模需求，且部署形态为 PD 混部或 decode-heavy 时开启。
- 新增 decode-aware scheduler / replay mode。
- trace 需要增加 output token count。

#### P3：实例入口真实排队

问题：

- 当前 `queue_component` 默认 0。
- V1 不建模 gateway / server / instance admission queue。

影响：

- 不影响 routed trace 下的当前假设。
- 若线上存在明显入口排队，TTFT 会被低估。

推荐处理方式：

- 新增 instance admission queue layer。
- 不要把真实入口排队时间硬塞进 static TTFT backend。
- 与 gateway simulation、tenant fairness、admission policy 一起设计。

## 2. batch 验收

### 2.1 InferTwin V1 当前 batch 语义

InferTwin V1 当前使用 vLLM-like prefill replay：

- running requests 优先。
- waiting requests 按 FCFS admission。
- 受 `max_num_batched_tokens` 约束。
- 受 `max_num_seqs` 约束。
- 支持 chunked prefill token slice。
- DDR/CPU hit 且 miss tokens 为 0 时，可以形成 load-only slice。

当前 batch replay 主要服务于 TTFT / prefix cache hit 的 prefill 主路径。

它不建模：

- preemption。
- LoRA adapter constraint。
- encoder / multimodal budget。
- pipeline parallel timeline。
- decode batch。
- 真实 kernel shape 非线性。

对应实现：

- `src/infertwin/scheduler/vllm_like.py`

### 2.2 preemption / LoRA / encoder / PP 为什么会影响组 batch

#### preemption

真实 vLLM 在 KV block 不足、资源压力过大或调度冲突时，可能暂停 running request，释放资源，之后再恢复或重算。

它会影响组 batch：

- running set 会变化。
- 被 preempt 的请求可能重新进入 waiting。
- 其他请求可能提前 admission。
- 被 preempt 的请求 TTFT 会增加等待或 recompute。
- 真实 physical KV slot pressure 会影响可调度状态。

对 TTFT 的间接影响：

- HBM / KV capacity 充足时：通常 0%-5%。
- KV capacity 紧张或高并发长 prompt：10%-50%。
- 频繁 preemption / recompute：可能 100%+。

InferTwin V1 不建 physical KV slot 和 preemption，因此在极端高压场景可能低估 TTFT。

#### LoRA

真实 vLLM 调度可能受 LoRA adapter 约束，例如同一 batch 中可同时运行的 LoRA 数量有限，或者 adapter load / switch 有额外成本。

它会影响组 batch：

- waiting 队首请求可能因为 LoRA 资源限制无法进入本轮 batch。
- batch 可能按 adapter 被切分，降低合并效率。
- adapter load / switch 可能引入额外等待。

对 TTFT 的间接影响：

- 无 LoRA 或单 LoRA：接近 0。
- 多租户多 LoRA，但 adapter 常驻：5%-20%。
- adapter 频繁切换 / 加载：20%-50%+。

InferTwin V1 不建 LoRA，当前公司主模型验收中不把 LoRA 作为 V1 阻塞项。

#### encoder / multimodal

encoder 主要影响 encoder-decoder 或多模态请求，例如图片、视频、音频输入需要额外 encoder compute 或 feature cache。

它会影响组 batch：

- scheduler 需要同时考虑 decoder token budget 和 encoder budget。
- multimodal feature 处理可能阻塞 request admission。
- 不同模态请求混 batch 时 shape 差异更大。

对 TTFT 的间接影响：

- 纯文本自然语言模型：0。
- 少量多模态输入：10%-30%。
- 大图、多图或视频输入：50%-100%+。

InferTwin V1 当前主要面向 GLM-V5 这类自然语言模型，不把 encoder / multimodal budget 纳入 V1 验收范围。

#### PP / pipeline parallel

pipeline parallel 会把模型层切分到不同 pipeline stage。一次 batch 的 latency 不再只是单阶段 compute，而会出现 pipeline fill、bubble、stage imbalance 和 microbatch 调度。

它会影响组 batch：

- scheduler 输出的 batch shape 会被拆成 pipeline microbatch。
- 前后 stage 负载不均会产生 bubble。
- 新请求进入 batch 的时刻受 pipeline 状态影响。
- first token latency 可能包含 pipeline fill / drain。

对 TTFT 的间接影响：

- PP 稳定、stage 均衡：5%-20%。
- stage imbalance 或小 batch：20%-50%。
- PP + chunked prefill + decode 混部：50%+。

InferTwin V1 不建 PP timeline。若线上 GLM-V5 部署中 PP 对 TTFT 影响明显，需要通过 fitted TTFT profile 进行校准，而不是在 V1 scheduler 中临时补丁。

### 2.3 真实 kernel shape 对 batch latency 的非线性影响

真实 batch latency 不是简单的：

```text
latency = tokens * ms_per_token
```

原因是 kernel shape 会影响真实执行效率。

典型因素：

- GEMM / attention kernel 有 tile size、shape bucket、launch overhead。
- 同样 8192 scheduled tokens，`1 x 8192`、`8 x 1024`、`64 x 128` 的 kernel 形态不同。
- prefill attention 成本受 context length、chunk length、batch size 共同影响。
- chunked prefill 中，一个 chunk 的计算量近似受 `chunk_tokens * context_tokens` 影响。
- paged attention / paged KV cache 会引入 page / block 访问模式差异。
- GPU / NPU kernel 对某些 batch size 或 token shape 更友好。
- TP / CP / PP 会引入通信。
- HBM bandwidth、operator fusion、graph capture 也会导致非线性。

示例：

```text
Batch A:
  1 个请求，每个请求 chunk=8192

Batch B:
  64 个请求，每个请求 chunk=128

总 scheduled tokens 都是 8192，
但真实 latency 可能完全不同。
```

InferTwin V1 的 fitted TTFT backend 更接近 token-linear 或 profile-linear，因此能表达趋势，但不能精确表达 kernel shape 非线性。

粗略影响：

- shape 稳定、profile 校准好：5%-20%。
- chunk size / batch size 波动大：20%-50%。
- PP / TP / CP / 多模态 / 混部场景：50%+。

### 2.4 batch latency 对单条 request TTFT 的综合影响

batch latency 对一条 request 的 TTFT 有三条影响路径。

第一，直接服务时间：

```text
request 被选入某个 iteration
-> 本轮 iteration duration 变长
-> request 的 chunk finish 变晚
-> TTFT 增加
```

如果 request 几乎不排队，batch latency 误差会近似 1:1 传导到该请求 TTFT。

第二，间接等待时间：

```text
前面的 batch duration 变长
-> 当前 request 更晚被 scheduler 选中
-> compute_wait_ms 增加
-> TTFT 增加
```

高并发时，这部分可能比请求自身 compute time 更重要，P90 / P99 TTFT 尤其敏感。

第三，cache visibility timing：

```text
batch / chunk finish 时间变化
-> full block materialization 时间变化
-> 后续请求是否能命中 prefix cache 可能变化
```

Step9 progressive mode 已经支持 chunk finish 后 full block 可见，因此 batch latency 不只影响 TTFT，也可能间接影响后续 prefix hit 的时刻。

综合判断：

| 场景 | batch latency 对单 request TTFT 影响 |
| --- | --- |
| 低并发、无排队、HBM-only | 中等，主要影响自身 compute |
| 低并发、高 prefix hit | 较低，TTFT 可能接近 0 或由 KV load 决定 |
| 高并发、chunked prefill | 高，compute wait 会累积 |
| 长 prompt、多个 chunk | 高，每个 chunk latency 和 chunk 间等待都会进入 TTFT |
| DDR/CPU hit 较多 | 中到高，取决于 KV load 和 transfer queue |
| decode-heavy / PD 混部 | 高，V1 未建模会低估 |
| PP / LoRA / multimodal | 中到高，需要单独校准 |

### 2.5 面向 GLM-V5 的 V1 验收判断

公司当前主要模型是 GLM-V5 这类自然语言模型，V1 验收以 text-only、prefill 主导、full-attention 路径为主。

在这个前提下：

- LoRA 对 V1 主路径影响可以暂不考虑。
- encoder / multimodal budget 对 V1 主路径影响可以暂不考虑。
- PP 对 batch latency 的影响如果存在，优先通过 fitted TTFT profile 校准。
- preemption 只有在真实 physical KV slot pressure 很强时才会显著影响 TTFT；V1 不建 physical slot，因此不作为当前验收阻塞项。
- kernel shape 非线性会影响 TTFT 绝对值，但可以先由 fitted TTFT backend 吸收，不要求 V1 scheduler 直接建模。

因此，综合判断：

```text
对于 GLM-V5 这类自然语言模型，
InferTwin V1 的 batch replay 已足够支撑 prefill 主导场景下的 TTFT 趋势分析。
batch latency 复杂项对 V1 验收不构成阻塞，可以暂时不考虑。
```

### 2.6 batch 验收结论

本轮 batch 验收通过。

结论：

- InferTwin V1 对齐了 vLLM continuous batching / chunked prefill 的核心 token budget 语义。
- V1 可以用于 GLM-V5 等自然语言模型的 prefill 主导 TTFT 趋势分析。
- preemption、LoRA、encoder、PP、真实 kernel shape 非线性不进入 V1 阻塞范围。
- batch latency 复杂项可通过 fitted TTFT profile 暂时吸收。
- 后续如需严格建模真实 batch latency，应进入 V3。
- V3 解决这些问题的前置条件是 TPOT 建模完成。

### 2.7 batch 遗留问题优先级

本小节记录 batch 相关遗留问题。它们不阻塞 V1 验收。根据当前判断，这些问题可以放到 V3 处理，且前置条件是 TPOT 建模完成。

#### V3-P0：TPOT / decode-aware scheduler

问题：

- 真实 vLLM scheduler 用统一 token-progress 机制覆盖 prefill 和 decode。
- 当前 InferTwin V1 聚焦 prefill TTFT，不建模 decode batch 和 TPOT。
- 在没有 TPOT 的情况下，preemption、PP、LoRA、kernel shape 对 batch 的真实影响无法完整闭合。

影响：

- 不影响 GLM-V5 prefill 主导场景下的 V1 验收。
- 会影响 decode-heavy 或 PD 混部场景中的 TTFT / throughput 判断。

处理方式：

- V3 前先完成 TPOT 建模。
- trace 需要增加 output token count。
- 新增 decode-aware scheduler / replay mode。

#### V3-P1：deployment-aware batch latency profile

问题：

- 当前 fitted TTFT backend 不能显式表达真实 kernel shape 非线性。
- 不同 batch size、chunk size、context length、parallel strategy 会导致同样 tokens 下 latency 不同。

影响：

- TTFT 绝对值可能出现 10%-50% 或更大误差。
- 对 V1 当前趋势分析不构成阻塞。

处理方式：

- 在 TPOT 建模完成后，新增 deployment-aware batch latency profile。
- profile 输入至少包括 model、hardware、batch_size、scheduled_tokens、chunk shape、context shape 和 parallel strategy。

#### V3-P2：preemption / physical KV slot pressure

问题：

- V1 cache capacity 表示 prefix cache residency，不表示真实 active KV physical slots。
- 真实 vLLM 在高压下可能 preempt running request。

影响：

- 高并发长 prompt 或 KV capacity 紧张时，V1 可能低估 TTFT。

处理方式：

- 新增 physical slot backend。
- 新增 preemption policy。
- 与 decode-aware scheduler 一起设计。

#### V3-P3：LoRA adapter scheduling constraint

问题：

- 多 LoRA 场景下，adapter 数量、adapter load / switch 会影响 batch admission。

影响：

- 当前 GLM-V5 主路径影响较低。
- 多租户多 LoRA 服务可能低估 TTFT。

处理方式：

- 新增 LoRA-aware scheduler constraint。
- 将 adapter residency / switch cost 放入 deployment profile。

#### V3-P3：encoder / multimodal budget

问题：

- 多模态或 encoder-decoder 模型需要额外 encoder budget。
- 当前 V1 面向自然语言模型，不建模该路径。

影响：

- 对 GLM-V5 text-only 主路径无影响。
- 对图像、视频、多模态输入 TTFT 可能严重低估。

处理方式：

- 在扩展视觉 / 视频模型时新增 encoder budget 和 multimodal request shape。

#### V3-P3：pipeline parallel timeline

问题：

- PP 会引入 pipeline fill、bubble、stage imbalance 和 microbatch 调度。

影响：

- PP 稳定且 profile 校准时可由 fitted backend 部分吸收。
- PP + decode-heavy + chunked prefill 场景需要显式建模。

处理方式：

- 新增 PP timeline profile。
- 与 TPOT / decode-aware scheduler 和 deployment-aware batch profile 一起设计。

## 3. prefix cache hit 验收

### 3.1 InferTwin V1 当前 prefix cache hit 语义

InferTwin V1 的 prefix cache hit 是 hash-only block replay。

当前链路：

```text
request
-> tokenizer / chat template
-> prefix block hash chain
-> HBM prefix lookup
-> 对 HBM miss 的连续 prefix blocks 做 DDR lookup
-> cached-token accounting
-> hbm_hit_tokens / ddr_hit_tokens / miss_tokens
```

核心规则对齐 vLLM：

```text
max_cache_hit_length = prompt_tokens - 1
effective_block_size = runtime_block_size * PCP * DCP
cached_tokens = full matched effective blocks, with optional MTP/EAGLE one-block drop
```

当前统计字段区分：

- `hbm_hit_tokens`
- `ddr_hit_tokens`
- `miss_tokens`
- `kv_hit_tokens`
- `kv_hit_rate`
- `kv_load_tokens`
- `kv_load_bytes`

对应实现：

- `src/infertwin/cache/tiered.py`
- `src/infertwin/cache/hbm_lru.py`
- `src/infertwin/cache/ddr_lru.py`
- `src/infertwin/cache/cached_token_accounting.py`

### 3.2 开启 pooling 后，一条 request 如何 reuse 其他 request 的 KV block

InferTwin V1 中的 pooling 指单实例 DDR/CPU tier pooling，不是 Mooncake 跨实例 remote pooling。

一条 request 能 reuse 其他 request 的 KV block，依赖 block hash 相同。

例如：

```text
Request A:
  prompt blocks = [b0, b1, b2, b3]
  A 计算完成后，b0-b3 被写入 HBM 和 DDR

Request B:
  prompt blocks = [b0, b1, b2, b3, b4]
  lookup 时发现 b0-b3 已存在
  B 可以 reuse A 产生的 b0-b3
```

能够命中的前提：

- model / cache scope 一致。
- tokenizer / chat template 一致。
- block content 一致。
- runtime block size / effective block size accounting 一致。
- block hash chain 一致。

### 3.3 自己的 KV block 如何被其他请求 reuse

一条 request 的 miss blocks 计算完成后，会进入 materialization / store。

当前有两种可见性模式：

```text
legacy mode:
  request finish 后，miss full blocks 才可见

progressive mode:
  scheduled chunk finish 后，newly completed full blocks 可见
```

Step9 推荐 progressive mode。它更接近长 prefill 场景：一个长请求不必等整个 prompt 完成，已经完成的 full blocks 可以先被后续请求看到。

当前 tiered cache materialization 语义：

```text
新计算出的 miss full blocks
-> materialize 到 HBM
-> store 到 DDR
```

因此，Request A 生成的 blocks 后续可以被 Request B 命中。

### 3.4 KV block 被 reuse 后，是否还会从 HBM 放到 DDR

当前 V1 结论：不会因为 reuse 触发 HBM -> DDR offload。

需要区分三种行为：

1. `materialize/store`  
   新计算出的 miss blocks 写入 HBM，同时写入 DDR。

2. `lookup_hit`  
   后续请求命中这些 blocks，只会 touch LRU 状态，不会重新 store。

3. `evict`  
   HBM 超容量时会淘汰 HBM block，但不会在淘汰瞬间把它 offload 到 DDR。

所以 V1 不是：

```text
HBM block 被淘汰
-> 自动搬到 DDR
```

而是：

```text
新 block 生成时
-> 同时写 HBM 和 DDR

后续 HBM 可能淘汰
-> DDR 是否还在，取决于 DDR 自己的容量和 LRU
```

这个设计避免把“生成后写入 DDR”和“HBM eviction offload 到 DDR”混成同一个事件。未来如果要模拟真实 offload，需要新增 offload / promotion / load completion policy。

### 3.5 为什么 DDR prefix cache hit 可能高于 HBM prefix cache hit

DDR prefix cache hit 高于 HBM prefix cache hit 是合理现象，尤其在 HBM 小、DDR 大、长 prompt 场景下很常见。

原因一：HBM 容量更小。

例如：

```text
Request A 生成 100 个 blocks
HBM capacity = 10 blocks
DDR capacity = 200 blocks
```

A 完成后：

```text
HBM 可能只保留最近 10 个 blocks
DDR 可能保留全部 100 个 blocks
```

原因二：prefix hit 必须从第一个 block 连续命中。

假设 A 的 blocks 是：

```text
[b0, b1, b2, ..., b99]
```

HBM 只保留 suffix：

```text
HBM = [b90, ..., b99]
DDR = [b0, ..., b99]
```

当 Request B 也是同样 prefix 时，HBM lookup 从 `b0` 开始：

```text
HBM 查 b0 -> miss
HBM prefix hit = 0
DDR 查 b0-b99 -> hit
DDR prefix hit = 100 blocks
```

即使 HBM 里有 `b90-b99`，它们也不计入 prefix hit，因为 prefix cache hit 要求从 prompt 开头连续命中。

原因三：HBM 和 DDR 是独立 LRU。

HBM LRU 和 DDR LRU 不共享同一个队列。HBM 被频繁挤压时，DDR 可能仍保留较早的 prefix blocks。

因此会出现：

```text
HBM hit tokens 低
DDR hit tokens 高
total cached tokens 高
```

这不是 bug，而是 tiered prefix cache 的自然结果。

### 3.6 HitFloor 外围能力能否开始建设

可以开始，而且应作为当前最高优先级外围能力。

原因：

- HitFloor 本质主要依赖 Prefix Cache Hit。
- 它要回答的是不同 cache 容量、不同请求长度、不同并发窗口下，prefix hit 和 P90 TTFT 的关系。
- TTFT 不需要做到非常精确，只要趋势可解释、误差边界清晰。
- 当前最大难点不是 TTFT 公式，而是如何调节 HBM hit 和 DDR hit 的组成。

因此，HitFloor 不能只看单一 total cache hit rate。

必须输出 tier-aware 指标：

```text
hbm_hit_tokens
hbm_hit_rate
ddr_hit_tokens
ddr_hit_rate
miss_tokens
miss_rate
kv_load_tokens
kv_load_bytes
kv_load_service_ms
kv_load_wait_ms
p90_ttft_ms
```

HBM hit 和 DDR hit 对 TTFT 的影响不同：

```text
HBM hit:
  节省 prefill compute
  基本不增加 KV load latency

DDR hit:
  节省 prefill compute
  但增加 KV load service time
  可能增加 KV load wait time
```

所以不能使用：

```text
TTFT = f(total_kv_hit_rate)
```

更合理的口径是：

```text
TTFT = compute_wait_ms
     + uncached_prefill_compute_ms(miss_tokens, batch_shape)
     + kv_load_wait_ms(ddr load queue)
     + kv_load_service_ms(ddr_hit_tokens or ddr_hit_bytes)
     + unattributed_ttft_ms
```

HitFloor 外围能力的核心难点：

- 如何通过 HBM capacity 控制 HBM hit。
- 如何通过 DDR capacity 控制 DDR hit。
- 如何判断 DDR hit 的收益是否大于 KV load cost。
- 如何在高并发窗口中表达 DDR load queue wait。
- 如何把不同请求长度 bucket 的 hit / miss / TTFT 关系汇总成可解释表格。

### 3.7 DDR hit 是否总是有收益

DDR hit 不一定总是有收益。

收益判断：

```text
saved_compute_ms = recompute_ms(ddr_hit_tokens)
load_cost_ms = kv_load_wait_ms + kv_load_service_ms
```

如果：

```text
saved_compute_ms > load_cost_ms
```

DDR hit 对 TTFT 有收益。

如果：

```text
saved_compute_ms <= load_cost_ms
```

DDR hit 可能不明显，甚至可能让 TTFT 变差。

高并发状态下尤其要小心：

```text
多个请求同时 DDR hit
-> transfer queue wait 上升
-> kv_load_wait_ms 上升
-> DDR hit 收益下降
```

因此 HitFloor 外围能力必须把 DDR hit 和 HBM hit 分开统计，不能只给 total hit rate。

### 3.8 针对不同长度请求计算最高并发下 prefix hit 对应 TTFT

初版可以做成 tier-aware surface / table。

推荐输出维度：

```text
request_length_bucket
peak_concurrency
hbm_capacity_blocks
ddr_capacity_blocks
hbm_hit_rate
ddr_hit_rate
miss_rate
p90_ttft_ms
p90_compute_wait_ms
p90_kv_load_wait_ms
p90_kv_load_service_ms
```

推荐流程：

1. 构造或抽取不同长度 bucket。

```text
8K / 32K / 64K / 128K / 200K
```

2. 构造最高并发 arrival pattern。

```text
同一 peak window 内集中到达
或从真实 trace 中抽取峰值窗口
```

3. 对每个 capacity 组合 replay。

```text
HBM capacity sweep
DDR capacity fixed or sweep
```

4. 输出 tier-aware result。

```text
HBM hit
DDR hit
miss
TTFT components
```

5. 判断 DDR hit 是否真实带来收益。

```text
DDR hit saved compute
vs
DDR load service + wait
```

### 3.9 prefix cache hit 验收结论

本轮 prefix cache hit 验收通过。

结论：

- InferTwin V1 的 HBM first、DDR second tiered lookup 语义清晰。
- HBM / DDR hit tokens 分开统计。
- miss tokens 明确。
- progressive mode 已解决长 prefill 场景下 finish-time materialization 低估复用的问题。
- DDR hit 高于 HBM hit 是合理现象，尤其在 HBM 小、DDR 大、长 prefix 场景下。
- HitFloor 外围能力可以开始建设，且应作为当前最高优先级外围能力。
- HitFloor 主要依赖 prefix cache hit；TTFT 只需要趋势可用，不要求完全精确。
- HitFloor 的核心难点是调节 HBM hit 和 DDR hit，并正确解释 DDR hit 带来的 KV load 时间。

### 3.10 prefix cache hit 遗留问题优先级

本小节记录 prefix cache hit 相关遗留问题。

#### P0：HitFloor tier-aware 外围能力

问题：

- 当前核心仿真器已经具备 HBM / DDR / miss typed metrics。
- 还需要一个面向产品使用的 HitFloor 外围能力，把不同请求长度、最高并发、HBM/DDR capacity 与 P90 TTFT 组织成表。

影响：

- 不影响核心 replay 正确性。
- 直接影响 InferTwin V1 的产品化价值。

处理方式：

- 优先建设 HitFloor 外围能力。
- 必须使用 tier-aware 指标。
- 不允许只用 total hit rate 反推 TTFT。
- 不在 report 层重算 replay 语义。

#### P1：DDR hit promotion 到 HBM

问题：

- 当前 DDR hit 不会自动 promotion 到 HBM。
- 真实系统中，DDR/CPU load 完成后可能把 block 放入 HBM。

影响：

- 可能影响后续请求是否从 HBM 命中。
- 不影响当前 V1 的 tier hit accounting。

处理方式：

- 新增 promotion policy。
- 需要结合 load completion event 和 HBM target allocation。

#### P1：HBM eviction offload 到 DDR

问题：

- 当前 HBM eviction 不触发 offload 到 DDR。
- DDR store 发生在 new block materialization 时。

影响：

- 对当前 V1 语义清晰性有益。
- 但不能表达真实 eviction/offload pipeline。

处理方式：

- 新增 offload policy。
- 不要把 `evict` 和 `store` 混成同一个事件。

#### P1：真实 DDR / Mooncake load completion event

问题：

- 当前 DDR hit 有 KV load latency accounting，但没有真实 load completion event。

影响：

- 不影响 V1 hit accounting。
- 会影响 promotion、async visibility 和真实 transfer timeline。

处理方式：

- 新增 load completion event。
- 与 transfer timeline backend 一起设计。

#### P2：DDR hit 是否值得 load 的 policy

问题：

- 当前只要 DDR hit，就按 profile 计入 KV load。
- 没有判断 `saved_compute_ms > load_cost_ms` 后再决定是否 load。

影响：

- 在 DDR load 很慢或高并发 transfer queue 很长时，可能高估 DDR hit 收益。

处理方式：

- 新增 load-vs-recompute policy。
- policy 输入包括 recompute cost、load service cost、queue wait、SLO。

#### P2：remote pooling / cross-instance hit

问题：

- 当前 pooling 只支持同实例 DDR/CPU tier。
- 不支持跨实例 remote cache hit。

影响：

- 无法评估 Mooncake global store 或多实例池化收益。

处理方式：

- 新增 remote store adapter。
- 新增 cross-instance pooling index。
- 新增 remote KV load profile。

#### P2：compute/load overlap

问题：

- 当前 DDR hit 的 KV load 与 compute 默认不 overlap。

影响：

- DDR hit 较多时可能高估 TTFT。

处理方式：

- 新增 overlap backend。
- 优先考虑 layer-level KV load shape。

#### P3：Hybrid / sparse attention cache manager

问题：

- 当前 prefix cache hit 主要面向 full-attention block 语义。
- Hybrid / sparse attention 可能打破统一 block 假设。

影响：

- 对 GLM-V5 当前主路径不构成阻塞。
- 对未来 Qwen/DeepSeek hybrid 或 sparse 模型可能误估 hit。

处理方式：

- 新增 hybrid / sparse cache manager。
- 新增 cache group schema 和 block conversion policy。

## 4. KV block management 验收

### 4.1 InferTwin V1 当前 KV block management 语义

InferTwin V1 中的 KV block management 主要服务于两个目标：

1. TTFT 趋势分析。
2. KV cache hit / prefix cache hit 统计。

因此，InferTwin 当前管理的是 prefix cache residency metadata，而不是真实 physical KV tensor slot。

当前 block 语义：

```text
PrefixBlock
  -> block_key
  -> block_index
  -> token_count
  -> size_bytes
```

当前 cache resident block 记录的是：

```text
这个 prefix block hash 是否在 HBM / DDR tier 中可被后续请求命中
```

而不是：

```text
这个 block 是否对应真实 GPU/NPU 上的一段 KV tensor slot
```

对应实现：

- `src/infertwin/request/block_hasher.py`
- `src/infertwin/cache/hbm_lru.py`
- `src/infertwin/cache/ddr_lru.py`
- `src/infertwin/cache/tiered.py`

### 4.2 与真实 vLLM 对齐的部分

InferTwin V1 与真实 vLLM / vLLM-Ascend 比较对齐的是 prefix cache 相关语义：

- block 级 prefix cache。
- 完整 block 才能命中。
- prefix 必须连续命中。
- `prompt_tokens - 1`。
- runtime / effective block size accounting。
- CP / DCP / PCP 对 effective block size 的影响。
- MTP / EAGLE one-block drop。
- HBM / DDR tier hit accounting。
- LRU residency / eviction abstraction。
- progressive full-block materialization。

这些属于 cached-token accounting / prefix hit semantics，对 V1 的主要目标很关键。

### 4.3 与真实 vLLM 不完全一致的部分

真实 vLLM 的 KV block management 不只是“这个 block 是否在 cache 中”。它还负责在线 serving 中的 physical KV lifecycle。

真实系统通常包含：

- physical KV slot allocation。
- active request block table。
- `req_to_blocks`。
- block refcount。
- block pinned / protected state。
- block touch / free。
- free queue。
- cached block reuse 前的 refcount 保护。
- block eviction before reusing free block。
- request finish 后 reverse-order free。
- insufficient capacity 时 preemption。
- speculative / decode / encoder 相关 block 管理。
- prefix cached block 与正在运行 request block 的交互。
- KVConnector / external KV transfer 下的 delay cache blocks。
- GPU block id / CPU block id / remote block metadata。

InferTwin V1 不建这些内容。

因此，InferTwin 中的 block 更像：

```text
prefix cache residency record
```

真实 vLLM 中的 block 更像：

```text
可被 model runner 直接索引的 physical KV slot + lifecycle state
```

这就是为什么 KV block management 的对齐程度不是“完全一致”，而是“中等”。

### 4.4 为什么不只是“物理存储环节”的差异

如果真实系统和 InferTwin 只差在“是否存 KV tensor”，那么可以认为管理逻辑基本一致。

但真实 physical KV lifecycle 会反过来影响 scheduler 和 cache 可见性。

典型例子：

#### refcount / pinned

真实 vLLM 中，如果 block 还被 running request 使用，就不能随意淘汰。

InferTwin V1 没有 active block refcount，只模拟 prefix cache residency，因此不会因为 running request 保护某些 block。

#### physical slot pressure

真实服务里，如果 active requests 占满 KV slots，scheduler 可能 admission 失败或 preempt。

InferTwin V1 的 capacity 只表示 prefix cache resident blocks，不表示 active sequence KV capacity，因此不会触发真实 OOM / preemption。

#### free queue / eviction timing

真实 vLLM 是在需要新 physical block 时，从 free queue 获取 block；如果这个 free block 仍带 hash，会先 eviction，再复用 slot。

InferTwin V1 是 materialize / store 时发现 resident cache 超容量，就直接按 LRU evict metadata。

两者 eviction 时机和语义不同。

#### request finish free

真实 vLLM 中，request finish 后会 free request blocks，改变 block refcount 和 free queue。

InferTwin V1 中，request finish 或 chunk finish 后是 materialize miss blocks，让它们成为 prefix cache 候选，不模拟 active request block free。

#### external KV transfer

真实 vLLM / vLLM-Ascend 有 `delay_cache_blocks`、`WAITING_FOR_REMOTE_KVS`、CPU/GPU block id、load completion 等状态。

InferTwin V1 当前只有 DDR hit accounting 和 KV load latency，没有真实 load completion 后的 HBM target allocation。

### 4.5 为什么 V1 可以先忽略这些差异

InferTwin V1 的核心目标不是评估真实 KV memory pressure，而是：

```text
TTFT trend + KV cache hit / prefix cache hit accounting
```

因此，以下问题可以先忽略：

- 真实 KV memory pressure。
- preemption。
- active request block lifecycle。
- pinned / refcount。
- physical slot allocation。
- request finish free。
- promotion。
- offload。
- decode KV growth。

这些能力虽然重要，但主要影响更真实的在线 serving memory manager 仿真，不是 V1 HitFloor / capacity sweep / prefix cache hit trend 的必要前提。

在当前产品边界下：

- prefix cache hit 统计仍然有效。
- HBM / DDR hit tokens 仍然可解释。
- miss tokens 仍然可用于 TTFT trend。
- capacity sweep 仍然可以回答“不同 prefix cache residency capacity 下的 hit 和 TTFT 趋势”。

### 4.6 KV block management 对齐程度判断

对齐程度：中等。

原因：

高对齐部分：

```text
prefix cache hit usage accounting
block hash replay
full-block contiguous prefix hit
tier-aware hit/miss stats
LRU residency abstraction
progressive full-block visibility
```

未对齐部分：

```text
physical slot allocation
active KV lifecycle
refcount / pinned
free queue
preemption
real block table
load completion / promotion
offload on eviction
decode KV growth
external KV connector metadata
```

因此，不能认为 InferTwin 和真实推理服务的 KV block management 除物理存储外完全一致。

更准确的判断是：

```text
InferTwin V1 与真实 vLLM 在 prefix cache residency 和 cached-token accounting 上对齐；
但没有完整模拟 physical KV slot lifecycle。
```

### 4.7 KV block management 验收结论

本轮 KV block management 验收通过。

结论：

- InferTwin V1 当前 KV block management 足够支撑 TTFT trend 和 KV cache hit / prefix cache hit 分析。
- V1 不评估真实 KV memory pressure、preemption、active request block lifecycle、promotion/offload 和 decode KV growth。
- 这些差异不影响 V1 面向 HitFloor / capacity sweep / prefix cache hit trend 的正确性。
- 如果后续要研究真实 serving memory manager，需要新增 physical KV block backend，而不是修改当前 prefix cache residency 语义。

### 4.8 KV block management 遗留问题优先级

本小节记录 KV block management 相关遗留问题。它们不阻塞 V1 验收。

#### V2-P1：promotion / load completion 后 HBM target allocation

问题：

- 当前 DDR hit 不会在 load completion 后 promotion 到 HBM。
- 真实系统中，KV load 完成后可能需要分配 HBM target blocks。

影响：

- 不影响 V1 tier hit accounting。
- 会影响后续请求是否从 HBM 命中。

处理方式：

- 新增 load completion event。
- 新增 promotion policy。
- 新增 HBM target allocation policy。

#### V2-P1：HBM eviction offload 到 DDR

问题：

- 当前 HBM eviction 不触发 offload 到 DDR。
- DDR store 只发生在 new block materialization 时。

影响：

- V1 语义更清晰。
- 无法表达真实 eviction/offload pipeline。
- 如果目标部署依赖 HBM eviction 后 offload 到 DDR 来形成 DDR residency，会直接影响后续 DDR lookup 和 DDR load。

处理方式：

- 新增 offload policy。
- 保持 `evict` 和 `store` 为不同事件。

#### V3-P1：physical KV slot backend

问题：

- 当前 capacity 表示 prefix cache residency capacity，不表示真实 active sequence KV slot。

影响：

- 无法评估真实 memory pressure、OOM、preemption。
- 不影响 V1 HitFloor / prefix hit trend。

处理方式：

- 新增 physical KV slot backend。
- 显式建模 active block table、free queue、refcount 和 request finish free。

#### V3-P2：preemption policy

问题：

- 当前 InferTwin V1 不建模 preemption。

影响：

- 高压容量或 decode-heavy 场景中可能低估 TTFT。

处理方式：

- 在 physical KV slot backend 和 TPOT 建模完成后新增 preemption policy。

#### V3-P3：decode KV growth

问题：

- 当前 replay 聚焦 prefill TTFT，不建模 decode KV growth。

影响：

- 对 GLM-V5 prefill 主导 V1 验收不构成阻塞。
- 对 decode-heavy / PD 混部 memory pressure 判断不足。

处理方式：

- 等 TPOT / decode-aware scheduler 完成后再设计。

## 5. cache event / KV block allocation 验收

### 5.1 InferTwin typed event 的定位

InferTwin V1 当前的 `CacheEvent` 是 typed simulation event，不是 vLLM / Mooncake 原生 telemetry。

InferTwin typed event 面向离线 replay，回答的是：

```text
某个 block 在仿真时刻是否被 lookup / 写入 / 淘汰？
发生在哪个 tier？
对 hit/miss/TTFT 统计有什么影响？
```

当前事件类型：

```text
lookup_hit
lookup_miss
materialize
store
evict
```

真实 vLLM / Mooncake 原生 telemetry 更偏向 runtime physical block manager 和 transfer system，回答的是：

```text
真实 runtime 中 physical KV block slot 如何分配、引用、释放、复用、传输？
```

因此，InferTwin event 与 vLLM / Mooncake event 处于不同抽象层级。

对应实现：

- `src/infertwin/cache/events.py`
- `src/infertwin/cache/event_sink.py`
- `src/infertwin/report/cache_event_writer.py`

### 5.2 为什么 cache event 没有完全对齐 vLLM / Mooncake

当前 InferTwin V1 对齐的是仿真因果信号，不是完整 runtime block manager telemetry。

| 真实机制 | vLLM / Mooncake 含义 | InferTwin V1 当前处理 | 为什么没完全对齐 |
| --- | --- | --- | --- |
| free queue | 管理可复用 physical KV block slot | 无 free queue，只有 resident metadata + LRU | V1 不建 physical slot |
| block eviction before reusing free block | 复用某个 free slot 前，如果它带 cached hash，先移除旧 cache block | capacity 满时直接按 LRU evict metadata | V1 没有 slot reuse 过程 |
| `BlockStored` / `BlockRemoved` | vLLM KV event，面向真实 GPU KV cache 事件 | `materialize` / `store` / `evict` | 语义相近，但 schema 和触发时机不同 |
| request finish reverse-order free | 请求结束后释放 request 持有的 block，更新 refcount/free queue | request finish 或 chunk finish 后 materialize miss blocks | V1 没有 active request block ownership |
| cached block touch | 命中 cached block 后 touch，可能更新 LRU / refcount | lookup hit 时更新 LRU access time | 只对齐 residency touch，不对齐 refcount |
| `ref_cnt` | block 被 running request 使用时不能淘汰 | 无 refcount / pinned | V1 不建 active KV lifecycle |

所以，当前不是事件设计错误，而是 V1 的抽象层级停留在 prefix cache residency。

### 5.3 运行中 KV block 占用 HBM 的影响

真实推理服务中，HBM KV cache 空间不是只给 prefix cache 使用。

它通常同时被两类 block 占用：

```text
active KV blocks:
  正在运行的 request 生成/使用的 KV

cached prefix blocks:
  已完成、可被后续 request reuse 的 KV
```

因此真实 HBM 可用于 prefix cache 的空间更接近：

```text
available_prefix_cache_blocks
= total_hbm_kv_blocks
- active_running_kv_blocks
- reserved_blocks
```

高并发时：

```text
active_running_kv_blocks 增加
-> 可保留的 HBM prefix cache blocks 减少
-> HBM prefix hit 下降
-> 更多 hit 可能落到 DDR/CPU
```

低并发时：

```text
active_running_kv_blocks 减少
-> HBM 可以保留更多 prefix blocks
-> HBM prefix hit 更高
-> DDR hit 占比下降
```

因此，用户观测到的现象是合理的：

```text
高负载压力下，KV cache hit 更多发生在 DDR；
低负载压力下，KV cache hit 更多发生在 HBM。
```

### 5.4 InferTwin V1 当前偏差

InferTwin V1 当前把 `hbm_capacity_blocks` 理解为 prefix cache residency capacity。

它没有把运行中 request 的 active KV blocks 从 HBM capacity 中扣掉。

因此在高并发场景下，V1 可能：

```text
高估 HBM prefix hit
低估 DDR/CPU hit 占比
低估 active KV pressure 导致的 eviction
低估高并发下的 TTFT
```

这个偏差不影响 V1 当前 replay 语义，但会影响后续更精确的 HitFloor 外围能力，尤其是希望解释不同负载压力下 HBM hit / DDR hit 分布迁移时。

### 5.5 后续建议建模方式

后续建议新增独立 backend，而不是直接修改当前 HBM LRU：

```text
ActiveKVOccupancyModel
或 PhysicalKVBlockBackend
```

它负责：

```text
total_hbm_kv_blocks
active_running_blocks
cached_resident_blocks
free_blocks
refcount / pinned
request block table
finish free
preemption trigger
```

然后让 HBM prefix cache capacity 变成动态值：

```text
effective_hbm_prefix_capacity(t)
= total_hbm_kv_blocks
- active_running_blocks(t)
- reserved_blocks
```

这样可以解释：

- 高并发为什么 HBM hit 下降。
- 低并发为什么 HBM hit 上升。
- active request 为什么会挤压 prefix cache。
- DDR hit 为什么在高压下变多。
- HBM eviction 是否应该触发 offload。
- request finish 后 block 如何从 active 转为 cached。

### 5.6 cache event / KV block allocation 验收结论

本轮 cache event / KV block allocation 验收通过。

结论：

- InferTwin V1 typed event 是仿真因果信号，不是 vLLM / Mooncake 原生 telemetry。
- 当前事件足够支撑 V1 的 prefix cache hit、tier-aware metrics、HitFloor 外围能力和 replay debug。
- V1 不建 free queue、reverse-order free、ref_cnt、physical slot reuse 和 request active block ownership，是明确边界。
- 运行中请求生成的 active KV 会占用 HBM，这一点对 HBM hit / DDR hit 分布很重要。
- 当前 V1 可先忽略 active KV occupancy，但后续为了更准确解释高并发下 DDR hit 上升，应优先补充 active KV occupancy-aware HBM capacity。

### 5.7 cache event / KV block allocation 遗留问题优先级

本小节记录 cache event / KV block allocation 相关遗留问题。它们不阻塞 V1 验收。

#### V2-P0：active KV occupancy-aware HBM capacity

问题：

- 当前 `hbm_capacity_blocks` 表示静态 prefix cache residency capacity。
- 真实服务中 running requests 的 active KV blocks 会占用 HBM。
- 高并发下 active KV occupancy 会挤压 prefix cache，导致 HBM hit 下降、DDR hit 上升。

影响：

- 对 V1 当前 replay 语义不构成错误。
- 对 HitFloor 外围能力非常重要，因为 HitFloor 的核心难点是调节 HBM hit 和 DDR hit。

处理方式：

- 新增 `ActiveKVOccupancyModel` 或 `PhysicalKVBlockBackend`。
- 计算动态 `effective_hbm_prefix_capacity(t)`。
- 保持当前 HBM LRU 作为 prefix residency policy，不直接混入 active KV lifecycle。

#### V2-P1：active block lifecycle event

问题：

- 当前 cache events 只覆盖 prefix cache residency。
- 不记录 active block allocate / free / pin / unpin。

影响：

- 无法解释 running request 对 HBM prefix cache 的挤压。
- 不影响 V1 prefix hit accounting。

处理方式：

- 新增 active block event schema。
- 与 prefix cache event 分开，避免混淆 `materialize/store/evict`。

#### V2-P1：request finish free / reverse-order free

问题：

- 真实 vLLM request finish 后会 reverse-order free request blocks。
- 当前 InferTwin 在 request finish 或 chunk finish 后 materialize prefix blocks，不建 active block free。

影响：

- 无法表达 active KV 空间释放后 HBM prefix capacity 回升。

处理方式：

- 在 active KV occupancy backend 中建模 request active blocks。
- request finish 后释放 active occupancy，再按 policy 决定是否转为 cached prefix residency。

#### V2-P2：vLLM-compatible event exporter

问题：

- InferTwin typed event 与 vLLM `BlockStored` / `BlockRemoved` schema 不完全一致。

影响：

- 不影响核心 replay。
- 影响和 vLLM 原生 telemetry 做 side-by-side 对比。

处理方式：

- 新增 exporter / adapter，将 InferTwin typed events 映射成 vLLM-like event view。
- 不要修改核心 event schema 来迎合某个外部系统。

#### V3-P1：free queue / block reuse before eviction

问题：

- 当前 InferTwin eviction 是 residency capacity 满时直接 LRU evict metadata。
- 真实 vLLM 是从 free queue 取 physical slot，复用 slot 前可能先移除旧 cached hash。

影响：

- 不影响 V1 HitFloor / prefix hit trend。
- 会影响真实 memory manager 仿真。

处理方式：

- 进入 physical KV block backend。
- 显式建模 free queue、slot reuse 和 block removal timing。

#### V3-P2：ref_cnt / pinned block

问题：

- 当前 InferTwin 不建 refcount / pinned。
- 真实 running request 持有的 block 不能随意淘汰。

影响：

- 高压场景下可能影响 HBM hit / eviction。

处理方式：

- 与 active KV lifecycle、physical slot backend 一起设计。

## 6. Replay 链路验收

### 6.1 当前 replay 链路

InferTwin V1 当前 replay 主链路为：

```text
trace row
-> trace schema guard
-> request build
-> tokenizer / chat template
-> prefix block hash
-> instance grouping or streaming shard
-> per-instance replay
-> pending arrival
-> waiting lookup frontier
-> scheduler schedule
-> latency estimate / transfer queue accounting
-> running state update
-> chunk finish / request finish
-> materialization / store / eviction
-> typed request metrics / iteration metrics / report
```

当前 replay 能力包括：

- fixed-routing 多实例隔离 replay。
- true streaming 大 trace 主路径。
- vLLM-like prefill / chunked prefill scheduler replay。
- HBM / DDR tier-aware prefix cache lookup。
- KV load latency accounting。
- progressive full-block materialization。
- typed metrics 输出。

### 6.2 验收结论

本轮 Replay 链路验收通过。

结论：

- InferTwin V1 的 replay 链路清晰、可解释、可继续扩展。
- 当前 replay 能力能够支撑 V1 的 TTFT trend、prefix cache hit、tier-aware metrics 和 HitFloor 外围能力。
- 后续新增 gateway、instance queue、remote pooling、physical KV backend 或 decode-aware replay 时，应通过新 layer / mode / backend / policy 接入，不应破坏当前 V1 replay 语义。

## 7. Cache Lookup / Store / Eviction 信号验收

### 7.1 当前信号

InferTwin V1 当前 cache 信号包括：

```text
lookup_hit
lookup_miss
materialize
store
evict
```

当前信号用于描述 prefix cache residency replay 中的关键因果事件：

- `lookup_hit`：某个 prefix block 在对应 tier 命中。
- `lookup_miss`：某个 prefix block 在对应 tier 未命中。
- `materialize`：新计算出的 full block 写入 HBM。
- `store`：新计算出的 full block 写入 DDR/CPU tier。
- `evict`：resident block 因容量或 policy 被淘汰。

信号字段包含：

- timestamp。
- instance_uuid。
- request_id。
- block_key。
- block_index。
- token_count。
- cache_tier。
- source_tier / target_tier。
- load_tokens / store_tokens。
- HBM / DDR used blocks。
- HBM / DDR capacity blocks。
- eviction_policy。
- reason。

### 7.2 验收结论

本轮 Cache Lookup / Store / Eviction 信号验收通过。

结论：

- 当前信号足够支撑 V1 prefix cache hit、tier-aware metrics、HitFloor 外围能力和 replay debug。
- 当前信号是 InferTwin typed simulation event，不是 vLLM / Mooncake 原生 telemetry。
- V1 不要求完全复刻 vLLM `BlockStored` / `BlockRemoved` 或 Mooncake TransferEngine telemetry。
- 若后续需要与真实 telemetry 对比，应新增 exporter / adapter，而不是修改核心 event schema。

## 8. DDR/CPU pooling 验收

### 8.1 InferTwin V1 当前 DDR/CPU pooling 语义

InferTwin V1 当前支持单实例 DDR/CPU pooling tier。

当前 lookup 顺序：

```text
HBM prefix lookup
-> 对 HBM miss 的连续 prefix blocks 做 DDR lookup
-> 得到 hbm_hit_tokens / ddr_hit_tokens / miss_tokens
```

当前 store 语义是 write-through on materialization：

```text
新 block 生成并可见
-> materialize 到 HBM
-> store 到 DDR
```

当前 HBM eviction 语义是：

```text
HBM evict
-> 只删除 HBM resident metadata
-> 不触发新的 DDR store/offload
```

因此，在 InferTwin V1 中：

```text
DDR hit 是否成立
= block 是否已经在 DDR resident set 中
```

而不是：

```text
DDR hit 是否成立
= block 是否刚刚从 HBM eviction offload 到 DDR
```

### 8.2 为什么 DDR/CPU pooling 对齐程度是中等

DDR/CPU pooling 给“中等”对齐程度，是因为 V1 对齐的是 tier-aware hit accounting，而不是完整真实 pooling runtime。

已对齐或接近对齐的部分：

- HBM first，DDR second。
- 只对 HBM miss 的连续 prefix blocks 查 DDR。
- HBM hit 和 DDR hit 分开统计。
- DDR hit tokens 进入 `ddr_hit_tokens`。
- DDR hit bytes 进入 `kv_load_bytes`。
- DDR hit 可产生 `kv_load_ms`。
- 多请求 DDR load 可以进入 deterministic shared-link FIFO wait。
- DDR tier 有独立 LRU。
- DDR store / lookup / evict 有 typed event。
- progressive mode 下，chunk finish 后新 full blocks 可写入 HBM / DDR。

未完全对齐的部分：

- 真实 async store / async load。
- load completion event。
- DDR hit 后 promotion 到 HBM。
- HBM eviction offload 到 DDR。
- running request active KV occupancy。
- TransferEngine backpressure。
- remote pooling / cross-instance hit。
- failure / timeout / fallback。
- layer-wise load / store。

因此：

```text
hit accounting 层面：中高
runtime pooling lifecycle 层面：中低
整体对齐程度：中等
```

### 8.3 HBM eviction offload 到 DDR 是否影响 DDR load

会影响，但取决于 pooling mode。

#### V1 当前 write-through mode

InferTwin V1 当前是：

```text
新 block 生成时
-> 同时写 HBM 和 DDR

HBM 后续 evict
-> 只从 HBM resident set 删除
-> 不触发新的 DDR store
```

在这个模式下：

```text
HBM eviction 不影响 DDR load 的可用性
```

因为 DDR 是否能 hit，只取决于：

- block 是否曾经 store 到 DDR。
- block 是否还没被 DDR LRU 淘汰。

#### 未来 hbm_evict_offload_ddr mode

如果真实系统采用 eviction-offload 语义：

```text
HBM evict
-> copy / offload KV block 到 DDR
-> HBM slot 释放
```

那么 HBM eviction 会直接影响后续 DDR load。

链路变成：

```text
Request A 的 block 在 HBM
-> HBM 空间紧张，被 evict
-> block 被写入 DDR
-> Request B 后续 prefix lookup 在 HBM miss
-> DDR hit
-> 从 DDR load 回 HBM
```

此时 DDR load 的前提是：

```text
evict/offload 已经完成，并且 DDR resident set 中可见
```

如果 offload 是异步的，还会出现中间状态：

```text
HBM 已决定 evict
DDR store/offload 尚未完成
```

这时后续请求可能：

- 等待 offload 完成后从 DDR load。
- 认为 DDR 尚不可见，直接 recompute。
- 取消 offload，保留或复用 HBM block。
- 进入 pending / loading 状态。

因此，eviction-offload mode 会影响：

- DDR resident set。
- DDR block 可见时间。
- 后续请求是 DDR hit 还是 miss。
- DDR load service time。
- DDR load wait time。
- HBM target allocation。
- HitFloor 中 HBM hit / DDR hit 的分布。

### 8.4 对 prefix cache hit 的影响

对 `total hit-rate`：

- 如果同实例复用、DDR 足够大、store 可见性不敏感，误差通常较小。
- 如果短间隔请求很多、async store/offload completion 重要，DDR hit 可能被高估或低估。
- 如果真实系统有 cross-instance pooling，V1 会低估 remote hit。

粗略判断：

| 场景 | total hit-rate 影响 |
| --- | --- |
| 同实例复用、DDR write-through、间隔较长 | 0-10pp |
| async store/offload 可见性重要 | 10-30pp |
| remote pooling 显著 | 低估 10-60pp+ |

对 `tier hit distribution`：

```text
HBM hit / DDR hit 的比例比 total hit-rate 更敏感。
```

尤其在下面场景中：

- HBM 小、DDR 大。
- 高并发 active KV occupancy 挤压 HBM。
- eviction-offload 决定 DDR residency。
- promotion 决定后续是否从 HBM 命中。

粗略判断：

| 场景 | HBM/DDR 分布影响 |
| --- | --- |
| 低压 | 0-10pp |
| 高并发 active KV pressure | 10-50pp |
| promotion/offload 很重要 | 10-50pp |

### 8.5 对 TTFT 的影响

DDR hit 不是免费 hit。

HBM hit：

```text
节省 prefill compute
基本不增加 KV load latency
```

DDR hit：

```text
节省 prefill compute
但增加 kv_load_service_ms
可能增加 kv_load_wait_ms
```

所以 DDR-heavy 场景下，TTFT 取决于：

```text
saved_compute_ms = recompute_ms(ddr_hit_tokens)
load_cost_ms = kv_load_wait_ms + kv_load_service_ms
```

如果：

```text
saved_compute_ms > load_cost_ms
```

DDR hit 有收益。

如果：

```text
saved_compute_ms <= load_cost_ms
```

DDR hit 可能收益不明显，甚至让 TTFT 变差。

因此，即使 total hit rate 相同：

```text
80% HBM hit + 10% DDR hit
```

和：

```text
10% HBM hit + 80% DDR hit
```

TTFT 也可能完全不同。

粗略判断：

| 场景 | TTFT 影响 |
| --- | --- |
| HBM hit 为主 | 0%-10% |
| DDR hit 少量、本地 load profile 已校准 | 10%-30% |
| DDR-heavy、高并发 load | 30%-100%+ |
| remote pooling / fallback | 30%-100%+ |
| active KV pressure 导致 HBM hit 转 DDR hit | TTFT 可能明显增加 |

### 8.6 HitFloor 开发前的仿真器优先级

HitFloor 外围能力的核心难点是调节 HBM hit 和 DDR hit，并解释 DDR hit 产生的 KV load 时间。

在开发 HitFloor 前，建议优先级如下。

#### P0：pooling mode 显式化与 ConfigGuard

问题：

- 当前 V1 隐含使用 `write_through_on_materialization`。
- 如果目标部署是 `hbm_evict_offload_ddr`，DDR resident set 和 DDR load 可见性会不同。

处理方式：

- 在 model / runtime profile 中显式声明 pooling mode。
- V1 当前支持：

```text
pooling_mode=write_through_on_materialization
```

- 对未实现模式 fail-fast，例如：

```text
pooling_mode=hbm_evict_offload_ddr
```

直到对应 backend 实现。

#### P0：active KV occupancy-aware HBM capacity

问题：

- 高并发下 running requests 的 active KV 会占 HBM。
- 这会直接影响 HBM hit / DDR hit 分布。

处理方式：

- 新增 `ActiveKVOccupancyModel`。
- 计算：

```text
effective_hbm_prefix_capacity(t)
= total_hbm_kv_blocks
- active_running_blocks(t)
- reserved_blocks
```

#### P0：kv_load_service_ms / kv_load_wait_ms 指标显式化

问题：

- HitFloor 必须解释 DDR hit 为什么影响 TTFT。
- 需要区分 load service time 和 load queue wait。

处理方式：

- 文档和 report 中明确：

```text
kv_load_total_ms = kv_load_service_ms + kv_load_wait_ms
```

- 保持 `kv_load_ms` 向后兼容，但语义上解释为 service time。

#### P0：DDR KV load profile guard / calibration knobs

问题：

- DDR-heavy 场景下 TTFT 主要受 KV load 参数影响。

处理方式：

- 在 config/profile 中显式声明 DDR load profile。
- 未配置时使用 conservative default，并在 report 中标注。
- 后续接入校准结果。

#### P1：hbm_evict_offload_ddr backend

问题：

- 如果真实部署依赖 HBM eviction 后 offload 到 DDR，V1 当前 write-through mode 不足以解释 DDR resident set。

处理方式：

- 新增 mode：

```text
tiered_cache_mode=hbm_evict_offload_ddr
```

- 新增 offload event。
- 新增 offload completion timing。
- DDR 仅在 offload completion 后可见。

说明：

- 如果当前 HitFloor 初版接受 V1 write-through mode，则该项不阻塞原型。
- 如果要对齐真实 eviction-offload 部署，则该项应提升为 P0。

#### P1：DDR load completion / promotion policy

问题：

- DDR hit load 完成后是否进入 HBM，会影响后续请求是 HBM hit 还是 DDR hit。

处理方式：

- 新增 load completion event。
- 新增 promotion policy。
- 新增 HBM target allocation policy。

#### P1：load-vs-recompute policy

问题：

- DDR hit 不一定值得 load。

处理方式：

- 增加判断：

```text
load if saved_compute_ms > load_cost_ms
else recompute
```

#### P2：Mooncake / remote pooling adapter

问题：

- V1 只支持同实例 DDR/CPU pooling。
- 真实 Mooncake 可支持跨实例 / remote pooling。

处理方式：

- 新增 remote store adapter。
- 新增 remote KV load profile。
- 新增 cross-instance pooling index。

#### P2：layer-wise async load / compute-load overlap

问题：

- 当前 KV load 是 request / iteration aggregate。
- 真实系统可能 layer-wise load 并与 compute overlap。

处理方式：

- 新增 layer-level KV load shape。
- 新增 overlap backend。

### 8.7 DDR/CPU pooling 验收结论

本轮 DDR/CPU pooling 验收通过。

结论：

- InferTwin V1 对齐的是同实例 tier-aware prefix hit accounting，不是完整 vLLM-Ascend / Mooncake pooling runtime。
- V1 当前 `write_through_on_materialization` 语义清晰，足够支撑 HitFloor 原型和 V1 capacity sweep。
- HBM eviction offload 到 DDR 会影响后续 DDR load，但该语义当前未实现。
- 如果目标部署依赖 eviction-offload 形成 DDR residency，则 `hbm_evict_offload_ddr` backend 应在 HitFloor 正式产品化前优先实现。
- HitFloor 开发前最关键的仿真器前置项是：pooling mode 显式化、active KV occupancy-aware HBM capacity、KV load service/wait 指标显式化、DDR load profile guard。

## 9. communication 验收

### 9.1 communication 对齐程度判断

InferTwin V1 的 communication 对齐程度为低到中。

原因是：

```text
InferTwin V1 对齐的是 DDR/CPU hit 对 TTFT 的 replay-facing 影响；
没有对齐真实 vLLM-Ascend / Mooncake 通信 runtime。
```

V1 已经具备：

- `kv_load_tokens`
- `kv_load_bytes`
- `kv_load_ms`
- `kv_load_wait_ms`
- token-linear / byte-linear KV load profile
- deterministic shared-link FIFO transfer queue
- instance-local transfer queue
- load-only slice
- HBM hit 和 DDR hit 区分
- DDR hit 对 TTFT 的影响

它能表达：

```text
DDR hit 不是免费 hit
DDR hit 需要 load
多个 DDR load 会排队
DDR-heavy 场景 TTFT 会变差
```

这对 HitFloor 初版是有价值的。

但 V1 尚未建模真实通信系统本身。

### 9.2 未完全对齐的通信链路

真实 vLLM-Ascend / Mooncake 通信链路可能包含：

- CPU DDR -> HBM。
- remote DRAM -> HBM。
- SSD -> CPU -> HBM。
- Mooncake memory replica -> GPU/NPU buffer。
- local disk fallback。
- HCCL all-to-all。
- RDMA。
- DMA。
- CPU copy。
- NPU/GPU stream copy。

Mooncake / TransferEngine 还可能包含：

- metadata query。
- replica selection。
- segment open。
- memory registration。
- batch submit。
- status polling。
- timeout。
- partial result。
- admission queue。
- failure / retry / fallback。

InferTwin V1 当前没有建这些真实 runtime 细节。

当前简化为：

```text
kv_load_ms = f(tokens or bytes)
ready_time -> start_time = max(ready_time, next_available)
finish_time = start_time + transfer_ms
```

这能表达共享链路排队，但不能表达真实 TransferEngine 行为。

### 9.3 对 prefix cache hit 的影响

communication 本身不直接改变 block hash 是否相同，但会通过可见性和完成时刻影响 prefix hit。

例如：

```text
DDR store/offload 尚未完成
-> DDR 中还不可见
-> 后续请求不能 DDR hit
```

或者：

```text
DDR load 完成后 promotion 到 HBM
-> 后续请求可能 HBM hit
```

因此：

- 对 total hit-rate：多数同实例场景影响中等偏低。
- 对 HBM vs DDR tier 分布：影响可能中等到高。
- 对短间隔、高并发、DDR-heavy trace：影响更大。

### 9.4 对 TTFT 的影响

communication 对 TTFT 的影响很直接：

```text
TTFT = compute_wait_ms
     + prefill_compute_ms
     + kv_load_service_ms
     + kv_load_wait_ms
     + residual
```

如果 DDR hit 很少，communication 影响较小。

如果 DDR hit 很多，communication 影响较大。

粗略判断：

| 场景 | communication 对 TTFT 影响 |
| --- | --- |
| HBM hit 为主 | 低，0%-10% |
| 少量 DDR hit，本地 DDR profile 已校准 | 中，10%-30% |
| DDR-heavy，高并发 load | 高，30%-100%+ |
| remote pooling / Mooncake fallback | 高，30%-100%+ |
| async load + promotion + overlap 明显 | 方向不确定，可能高估也可能低估 |

### 9.5 为什么不是低，也不是中高

不是低：

```text
V1 已经能表达 DDR hit 会产生 load cost，
load cost 可按 tokens/bytes 建模，
多个 load 会排队，
load wait 会进入 TTFT。
```

不是中高：

```text
V1 没有建 TransferEngine、真实协议路径、async completion、
promotion、offload/store bandwidth、priority/backpressure、
remote pooling、layer/page-level load、compute/load overlap、
failure/retry/fallback。
```

所以综合为低到中。

### 9.6 communication 开发意见优先级

本小节记录 communication 相关开发意见。它们不阻塞 V1 验收，但会影响 HitFloor 的可信度。

#### P0：KV load service/wait 指标显式化

问题：

- HitFloor 必须解释 DDR hit 为什么影响 TTFT。
- 当前已有 `kv_load_ms` 和 `kv_load_wait_ms`，但文档和 report 中需要明确：

```text
kv_load_service_ms = kv_load_ms
kv_load_total_ms = kv_load_service_ms + kv_load_wait_ms
```

影响：

- 不影响 replay 语义。
- 直接影响 HitFloor 结果解释。

处理方式：

- report / summary 中显式展示 service、wait、total。
- 保持 `kv_load_ms` 向后兼容。

#### P0：DDR load profile guard / calibration knobs

问题：

- DDR-heavy 场景下 TTFT 主要受 KV load profile 影响。
- 未校准的 token-linear / byte-linear profile 只能用于趋势，不应当成真实值。

影响：

- 直接影响 HitFloor 中 DDR hit 的收益判断。

处理方式：

- model / instance profile 中显式声明 DDR load profile。
- 缺失时使用 conservative default，并在输出中标注 `uncalibrated`。
- 支持后续从线上采样、Mooncake benchmark、Ramulator2 或公司内工具拟合。

#### P0：pooling mode 与 communication mode 绑定

问题：

- 不同 pooling mode 对 communication 语义要求不同。
- `write_through_on_materialization` 与 `hbm_evict_offload_ddr` 的 DDR 可见性和 load 前置条件不同。

影响：

- 如果 mode 不显式，HitFloor 可能错误解释 DDR hit。

处理方式：

- profile 中显式声明：

```text
pooling_mode
kv_load_mode
kv_store_mode
```

- 未实现组合 fail-fast。

#### P1：load completion event

问题：

- 当前 KV load 只有 accounting，没有真实 completion event。
- promotion、async visibility、load target allocation 都依赖 completion。

影响：

- 对 V1 hit accounting 不阻塞。
- 对更真实 HBM/DDR tier 分布有影响。

处理方式：

- 新增 load completion event。
- 与 promotion policy / HBM target allocation policy 绑定。

#### P1：store/offload bandwidth accounting

问题：

- 当前 store 是 cache event，不消耗通信带宽。
- 真实系统中 HBM -> DDR store/offload 可能与 DDR -> HBM load 竞争带宽。

影响：

- DDR-heavy 和 eviction-offload 场景下可能低估 TTFT。

处理方式：

- 新增 store/offload transfer request。
- 与 load 共用或配置不同 transfer queue。

#### P1：load-vs-recompute policy

问题：

- DDR hit 不一定值得 load。

影响：

- DDR load 很慢或 queue wait 很长时，继续 load 可能比 recompute 更差。

处理方式：

- 新增 policy：

```text
load if saved_compute_ms > load_cost_ms
else recompute
```

#### P2：layer-wise / page-wise KV load

问题：

- V1 当前 KV load 是 request / iteration aggregate。
- 真实系统可能 layer-wise、page-wise 或 batch-wise load。

影响：

- 无法表达 compute/load overlap 和 page-level transfer。

处理方式：

- 新增 `KVLoadShape`。
- 支持 layer/page/chunk 粒度。
- 与 overlap backend 结合。

#### P2：Mooncake / TransferEngine adapter

问题：

- V1 不建真实 Mooncake TransferEngine。

影响：

- remote pooling、replica placement、failure/fallback 场景 TTFT 误差较大。

处理方式：

- 新增 opt-in adapter。
- 只作为 calibration / replay backend，不进入默认轻量路径。

#### P3：failure / timeout / retry / fallback

问题：

- V1 不建通信失败和 fallback。

影响：

- 对 V1 HitFloor 初版不阻塞。
- 对真实服务可靠性分析重要。

处理方式：

- 在 TransferEngine adapter 稳定后新增。

### 9.7 communication 验收结论

本轮 communication 验收通过。

结论：

- InferTwin V1 communication 对齐程度为低到中。
- V1 已具备 replay-facing KV load latency accounting 和 deterministic shared-link wait。
- 这些能力足够支撑 HitFloor 初版对 DDR hit 成本的趋势分析。
- V1 未模拟真实 vLLM-Ascend / Mooncake communication runtime。
- 这些差异不阻塞 V1，但会影响 DDR-heavy、remote pooling 和高并发场景下的 TTFT 精度。
- HitFloor 初版应保守标注 KV load profile 口径，并优先补充 KV load service/wait 显式指标、DDR load calibration、pooling/communication mode guard。

## 10. 遗留问题总排序：面向 HitFloor 下一阶段

V1 验收记录较长，后续开发不应逐段翻找遗留项。本节按下一阶段目标重新排序。

下一步开发重点是 HitFloor 外围能力。因此遗留问题分为三类：

1. 开发 HitFloor 的前置条件。
2. 开发 HitFloor。
3. HitFloor 之后的仿真器设计。

### 10.1 开发 HitFloor 的前置条件

这些工作应优先于 HitFloor 正式开发，或者至少在 HitFloor 第一版中同步完成。原因是它们直接决定 HBM hit / DDR hit / miss / TTFT 组成是否可解释。

#### HF-Pre-P0：pooling mode 显式化与 ConfigGuard

当前 V1 隐含使用：

```text
pooling_mode=write_through_on_materialization
```

HitFloor 必须显式知道当前 pooling mode。否则用户会把当前 V1 语义误解成真实 HBM eviction offload 到 DDR。

要求：

- model / runtime profile 中显式声明 `pooling_mode`。
- V1 支持 `write_through_on_materialization`。
- 未实现模式 fail-fast，例如 `hbm_evict_offload_ddr`。
- report 中展示 pooling mode。

#### HF-Pre-P0：KV load service/wait 指标显式化

HitFloor 必须解释 DDR hit 为什么影响 TTFT。

要求：

```text
kv_load_service_ms = kv_load_ms
kv_load_total_ms = kv_load_service_ms + kv_load_wait_ms
```

- 保持 `kv_load_ms` 向后兼容。
- report / summary 中显式输出 service、wait、total。
- 文档中避免把 `kv_load_wait_ms` 误解成全部 KV load 时间。

#### HF-Pre-P0：DDR load profile guard / calibration knobs

DDR-heavy 场景下，TTFT 主要受 KV load profile 影响。

要求：

- model / instance profile 中显式声明 DDR load profile。
- 缺失时使用 conservative default。
- 输出中标注 `calibrated` / `uncalibrated`。
- 支持后续接入线上采样、Mooncake benchmark、Ramulator2 或公司内工具拟合结果。

#### HF-Pre-P0：active KV occupancy-aware HBM capacity

高并发下，running requests 的 active KV 会占用 HBM，直接影响 HBM hit / DDR hit 分布。

要求：

```text
effective_hbm_prefix_capacity(t)
= total_hbm_kv_blocks
- active_running_blocks(t)
- reserved_blocks
```

第一版可以轻量实现：

- 不建完整 refcount / free queue。
- 只估算 running requests 的 active KV occupancy。
- 动态调整 HBM prefix cache 可用容量。

这项对 HitFloor 很关键，因为 HitFloor 需要解释：

- 高并发下为什么 DDR hit 更多。
- 低并发下为什么 HBM hit 更多。
- 同样 total hit rate 下为什么 TTFT 不同。

#### HF-Pre-P1：runtime block size / tokenizer / chat template parity check

HitFloor 主要依赖 prefix cache hit。tokenizer、chat template、runtime block size 配错会直接导致 hit 失真。

要求：

- 运行前确认 model profile 绑定 tokenizer / chat template。
- 确认 runtime block size，而不是只看 CLI `--block-size`。
- 对 CP / MTP / EAGLE / DCP / PCP 配置进入 ConfigGuard。

### 10.2 开发 HitFloor

这些是 HitFloor 外围能力本身。它们必须消费核心仿真器 typed result，不允许在 report / CLI 中重算 replay 语义。

#### HF-P0：tier-aware HitFloor schema

HitFloor 不能只输出 total hit rate。

核心输出必须区分：

```text
hbm_hit_tokens
hbm_hit_rate
ddr_hit_tokens
ddr_hit_rate
miss_tokens
miss_rate
kv_load_service_ms
kv_load_wait_ms
kv_load_total_ms
p90_ttft_ms
```

原因：

```text
HBM hit:
  节省 prefill compute，基本不增加 KV load latency

DDR hit:
  节省 prefill compute，但增加 KV load service/wait
```

#### HF-P0：capacity / concurrency / request length sweep

HitFloor 第一版应围绕这张表构建：

```text
request_length_bucket
peak_concurrency
hbm_capacity_blocks
ddr_capacity_blocks
hbm_hit_rate
ddr_hit_rate
miss_rate
p90_ttft_ms
p90_compute_wait_ms
p90_kv_load_service_ms
p90_kv_load_wait_ms
pooling_mode
kv_load_profile_status
```

推荐 request length bucket：

```text
8K / 32K / 64K / 128K / 200K
```

推荐并发输入：

- 从真实 trace 抽取 peak window。
- 或用合成 arrival pattern 构造最高并发状态。

#### HF-P0：DDR hit 收益解释

HitFloor 必须判断 DDR hit 是否真的带来 TTFT 收益。

解释口径：

```text
saved_compute_ms = recompute_ms(ddr_hit_tokens)
load_cost_ms = kv_load_service_ms + kv_load_wait_ms
```

如果：

```text
saved_compute_ms > load_cost_ms
```

DDR hit 有收益。

如果：

```text
saved_compute_ms <= load_cost_ms
```

DDR hit 可能收益较低，甚至让 TTFT 变差。

#### HF-P1：HitFloor summary / report

HitFloor 应提供面向用户的简洁 report：

- 核心 CSV：long-format tier-aware table。
- summary.md：说明最佳 capacity 区间、HBM/DDR hit 分布、TTFT 趋势和风险。
- 明确标注 TTFT profile 是否 calibrated。
- 明确标注 pooling mode。
- 明确标注当前不建模 remote pooling / real TransferEngine / decode。

#### HF-P1：HitFloor 不做的事情

HitFloor 初版不应做：

- 在外围能力里重算 prefix hit。
- 在 report 层重算 TTFT。
- 自动假设 DDR hit 一定有收益。
- 把 total hit rate 当成唯一指标。
- 混入 gateway routing。
- 混入真实 physical KV memory pressure 结论。

### 10.3 HitFloor 之后的仿真器设计

这些能力很重要，但不应阻塞 HitFloor 初版。进入后续阶段时必须重新定义产品边界和技术路线。

#### Sim-P0：hbm_evict_offload_ddr backend

如果目标部署依赖 HBM eviction 后 offload 到 DDR，则需要新增：

```text
tiered_cache_mode=hbm_evict_offload_ddr
```

需要建模：

- offload event。
- offload completion timing。
- DDR visible-after-offload。
- store/offload bandwidth。
- 后续 DDR load 前置条件。

说明：

- 如果 HitFloor 初版接受 V1 write-through mode，该项不阻塞原型。
- 如果要对齐真实 eviction-offload 部署，该项应提升为 HitFloor 前置 P0。

#### Sim-P1：DDR load completion / promotion policy

DDR hit load 完成后是否进入 HBM，会影响后续请求是 HBM hit 还是 DDR hit。

需要新增：

- load completion event。
- promotion policy。
- HBM target allocation policy。

#### Sim-P1：load-vs-recompute policy

DDR hit 不一定值得 load。

后续可新增：

```text
load if saved_compute_ms > load_cost_ms
else recompute
```

#### Sim-P1：store/offload bandwidth accounting

真实系统中 HBM -> DDR store/offload 可能和 DDR -> HBM load 竞争带宽。

需要新增：

- store/offload transfer request。
- load/store shared queue 或独立 queue 配置。

#### Sim-P2：真实 KV transfer timeline backend

当前 `SharedLinkFIFOTransferQueue` 是 deterministic accounting abstraction。

后续可新增：

- priority。
- backpressure。
- load completion event。
- bandwidth sharing。
- remote/local tier 差异。

#### Sim-P2：Mooncake / TransferEngine adapter

用于更真实地表达：

- metadata query。
- replica selection。
- batch submit。
- status polling。
- timeout / failure / fallback。
- remote pooling。

该能力应作为 opt-in adapter，不进入默认轻量路径。

#### Sim-P2：compute / transfer overlap backend

当前默认 `overlap_mode=none_v1`。

后续可新增：

```text
overlap_mode=max_compute_load_v1
overlap_mode=layer_pipeline_v1
```

`layer_pipeline_v1` 需要 per-layer KV load shape 和 per-layer compute profile。

#### Sim-P3：physical KV slot backend

用于真实 memory pressure / preemption / active block lifecycle 仿真。

需要建模：

- active block table。
- free queue。
- refcount / pinned。
- request finish reverse-order free。
- preemption policy。

该项对真实 serving memory manager 重要，但不阻塞 HitFloor 初版。

#### Sim-P3：Decode / TPOT / deployment-aware batch latency

只有在明确需要 decode-heavy 或 PD 混部建模时开启。

前置条件：

- trace 增加 output token count。
- 新增 decode-aware scheduler / replay mode。
- 新增 TPOT latency component。

#### Sim-P3：Hybrid / sparse attention cache manager

面向 Qwen / DeepSeek hybrid 或未来 sparse attention 模型。

需要新增：

- hybrid cache group schema。
- sparse-aware cache manager。
- block conversion policy。

### 10.4 下一步建议

下一步建议按以下顺序推进：

1. 先做 HitFloor 前置 P0。
2. 再开发 HitFloor tier-aware 外围能力。
3. HitFloor 初版验收后，再决定是否进入 `hbm_evict_offload_ddr`、promotion、真实 TransferEngine 或 active physical KV backend。

优先路线：

```text
pooling mode guard
-> kv_load service/wait 显式化
-> DDR load profile guard
-> active KV occupancy-aware HBM capacity
-> HitFloor tier-aware schema/report
```
