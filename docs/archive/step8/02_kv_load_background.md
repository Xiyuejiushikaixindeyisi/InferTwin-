# KV Load Latency 背景知识

状态：初版学习笔记。

目标读者：熟悉大模型推理、不熟悉存储/通信仿真的同事。

## 1. KV load 是什么

在 prefix cache 命中时，命中的 KV 不一定都已经在 HBM 中。

Step7 已经区分：

```text
HBM hit tokens
DDR hit tokens
miss tokens
```

HBM hit 表示 KV 已在本实例 HBM prefix cache 中，可近似认为不产生额外 restore latency。

DDR/CPU hit 表示 KV metadata 命中，但真实推理时仍需要把对应 KV tensor 从 DDR/CPU/外部 store 加载到可供计算使用的位置。这个过程就是 Step8 要建模的 KV load。

Step8 后 TTFT 口径应变为：

```text
ttft_ms =
  scheduler_wait_ms
  + prefill_compute_ms(miss / uncached tokens)
  + kv_load_ms(non-HBM cached tokens)
```

当前 InferTwin 暂不建机器侧 queue，因此 `scheduler_wait_ms` 仍来自 replay scheduler 语义，而不是 gateway 或实例入口排队。

## 2. 为什么 KV load 与模型有关

同样是 128 个 cached tokens，不同模型需要加载的 KV bytes 可能差很多。

对普通 full-attention KV cache，可以用近似公式理解：

```text
bytes_per_token_per_rank =
  2
  * num_layers
  * num_kv_heads_per_rank
  * head_dim
  * kv_dtype_bytes
```

其中：

- `2` 表示 K 和 V。
- `num_layers` 是 transformer 层数。
- `num_kv_heads_per_rank` 取决于 GQA/MQA 和并行切分。
- `head_dim` 是每个 head 的维度。
- `kv_dtype_bytes` 取决于 KV cache dtype，例如 fp16/bf16 通常为 2 bytes。

block 级大小：

```text
bytes_per_block =
  effective_block_size_tokens * bytes_per_token_per_rank
```

请求级 DDR load bytes：

```text
ddr_load_bytes =
  ddr_hit_tokens * bytes_per_token_per_rank
```

注意：这是 full-attention 的简化公式。Hybrid/Mamba/MLA/稀疏注意力可能打破这个公式，V1 不能把它们伪装成完全支持。

## 3. KV load 可能包含哪些时间

真实系统中的 KV load 不只是一次 DRAM read。

它可能包含：

| 部分 | 说明 | Step8 v1 是否建模 |
|---|---|---|
| DDR/CPU memory access | 从 DDR/CPU 读取 KV bytes | 用系数折叠建模 |
| 通信传输 | CPU/NPU、节点内、节点间传输 | 用系数折叠建模 |
| HBM 写入 / slot placement | 加载到可计算位置 | 暂时折叠 |
| request queue / controller queue | 多条 load 竞争共享资源 | v1 不单独建 queue |
| load completion sync | load 完成后 prefill 才能继续 | v1 以 additive latency 表达 |
| promotion / eviction | load 后是否进入 HBM cache | v1 不做 promotion |

因此 Step8 v1 的 `kv_load_ms` 不是完整硬件仿真，而是一个明确口径的 latency accounting：

```text
kv_load_ms = fitted profile over non-HBM hit tokens / bytes
```

## 4. 粒度选择

Step8 有四种可选粒度。

### 4.1 Request-level

请求完成时一次性加：

```text
kv_load_ms = f(request.ddr_hit_tokens)
```

优点：实现最简单。

缺点：

- 无法影响 batch iteration finish time。
- 无法处理 zero-miss DDR request 的完成时间。
- 无法和 future progressive visibility 对齐。

不建议作为 Step8 主方案。

### 4.2 First-scheduled-iteration level

请求第一次进入 scheduler iteration 时加载它的 DDR hit KV：

```text
first scheduled slice:
  kv_load_tokens = request.ddr_hit_tokens
later slices:
  kv_load_tokens = 0
```

batch 的 `kv_load_ms` 由本轮所有 `kv_load_tokens` 聚合得到。

