# InferTwin V1 TTFT 与 Prefix Cache Hit 误差分析

本文用于说明 InferTwin V1 与真实 vLLM / vLLM-Ascend / Mooncake 推理服务之间的差异，以及这些差异可能导致的 TTFT 和 prefix cache hit 偏差。

结论来自本地源码对照阅读：

- InferTwin: `src/infertwin/`
- vLLM: `/home/zhangxiyue/vllm/vllm/`
- vLLM-Ascend: `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/`
- Mooncake: `/home/zhangxiyue/Mooncake/`

本文给出的误差范围是工程估计，不是统计置信区间。真实误差需要通过线上 trace、vLLM metrics、TTFT 采样或外部仿真器校准。

## 1. 总体判断

InferTwin V1 对 prefix cache hit 的结构性误差通常小于 TTFT 误差。

原因：

- prefix cache hit 已尽量对齐 vLLM 的 block-level 规则，包括 `prompt_tokens - 1`、完整 block 命中、runtime block size、CP、MTP/EAGLE drop 和 progressive full-block visibility。
- TTFT 仍然是 fitted / profile / replay timeline 建模，没有真实 kernel、真实通信、真实 queue、真实 decode 干扰和硬件非线性。

因此：

- Prefix cache hit 更接近“可校准的 block-level replay”。
- TTFT 更接近“可解释的 fitted latency replay”。

## 2. TTFT 误差来源

InferTwin V1 中 TTFT 的核心口径是：

```text
ttft_ms = compute_wait_ms
        + kv_load_wait_ms
        + uncached_prefill_compute_ms
        + unattributed_ttft_ms
```

其中：

