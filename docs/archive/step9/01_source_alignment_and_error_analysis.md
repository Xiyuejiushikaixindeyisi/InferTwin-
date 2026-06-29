# Step9 Source Alignment And Error Analysis

状态：源码对齐与误差分析文档，待用户评审。

阶段类型：核心仿真器。

改动等级：L0 文档治理。

本文件回答 Step9 评审中提出的三个问题：

1. 通过阅读 vLLM / vLLM-Ascend / Mooncake 源码，确认 KV load、TTFT、prefix hit、batching
   和通信链路与 InferTwin 现状的差异。
2. 明确 Step9 为什么不能只做 progressive visibility。
3. 直接给出现有 TTFT / prefix cache hit 估算与真实系统的精度差异来源和可计算区间。

## 1. 参考源码

本次阅读的本地源码：

### vLLM

- `/home/zhangxiyue/vllm/vllm/v1/core/kv_cache_manager.py`
- `/home/zhangxiyue/vllm/vllm/v1/core/single_type_kv_cache_manager.py`
- `/home/zhangxiyue/vllm/vllm/v1/core/block_pool.py`
- `/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py`
- `/home/zhangxiyue/vllm/vllm/v1/worker/gpu/model_runner.py`
- `/home/zhangxiyue/vllm/vllm/model_executor/layers/attention/kv_transfer_utils.py`
- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/offloading/scheduler.py`
- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/offloading/worker.py`
- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py`
- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/nixl/connector.py`

### vLLM-Ascend

- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/kv_offload/npu.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/kv_offload/cpu_npu.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/patch/platform/patch_kv_cache_interface.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/utils.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/ascend_config.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_p2p/mooncake_connector.py`

### Mooncake

- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/include/transfer_engine.h`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/include/transport/transport.h`
- `/home/zhangxiyue/Mooncake/mooncake-store/include/real_client.h`
- `/home/zhangxiyue/Mooncake/mooncake-store/src/real_client.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-store/include/transfer_task.h`

## 2. DDR / CPU / Remote KV Load 发生在什么时刻

### 2.1 vLLM scheduler 侧

真实 vLLM 的关键顺序是：

```text
waiting request
-> scheduler get_computed_blocks()
-> connector get_num_new_matched_tokens()
-> allocate target KV slots
-> if async external KV load:
     request.status = WAITING_FOR_REMOTE_KVS
     no new compute tokens this step
   else:
     request enters running / scheduled compute
```

当 worker connector 后续报告 `finished_recving` 后，scheduler 会把 received KV blocks 标记为
computed / cached，再让 request 继续参与后续调度。

因此，真实 load 不应理解为 request finish 后发生；它发生在 scheduler 判断该 request
需要 external KV、分配目标 block 之后，并且可能让 request 在 compute 前等待。

### 2.2 vLLM worker / attention 侧

真实 worker 侧在模型 forward 周围有 connector hooks：

```text
pre_forward / start_load_kv
model forward
attention layer enter -> wait_for_layer_load(layer)
attention layer exit  -> save_kv_layer(layer)
post_forward / get_finished
```

不同 connector 的实现不同：

- 有些 connector 不做 layerwise wait，load 更接近 request-level async。
- 有些 connector 可以在 attention layer 前等待对应 layer 的 KV。
- store/offload 路径可能把 newly full blocks 随 chunk 进度异步 store，而不是等 request finish。

### 2.3 vLLM-Ascend CPU/NPU offload

vLLM-Ascend 的 `CpuNpuOffloadingHandler` 使用真实张量：

```text
CPU tensor <-> NPU tensor
via torch.npu stream + torch.ops._C_ascend.swap_blocks
```

它有独立 H2D / D2H stream 和 NPU event，因此 load/store 可以异步提交并通过 event 查询完成。

### 2.4 vLLM-Ascend Mooncake P2P

Ascend Mooncake P2P connector 的 scheduler 侧：

- `get_num_new_matched_tokens()` 对 remote prefill 返回 external tokens 和 async load 标记。
- `update_state_after_alloc()` 记录本地目标 block ids。
- `build_connector_meta()` 把需要接收的 request metadata 交给 worker。

worker 侧：

- `register_kv_caches()` 注册真实 KV cache buffer base address 和 block byte length。
- `start_load_kv()` 根据 TP / PCP / DCP / PP 计算远端端口、远端 block ids 和本地 block ids。
- 背景 recv thread 负责实际传输并报告完成。

### 2.5 Mooncake Store

Mooncake Store 的 `batch_get_into_multi_buffers()` 是对象 / buffer slice 级接口：

- 先 `BatchQuery(keys)` 选 replica。
- MEMORY replica 走 TransferEngine / registered buffer。
- LOCAL_DISK / DISK 走文件或 storage path。
- GPU buffer 场景要求目标 buffer 已注册；部分路径可以 scatter 到非连续目标 slice。

Mooncake TransferEngine 的 request 描述包含：

- opcode READ / WRITE。
- source pointer。
- target id / target offset。
- length。
- protocol-specific metadata：rdma、local、tcp、nvmeof、cxl、hccl、ascend_direct 等。

## 3. 现有 TTFT 建模和真实 TTFT 的区别

当前 InferTwin Step8 语义：

```text
request TTFT
  ~= scheduler_wait_ms
   + uncached_prefill_compute_ms
   + kv_load_ms