优点：

- 与当前 iteration-level latency backend 对齐。
- 可以让 KV load 影响 finish_time。
- 可以处理多请求同轮 load 的简单带宽竞争。
- 不需要 block-level memory trace。

缺点：

- 不建真实 async load。
- 不建 load 与 compute overlap。
- 对很长 prefix 的逐 chunk load 不够细。

建议作为 Step8 v1 主方案。

### 4.3 Block-level

每个 DDR hit block 产生 load latency，并可独立完成。

优点：更接近 KV page/block 管理。

缺点：

- 需要 block load queue。
- 需要 completion event。
- 需要考虑 load target allocation。
- 很容易和 Step9 progressive visibility、promotion 混在一起。

建议 Step8 v1 不做，作为 Step8+ 或 Step9 之后扩展。

### 4.4 Memory-request-level

把 KV block 拆成 DRAM/cacheline-level memory requests，交给 Ramulator2。

优点：最贴近 DRAM simulator。

缺点：

- 实现和运行成本很高。
- 需要地址映射、trace 生成、queue、callback。
- Ramulator2 只覆盖 DRAM access，不覆盖完整通信链路。
- 不适合作为 11G trace 默认路径。

建议仅作为 opt-in calibration harness 或存储专项。

## 5. 并发 load 如何理解

多条请求同一个 iteration 内都有 DDR hit 时，真实系统可能出现：

- load 可并行。
- load 共用一条带宽，近似串行。
- load 与 prefill compute 有 overlap。
- controller queue 满导致等待。

Step8 v1 推荐采用最稳定的口径：

```text
aggregation = shared_link_sum
kv_load_ms = fixed_overhead_ms + sum(ddr_load_bytes) * ms_per_byte
```

这等价于“本实例内 DDR load 共用一个简化链路”。它保守、确定、可测。

未来可新增：

```text
aggregation = per_request_parallel_max
aggregation = bandwidth_queue
aggregation = ramulator2_trace
```

但这些都不应改变 Step8 v1 已确认的默认语义。

## 6. 与 zero-miss 的关系

Step7 中 zero-miss fast-finish 代表：

```text
miss_tokens == 0
```

如果请求全部命中 HBM，则 fast-finish 可以继续成立：

```text
hbm_hit_tokens > 0
ddr_hit_tokens == 0
miss_tokens == 0
kv_load_ms == 0
```

如果请求全部命中 DDR，则不能直接在 lookup 时刻完成：

```text
ddr_hit_tokens > 0
miss_tokens == 0
```

它至少需要 KV load 时间。Step8 必须修正这个路径。

建议口径：

```text
zero-miss HBM-only -> immediate finish
zero-miss with DDR -> finish_time = now + kv_load_ms
```

是否生成专门的 load-only iteration metric，需要在 Step8 代码方案中细化。

## 7. 与 Step9 的关系

Step9 准备实现 progressive block/chunk visibility。它会让长 prefill 中途生成的 cache block 提前可见。

Step8 不应提前实现 progressive visibility，但 Step8 的结构要为 Step9 留接口：

- `kv_load_tokens` / `kv_load_bytes` 应挂在 iteration/slice 上，而不是只挂在最终 request metric 上。
- `kv_load_ms` 应进入 iteration finish time。
- request finish time 应由最后一个相关 iteration 决定。

这样 Step9 把一个长 prefill 拆成更多 chunk 后，KV load latency 仍然可以自然组合。

## 8. V1 与 V2 边界

Step8 V1 做：

- DDR/CPU hit 的 KV load latency accounting。
- 模型相关 KV bytes 输入口径。
- token-linear / byte-linear fitted function。
- per-instance latency profile 的 fallback。
- streaming capacity sweep metrics。

Step8 V1 不做：

- 真实 Ramulator2 online replay。
- load queue/backpressure。
- load 与 compute overlap。
- promotion 到 HBM。
- cross-instance remote KV transfer。
- SSD tier。
- Decode / TPOT。
- Hybrid cache group 精确 tensor layout。

这些边界必须写入 Step8 review，避免“加了 kv_load_ms”被误解为完整存储系统仿真。
