# Internal Model Deployment Method Notes

## 来源与用途

本文档根据用户提供的公司内部模型部署方法整理。该文档用于辅助 HitFloor Step4 理解：

- batch size
- chunked prefill
- PD 分离
- 多级缓存
- 池化

本文档只沉淀与 HitFloor 建模相关的口径，不作为部署操作手册。

## 核心指标

调优目标主要围绕：

- TTFT：首 token 延迟，主要由 Prefill 决定。Prefill 是计算密集型，长上下文下尤其吃算力。
- TPOT：每输出 token 延迟，主要由 Decode 决定。Decode 是显存带宽密集型，通常通过并发、DP 和专家并行来摊薄。

经验目标：

- 上下文长度：128K，`--max-model-len 131072`。
- 128K Prefill TTFT：常见目标小于 40-50 秒。
- TPOT：常规服务小于 50 ms/token，严苛低延迟服务小于 30 ms/token。

## 最大支持并发口径

公司内部“最大支持并发”是业务压测指标：

```text
在满足 TTFT / TPOT 时延约束的前提下，能稳定支撑的最大并发请求数。
```

它不是：

- `--max-num-seqs`
- 框架启动日志里的 maximum concurrency
- 显存容量上理论能容纳的请求数

对 HitFloor 的影响：

- `BatchShape.batch_size` 只能表示单个 scheduler iteration 内的 request slice 数。
- `max_num_seqs` 只能表示组批上限。
- HitFloor 后续输出不能把 iteration batch size 直接解释为最大支持并发。
- 若要做最大支持并发，需要结合 TTFT/TPOT SLO 和压力搜索，这是更高层的 experiment/search 问题。

## 固定基线参数

常见启动参数：

```text
--block-size 128
--seed 1024
--max-num-seqs 16
--max-model-len 131072
--max-num-batched-tokens 32768
--async-scheduling
--gpu-memory-utilization 0.93
```

部署文档强调：

- `--max-num-seqs` 是组批上限，不保证时延。
- `--max-num-batched-tokens` 是 token budget，不是 batch size。
- 这两个值需根据模型和 SLO 调整。

对 HitFloor 的影响：

- `SchedulerConfig.max_num_seqs` 对应框架组批请求上限。
- `SchedulerConfig.max_num_batched_tokens` 对应每轮 prefill token budget。
- 二者都不等价于业务最大支持并发。

## HCCL buffer 与 batch size

部署文档中的 HCCL buffer 估算公式使用 `batch_size`：

```text
HCCL_BUFFSIZE_MB = ceil(2 * (batch_size * ep_world_size
    * min(local_expert_num / ep_world_size, active_experts)
    * hidden_size * dtype_bytes / (1024 * 1024) + 2))
```

这里的 `batch_size` 是通信/MoE 路由形状里的请求并发数，不是 token 数。

对 HitFloor 的影响：

- 通信 buffer 调优不进入 Step4 Batch C。
- 但这再次确认 batch size 是请求数。
- 未来如果要把 HCCL/通信模型纳入 latency backend，必须把 batch size 与 token budget 明确分开。

## chunked prefill

部署文档中，长上下文默认建议开启 chunked prefill：

```text
--enable-chunked-prefill
```

原因：

- 128K 一次性 prefill 激活和显存峰值高。
- chunked prefill 把长 prompt 切块分批喂入。
- 主要作用是平滑显存峰值、支撑长上下文。

GLM5 PD 分离 P 节点示例：

```text
--enable-chunked-prefill
--max-num-batched-tokens 4096
--max-num-seqs 48
```

D 节点示例：

```text
--enable-chunked-prefill
--max-num-batched-tokens 64
--max-num-seqs 48
```

对 HitFloor 的影响：

- Step4 的 scheduler 应将 `max_num_batched_tokens` 解释为 iteration token budget。
- 长请求可以跨多个 iteration 完成 prefill。
- `previous_chunk_tokens` 是同一请求前序 chunk 已经完成的 token，应与 prefix cache hit 区分。
- Step4 Batch C 必须保证一个请求只有在所有 miss tokens 完成后才 finish/materialize。

## PD 分离

部署方法中，性能优化阶段默认切 PD 分离：

- P 节点负责 Prefill。
- D 节点负责 Decode。
- 通过 KV connector 把 P 产出的 KV cache 传给 D。
- P/D 的 TP、DP、EP 可以不同。

瓶颈差异：

- Prefill：计算密集，倾向更大的 TP/PCP 压 TTFT。
- Decode：显存带宽密集，倾向小 TP、大 DP，提高并发与 TPOT 表现。

GLM5 经验配置：

```text
PD 混部: TP8 DP2 EP16
PD 分离:
  P: TP8 DP2 EP16
  D: TP4 DP4 EP16
```

对 HitFloor 的影响：

- Step4 当前只建模 TTFT/prefill，因此更接近 P 节点 replay。
- D 侧 TPOT、MTP、decode graph capture 不进入 Batch C。
- PD transfer / KV connector 延迟暂不进入 Batch C。
- 如果未来输入 trace 来自 PD 分离系统，需要确认 `service_start_time` 是 P 节点接收 prefill 的时间，还是全服务入口时间。

## PD 配比