```

其中：

- `uncached_prefill_compute_ms` 来自 fitted/static TTFT component。
- `kv_load_ms` 来自 fitted/static KV load component。
- DDR hit request 第一次被 scheduler 选中时收取 KV load。
- 默认无 compute/load overlap。
- 默认无 load queue/backpressure。
- 默认无 load completion event。

真实 vLLM / vLLM-Ascend 的 TTFT 更接近：

```text
admission / queue wait
-> local prefix lookup
-> external KV lookup
-> target slot allocation
-> maybe async KV load wait
-> one or more prefill compute chunks
-> layer/page/block transfer and compute may overlap
-> first token available
```

差异：

| 维度 | InferTwin Step8 | 真实 vLLM / Ascend / Mooncake |
| --- | --- | --- |
| 计算粒度 | iteration/request 聚合 | scheduler chunk、layer、kernel、transfer event 混合 |
| KV load timing | first schedule 时收费，直接并入 duration | scheduler 分配 target slots 后异步 load，可能进入 waiting state |
| overlap | 默认不建模 | connector / DMA / stream / layerwise wait 可能 overlap |
| queue/backpressure | 不建模 | TransferEngine、线程、链路、buffer、event 都可能产生等待 |
| completion | 无 load completion event | worker reports finished_recving / finished_sending |
| decode interference | 不建模 | decode 和 prefill 共享 scheduler / compute resources |

Step9 因此必须引入 chunk timeline 和 load wait state，否则 TTFT 仍然只能是 Step8 的粗粒度近似。

## 4. 现有 Load 链路和真实 Load 链路的区别

当前 InferTwin：

```text
DDR hit tokens / bytes
-> KVLoadLatencyProfile
-> kv_load_ms scalar
-> add to iteration/request duration
```

真实链路：

```text
external/local tier lookup
-> source blocks / object keys
-> target HBM slots
-> transfer descriptors
-> async submit
-> transfer queue / protocol / registered memory
-> completion signal
-> scheduler unblocks request
```

关键差异：

- InferTwin 当前没有 source/target slot mapping。
- 没有真实 transfer descriptor。
- 没有 queue、priority、thread 或 event。
- 没有 load completion 后的 HBM promotion。
- 没有 layer/page/chunk-level transfer split。

Step9 建议先补最小 timing：

```text
KVLoadTimingPolicy + KVTransferTimelinePolicy
```

仍不模拟真实 TransferEngine。

## 5. 现有 Prefix Hit 链路和真实 Prefix Hit 链路的区别

### 5.1 vLLM 真实规则

vLLM prefix cache 关键规则：

- 只命中 full block。
- lookup 前设置 `max_cache_hit_length = request.num_tokens - 1`。
- `prompt_tokens - 1` 是因为即使完整 prompt 命中，也需要重算最后 token 才能得到下一个 token logits。
- CP / PCP / DCP 会改变 effective block size。
- MTP / EAGLE / EAGLE3 会丢弃最后一个 matched block。
- running request 产生 full block 后，可以进入 block pool 的 cached block map。
- cached block 可以处于 running refcount 或 free queue 中；eviction 与 refcount/free queue 相关。

### 5.2 InferTwin 当前规则

当前 InferTwin 已实现：

- hash-only prefix block chain。
- vLLM-like cached token accounting。
- HBM + DDR contiguous lookup。
- HBM / DDR LRU。
- finish-time materialization。
- no physical slot/refcount/pin。

主要差异：

| 维度 | InferTwin 当前 | 真实 vLLM |
| --- | --- | --- |
| 可见时间 | request finish 后 full miss blocks 可见 | full blocks 可随 request progress 进入 cache map |
| 物理状态 | hash + metadata | physical block id、refcount、free queue、cached map |
| re-lookup | 当前一次 lookup 口径 | scheduler 在等待请求被考虑时计算 computed blocks；外部 KV 可能重试 |
| eviction | logical LRU | 与 free queue/refcount/pin/allocator 状态相关 |

Step9 必须补 progressive full-block visibility，但仍不做 physical slot/refcount。

## 6. 现有组 Batch 方法和真实组 Batch 方法的区别

当前 InferTwin：

- fixed-routing multi-instance isolated replay。
- running / waiting request state。
- vLLM-like token budget。
- `batch_size` = iteration 内 request slice 数。
- `max_num_batched_tokens` = iteration token budget。
- prefill-only。
- no decode / spec decode。
- no preemption / priority / LoRA / encoder constraints。

真实 vLLM：

- scheduler 不简单区分 prefill/decode phase，而是用 `num_computed_tokens` 追赶
  `num_tokens_with_spec`。
- RUNNING 优先，然后 WAITING。
- token budget、max running request、long prefill threshold、encoder budget、LoRA、priority、
  preemption、connector状态都会影响 batch。
- external KV async load 会把 request 放入 `WAITING_FOR_REMOTE_KVS`。

Step9 不需要复制全部 vLLM scheduler，但要补充：

- load waiting state。
- chunk timeline。
- progressive materialization timing。

## 7. 现有通信链路和真实通信链路的区别

当前 InferTwin：

```text
tokens/bytes -> fitted/static latency
```

真实系统可能包含：

- CPU pinned memory / DMA / PCIe。
- NPU stream / `swap_blocks` / event。
- HCCL / HIXL / ascend direct。
- RDMA / TCP / NVMe-oF / CXL。
- Mooncake TransferEngine。
- registered buffer。
- ZMQ/bootstrap side channel。
- background send/recv thread。
- batch transfer descriptors。
- replica placement / lease / eviction。

Step9 不接真实通信栈，但需要把通信抽象成：

```text
KVTransferTimelinePolicy:
  submit(load_request)
  -> start_ms
  -> finish_ms
  -> wait_ms