- `compute_wait_ms` 来自 scheduler replay 中 chunk 等待。
- `kv_load_wait_ms` 来自 deterministic shared-link FIFO accounting。
- `uncached_prefill_compute_ms` 来自 fitted TTFT backend。
- `unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果。

### 2.1 TTFT 误差表

| 差异项 | InferTwin V1 | 真实服务 | 偏差方向 | 粗略误差 |
| --- | --- | --- | --- | --- |
| prefill compute | fitted / token-linear profile | 真实模型 kernel、batch shape、算子实现 | 可能高估或低估 | 校准好：5%-20%；未校准：20%-60%+ |
| batch shape | vLLM-like prefill replay | prefill + decode + preemption + PP / LoRA / spec decode | 通常低估复杂混部等待 | 普通 prefill：10%-30%；复杂混部：30%-100%+ |
| queue | 默认不建实例入口排队 | gateway / server / scheduler 多级排队 | 通常低估 | 误差约等于真实排队时间，可从 0 到数秒 |
| KV load | token / byte linear + FIFO wait | H2D / RDMA / HCCL / DMA / CPU copy / layer-wise load | 可能高估或低估 | DDR-heavy：20%-80%；高并发传输可 >100% |
| compute/load overlap | 默认不建 same-request overlap | 真实可能 transfer 与 compute overlap | 通常高估 | 高估上限约为 `min(compute_ms, load_ms)` |
| Decode / TPOT | 未建模 | decode 会占用 engine 资源 | 通常低估 | PD 分离：<5%-10%；PD 混部 decode-heavy：20%-100%+ |
| 硬件非线性 | 简化 profile | kernel、memory bandwidth、batch shape 非线性 | 可能高估或低估 | P90 偏差通常大于均值 |

### 2.2 TTFT 误差分级

| 场景 | 预期 TTFT 误差 | 置信度 |
| --- | --- | --- |
| full-attention、HBM-only、TTFT profile 已校准、prefill 主导 | 5%-20% | 中高 |
| full-attention、HBM + DDR、KV load profile 已校准 | 10%-30% | 中 |
| 未校准 TTFT profile，但 trace 分布稳定 | 20%-60% | 中低 |
| DDR-heavy、高并发 KV load、真实链路有 backpressure | 30%-100%+ | 低 |
| PD 混部、decode-heavy、TPOT 干扰明显 | 20%-100%+ | 低 |
| Hybrid / sparse attention、真实 cache group 复杂 | 不建议给数值 | 低 |

## 3. Prefix Cache Hit 误差来源

InferTwin V1 的 prefix cache hit 是 hash-only block replay。

核心规则：

```text
max_cache_hit_length = prompt_tokens - 1
effective_block_size = runtime_block_size * PCP * DCP
cached_tokens = full matched effective blocks, with optional MTP/EAGLE one-block drop
```

InferTwin 不保存真实 KV tensor，只保存 block hash 和轻量 metadata。

### 3.1 Prefix Cache Hit 误差表

| 差异项 | InferTwin V1 | 真实服务 | 偏差方向 | 粗略误差 |
| --- | --- | --- | --- | --- |
| full-attention HBM hit | 对齐 vLLM block accounting | vLLM `get_computed_blocks()` / full block lookup | 基本一致 | 配置正确时接近 0，最多约 1 个 effective block |
| tokenizer / chat template | profile registry | 真实服务 tokenizer / template | 错配会大幅低估 hit | 正确：0-5pp；错误：可接近 100% miss |
| runtime block size | conversion policy | vLLM-Ascend 可能 runtime override | 错配会改变所有 block 边界 | 可差 10pp-50pp+ |
| CP / MTP / EAGLE | 显式 accounting | 真实 scheduler / KV manager | 配置错会系统性偏差 | 常见为 1 个或多个 effective block |
| progressive visibility | Step9 新 mode 支持 chunk 完成后 full block 可见 | 真实 full block 生成后可被后续请求使用 | 旧 finish-time 会低估，progressive 明显改善 | progressive：通常 0-10pp；旧模式长 prefill 可低估 20pp-100pp |
| active KV / refcount / pinned | 不建真实物理 slot | vLLM 有 refcount、free queue、preemption | 可能高估 hit | 正常容量：0-10pp；高压容量：10pp-50pp+ |
| DDR/CPU tier | 同实例 DDR/CPU metadata tier | 真实 offload / async store / layer-wise load | 可能高估短间隔 DDR hit | 低并发：0-10pp；高并发/异步 store：10pp-40pp |
| remote pooling | 未建跨实例命中 | Mooncake 可支持 remote/global cache | InferTwin 低估 hit | 低估 remote hit 部分，可能 0-100% |
| Hybrid / sparse attention | 未完整支持复杂 cache group | 多 cache group / 非均匀 block / sparse layout | 可能高估或低估 | 不建议给数值，可能 10pp-100pp |

### 3.2 Prefix Cache Hit 误差分级

| 场景 | 预期 hit-rate 误差 | 置信度 |
| --- | --- | --- |
| full-attention、HBM-only、tokenizer/template/runtime block size 正确 | 0-5pp | 高 |
| full-attention、HBM + DDR、同实例 pooling、profile 正确 | 0-10pp | 中高 |
| 长 prefill、高复用、progressive mode 开启 | 0-10pp | 中 |
| 长 prefill、高复用、legacy finish-time mode | 20pp-100pp 低估风险 | 中 |
| runtime block size / CP / MTP 配置错误 | 10pp-50pp+ | 中 |
| remote pooling 显著 | 低估 remote hit 部分 | 中低 |
| Hybrid / sparse attention | 不建议使用 V1 hit 结论 | 低 |

## 4. 关键差异对 TTFT 和 Hit 的联动影响

### 4.1 Tokenizer / Chat Template 错配

影响：

- prefix block hash 全部改变。
- hit-rate 可能从高命中变成接近 0。
- miss_tokens 增加后 TTFT 同步升高。

误差：

- hit-rate 可差接近 100pp。
- TTFT 可从 near-zero prefill 变成完整 prefill。

风险控制：

- 模型必须通过 model registry 绑定 tokenizer / chat template。
- 大 trace 前先做小样本 tokenizer parity check。

### 4.2 Runtime Block Size 错配

影响：

- block 边界变化。
- cached_tokens 按错误粒度向下取整。
- CP / DCP / PCP / MTP / EAGLE 下误差会放大。

误差：

- 常见为 1 个 effective block 到多个 effective block。
- trace-level hit-rate 可差 10pp-50pp+。

风险控制：

- 以 runtime block size 为准，不只看 CLI `--block-size`。
- Ascend hybrid / Mamba 场景必须检查启动日志和 model config。

### 4.3 Finish-Time vs Progressive Visibility

InferTwin legacy mode 中，miss blocks 到 request finish 后才可见。

真实 vLLM 中，完整 block 生成后可以进入 cache block 管理路径，长 prefill 期间可能被后续请求命中。

影响：

- legacy mode 会低估长 prefill 中的 block reuse。
- Step9 progressive mode 已修正为 chunk finish 后 newly completed full blocks 可见。

误差：

- progressive mode：通常剩余 0-10pp。
- legacy mode：长 prefill、高复用、高并发场景可低估 20pp-100pp。

风险控制：

- V1 后续默认推荐使用 `batch_aware_hbm_ddr_lru_progressive_timeline`。

### 4.4 真实物理 KV Slot / Refcount / Preemption

InferTwin 的 cache capacity 表示 prefix cache residency capacity，不表示真实 active sequence KV slot。

真实 vLLM 中，BlockPool 有 refcount、free queue、slot allocation、preemption 和 reverse-order free。

影响：

- InferTwin 不会因为 active sequence KV pressure 触发 OOM 或 preemption。
- InferTwin 的 eviction 只影响后续 hit，不影响当前请求执行。
- 高压容量场景可能高估 prefix cache 可用性。

误差：

- 正常容量：0-10pp hit-rate。
- 高压容量或并发超高：10pp-50pp+ hit-rate。
- TTFT 可能低估 preemption / recompute / waiting。

风险控制：

- 若研究真实 capacity pressure，需要新增 physical slot backend。

### 4.5 DDR / CPU / Mooncake 传输

InferTwin 的 KV load 是 token / byte linear profile，并用 deterministic FIFO 表达 shared-link wait。

真实链路可能包含：

- H2D / D2H stream。
- RDMA / fabric memory。
- CPU copy。
- HCCL all-to-all。
- Mooncake metadata query。
- replica selection。
- local disk / disk fallback。
- TransferEngine batch submit / status polling。
- admission queue。

影响：

- DDR hit 的 latency 可能被高估或低估。
- 如果真实 store/load 是 layer-wise 或 async，request-level load 粒度会产生归因误差。
- 高并发 KV load 下，真实 backpressure 可能更复杂。

误差：

- 低并发、本地 DDR：10%-30% TTFT。
- 高并发、remote / disk / fallback：30%-100%+ TTFT。
- hit-rate 本身通常不受 latency profile 影响，但真实异步 store 完成时刻会影响短间隔 hit。

风险控制：

- 后续新增 KV transfer timeline backend。
- 对 Mooncake / Ramulator2 只作为 calibration source，不直接污染 replay core。

## 5. 最可信和最不可信的结论

### 5.1 当前最可信

InferTwin V1 当前最可信的场景：

- routed trace。
- full-attention 模型。
- tokenizer / chat template / runtime block size 正确。
- HBM-only 或同实例 DDR/CPU tier。
- prefill 主导。
- capacity sweep 的相对趋势。
- prefix cache hit token accounting。

在这些条件下：

- prefix hit-rate 误差通常可控制在 0-5pp 到 0-10pp。
- 校准后的 P90 TTFT 趋势通常有参考价值。

### 5.2 当前最不可信

InferTwin V1 当前不应给强结论的场景：

- 未校准 TTFT 绝对值。
- decode-heavy 或 PD 混部。
- remote pooling / cross-instance hit。
- Mooncake 真实通信链路性能。
- Hybrid Mamba / sparse attention cache group。
- 真实 physical KV slot pressure / preemption。
- gateway routing 和 instance admission queue。

这些场景需要新增 mode、backend、policy、adapter 或 schema。

## 6. 推荐误差标注口径

对外汇报时，建议使用以下口径：

```text
InferTwin V1 的 prefix cache hit 是 block-level replay 结果。
在 full-attention、tokenizer/template/runtime block size 配置正确的前提下，
trace-level hit-rate 预计具备较高可信度。

InferTwin V1 的 TTFT 是 fitted latency replay 结果。
它适合比较趋势和容量变化带来的相对影响，
不应直接等同于线上真实 TTFT。
TTFT 绝对值需要结合真实采样、vLLM metrics 或外部仿真器校准。
```

## 7. 后续降低误差的优先级

按收益排序：

1. 做 tokenizer / chat template parity check，避免 hash 全局错配。
2. 强制 runtime block size / CP / MTP / EAGLE 配置校验。
3. 默认使用 Step9 progressive mode，避免长 prefill reuse 被低估。
4. 对 fitted TTFT backend 做线上采样校准。
5. 对 KV load profile 做 DDR/CPU/Mooncake 采样校准。
6. 新增 physical slot backend，研究真实 capacity pressure 和 preemption。
7. 新增 decode-aware scheduler mode，处理 PD 混部或 decode-heavy 场景。
8. 新增 hybrid / sparse attention cache manager。