部署方法强调 P:D 配比需按真实负载调优。

典型方法：

```text
P-QPS = P-bs / TTFT
D-QPS = D-bs / (TPOT * Output-lens)
P 节点数 : D 节点数 = D-QPS : P-QPS
```

真实 agent 负载常见输入:输出比例为 30:1 到 100:1，甚至更高，说明 Prefill 压力通常远大于 Decode。

对 HitFloor 的影响：

- HitFloor Step4 的 TTFT 建模可为 P 侧容量与命中率分析提供输入。
- PD ratio 优化不是 Batch C 范围。
- 若未来做 PD ratio，需要 trace 中的 output length 或请求参数中的 `max_tokens`/实际输出 token。

## Prefix Cache

prefix cache 缓存请求公共前缀对应的 KV：

```text
命中时复用 KV，跳过命中部分 prefill。
```

agent 场景中的系统提示、工具定义、历史上下文等长前缀高度复用，因此收益明显。

部署参数：

```text
--enable-prefix-caching
```

对 HitFloor 的影响：

- HitFloor 的 hash-only prefix block 设计方向正确。
- cache hit 应发生在 prefix block 序列的连续前缀上。
- 命中产生 `cached_prefix_tokens`。
- 未命中产生 `miss_tokens`，并在请求 prefill 完成后 materialize。

## 多级缓存

部署文档定义的多级缓存：

```text
显存 -> 内存 DRAM -> SSD
```

当前部署中主要使用：

```text
显存 + 内存
```

SSD 暂不启用。

多级缓存解决的问题：

```text
单实例内 KV 放在哪里、能放多少。
```

对 HitFloor 的影响：

- Step4 Batch C 只做无限 HBM，不进入多级缓存。
- 后续有限 HBM + DDR LRU 阶段应映射为显存/内存两级。
- DDR/DRAM 命中需要单独 latency backend，不应混进 compute backend。

## 池化

部署文档区分两种说法：

### 当前常说的池化

很多时候实际指多级缓存：

```text
单实例内把 KV 摊到显存 / 内存 / SSD 多级存储。
```

### 广义 KV pooling

多个模型实例共享同一份 KV pool：

```text
一个实例 prefill 产生的 KV，另一个实例也能查到并复用。
```

典型实现：

- Mooncake KV pooling 存储层。
- `MultiConnector`
- `AscendStoreConnector`
- `backend: mooncake`

池化解决的问题：

```text
多个实例之间怎么共享 KV。
```

对 HitFloor 的影响：

- 第一版 Step4 不做跨实例 pooling。
- 当前 `block_key` 不包含 instance id，这是未来跨实例 pooling 的必要条件之一。
- 当前 per-instance cache 隔离仍符合第一版边界。
- 后续 pooling 需要单独建模共享存储、lookup 可见性、一致性和传输开销。

## 测试数据与 prefix 比例

部署文档建议：

- 默认按 50% prefix 比例压测。
- 用人工构造且前缀多样的数据集。
- 不直接使用 ais_bench 自带的单一前缀比例参数，否则容易高估 prefix caching 收益。

对 HitFloor 的影响：

- 合成测试不能只用一个重复 prompt。
- 需要构造多种前缀混合的 synthetic trace，覆盖部分命中、全命中、无命中。
- 真实 trace 的租户/应用维度差异应保留，不能过度聚合。

## 与 AIConfigurator / MkSim 的接口关系

共同结论：

- `batch_size` 是请求数。
- `max_num_batched_tokens` / `ctx_tokens` 是 token budget。
- prefix cache hit 表示“这部分 token 不需要重复 prefill compute”。
- DDR/DRAM KV load 不属于 compute simulator。

差异：

- AIConfigurator 可用 `ctx_tokens` 建模 IFB/chunked prefill。
- MkSim 核心不内建 chunk loop，需 HitFloor 外层拆 chunk。
- 公司部署方法以 vLLM/vLLM-Ascend 实际参数为准，强调 P/D 分离、多级缓存和 Mooncake pooling。

## 对 Batch C 的直接影响

Batch C 应坚持：

```text
fixed-routing, per-instance isolated, P-side-like, infinite-HBM, batch-aware prefill replay
```

Batch C 不应引入：

- D 节点 decode replay。
- PD ratio 搜索。
- Mooncake pooling。
- DDR/DRAM/SSD LRU。
- KV transfer time。

Batch C 必须处理：

- batch size = iteration request slice count。
- token budget = `max_num_batched_tokens`。
- chunked prefill = request miss tokens 跨 iteration 完成。
- `cached_prefix_tokens` 与 `previous_chunk_tokens` 分开。
- full prefix hit / zero miss token 请求的完成语义。

## 待确认问题

1. 现网 trace 中 `service_start_time` 在 PD 分离部署下对应 P 节点开始处理时间，还是聚合服务入口时间。
2. Batch C 是否需要在 metrics 中显式输出 `business_concurrency` 与 `iteration_batch_size` 的区别说明。
3. Batch C 对 100% prefix hit 请求的 TTFT 是否暂定为 0 compute ms，还是预留 HBM KV load ms。
4. 未来有限 HBM/DDR 阶段是否只建模显存+内存两级，SSD 继续不做。