```

## 8. 精度差异的原因和可计算区间

没有真实校准数据时，不应承诺固定百分比误差。更可靠的做法是给出每一类误差的方向和
trace-specific 可计算边界。

### 8.1 Prefix hit 低估：finish-time vs progressive visibility

当前 finish-time mode 可能低估真实 hit。

对单个 request：

```text
0 <= real_hit_tokens - current_hit_tokens
   <= newly_visible_full_tokens_before_lookup
```

其中 `newly_visible_full_tokens_before_lookup` 是其他 in-flight request 在当前 request lookup
前已经完成 full block、但 InferTwin finish-time mode 尚未 materialize 的 token 数。

单请求最坏上界：

```text
reusable_tokens =
  floor((prompt_tokens - 1) / effective_block_size) * effective_block_size
```

如果启用 MTP / EAGLE / EAGLE3：

```text
reusable_tokens =
  max(floor((prompt_tokens - 1) / effective_block_size) - 1, 0)
  * effective_block_size
```

Trace 级 hit rate 低估区间：

```text
0 <= real_hit_rate - current_hit_rate
   <= sum(newly_visible_full_tokens_before_lookup) / sum(prompt_tokens)
```

在没有长请求重叠复用的 trace 中，该误差接近 0。对重复长 prompt 且 arrival 间隔落在前一条
request prefill 中间的场景，误差可接近该 request 的全部 reusable tokens。

### 8.2 Prefix hit 差异：physical block/refcount/eviction

该误差方向不固定。

InferTwin logical LRU 可能：

- 高估真实 hit：真实 physical free queue/refcount/pin 导致 block 不可复用或被不同顺序淘汰。
- 低估真实 hit：真实 refcount/pin 保护了某些 block，logical LRU 提前淘汰。

可计算边界只能在引入 physical slot/refcount mode 后给出。当前可记录为：

```text
hit_token_delta = simulated_hit_tokens - physical_mode_hit_tokens
```

V1 暂无全局静态百分比上界；受 cache capacity、reuse interval 和 running request 数影响。

### 8.3 TTFT 差异：compute fitted model

当前 compute TTFT 来自 fitted/static profile。

如果有真实校准集，应表达为：

```text
real_compute_ms in [fitted_compute_ms - epsilon_fit(shape),
                    fitted_compute_ms + epsilon_fit(shape)]
```

其中 `epsilon_fit(shape)` 应来自离线校准 residual table，shape 至少包含：

- model。
- hardware。
- batch shape。
- uncached tokens。
- chunk size。
- deployment profile。

没有校准集时，只能说“方向未知、区间未知”，不能给固定百分比。

### 8.4 TTFT 差异：未建模 compute/load overlap

Step8 当前保守串行：

```text
T_current = compute_ms + load_ms
```

如果真实系统可以完全 overlap：

```text
T_real = max(compute_ms, load_ms)
```

则串行模型的高估上界：

```text
0 <= T_current - T_real <= min(compute_ms, load_ms)
```

如果真实系统必须先 load 再 compute，该项误差为 0。

### 8.5 TTFT 差异：未建模 load queue / backpressure

当前无队列时可能低估真实 TTFT：

```text
0 <= T_real - T_current <= queue_wait_ms + contention_penalty_ms
```

没有带宽、并发传输和 queue policy 时，`queue_wait_ms` 没有静态上界。Step9 如果实现
`shared_link_fifo_v1`，则可以把该项变成可计算：

```text
queue_wait_ms = load_start_ms - load_ready_ms
```

### 8.6 TTFT 差异：KV load 粒度

当前 Step8 是 request/iteration 聚合 load。

真实系统可能按：

- request。
- block。
- page。
- layer。
- TP / PCP / DCP split。
- store object slice。

进行传输。

第一版 Step9 仍不做 layer/page split。可给出的区间是：

```text
aggregate_load_ms - overlapped_load_ms
```

若引入 chunk/layer split 后，该误差应由 `KVLoadTimelineEntry` 的 critical path 计算，而不是
由总 bytes 直接相加。

### 8.7 TTFT 差异：Decode / TPOT 未建模

当前对纯 prefill TTFT 和 PD 分离场景影响较小；对 PD 混部、decode-heavy 或高输出 token
场景可能低估 TTFT。

无 decode trace 字段和 decode scheduler 前，无法给静态上界。处理阶段仍建议放到 V2 或明确
PD 混部需求后启动。

## 9. 对 Step9 的直接结论

Step9 必须从旧方案升级为：

```text
chunk-level TTFT
+ KV load timing state
+ minimal transfer queue/backpressure
+ progressive full-block visibility
```

只做 progressive materialization 不能解决：

- DDR hit 什么时候开始 load。
- load 是否让 request 等待。
- 多请求 load 是否共享带宽。
- request TTFT 如何由多个 chunk 和 load event 组合。

同时，Step9 不应试图一次性实现真实 Mooncake / Ramulator2 / layerwise transfer。正确边界是：

- 用新 replay/cache mode 增加时间线语义。
- 用 typed timeline metrics 暴露误差来源。
- 保持旧 mode 稳定。
- 后续再通过 Ramulator2 / Mooncake calibration adapter 给 timeline policy 提供参数。
