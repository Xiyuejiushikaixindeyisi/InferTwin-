# Step8 KV Load Overlap 与源码调研记录

状态：调研文档，未进入代码开发。

阶段类型：核心仿真器。

改动等级：L0 文档治理。

本文件回答 Step8 进入代码设计前的几个关键问题：

- InferTwin 的接口和实现是否分开。
- 为什么 KV load 可以与 prefill compute overlap。
- 什么情况下无法 overlap，以及如何观测。
- 真实 vLLM / vLLM-Ascend / Mooncake 相关路径中的传输链路、load 粒度、并发调度。

本次只阅读源码和沉淀结论，不修改业务代码。

## 1. InferTwin 接口和实现是否分开

结论：已经基本分开，但 Step8 需要继续沿用这个边界，而不是把底层传输实现塞进 replay loop。

当前已有的接口边界：

| 领域 | 接口 / 抽象 | 实现示例 | 说明 |
| --- | --- | --- | --- |
| cache | `src/infertwin/cache/base.py::PrefixCache` | `hbm_lru.py`、`ddr_lru.py`、`tiered.py` | replay 只依赖 lookup / materialize / take_events，不依赖具体 LRU 实现。 |
| materialization | `src/infertwin/cache/materialization.py::MaterializationPolicy` | `FinishTimeMaterializationPolicy` | progressive visibility 未来应新增 policy，而不是改默认 finish-time 语义。 |
| latency | `src/infertwin/latency/backend.py::BatchLatencyBackend` | `fitted_ttft.py`、`formula.py` | replay 只问 iteration shape 的 duration。 |
| serving latency | `src/infertwin/latency/profile.py::IterationLatencyComponent` | `ZeroLatencyComponent`、`StaticLatencyComponent` | Step8 应新增 KV-load component，而不是直接在 scheduler 中写公式。 |
| event output | `src/infertwin/cache/event_sink.py::CacheEventSink` | null / stats-only / memory / csv writer | 大 trace 不应该持有全量事件。 |
| request source | `src/infertwin/streaming/source.py::RequestSource` | list / jsonl shard | replay 与内存 list、磁盘 shard 解耦。 |

需要注意的边界：

- `ServingLatencyProfile` 已经预留 `kv_load_component`，但当前默认仍是 zero component。
- `BatchShape` / `ShapeKey` 还没有完整表达 `kv_load_tokens` / `kv_load_bytes`，这正是 Step8 后续要补的接口。
- Step8 代码设计应保持：

```text
replay -> BatchShape / tier hit accounting -> ServingLatencyProfile -> KVLoadLatencyComponent
```

而不是：

```text
replay -> Mooncake / Ramulator2 / 真实通信实现
```

## 2. 为什么 KV load 可以与 prefill compute overlap

在真实服务中，KV load 可以 overlap 的核心原因是：KV transfer 与模型计算通常不是同一个执行单元，且 vLLM connector 接口明确支持 async load。

vLLM `KVConnectorBase_V1` 的 worker-side 接口中：

- `start_load_kv()`：在 forward 前启动 KV load，可异步。
- `wait_for_layer_load(layer_name)`：在 attention layer 内等待某一层 KV 已经到位。
- `save_kv_layer()`：在 layer 内启动保存。
- `wait_for_save()`：forward context 退出时等待保存完成。

这说明 vLLM 的设计目标不是“先把所有 KV load 完，再开始 compute”，而是支持：

```text
start_load_kv
  -> model forward begins
  -> layer i attention 前 wait_for_layer_load(i)
  -> compute and transfer may overlap
```

vLLM `KVConnectorModelRunnerMixin` 也把 `start_load_kv(get_forward_context())` 放在执行模型期间的 context manager 里，并在 finally 中收集 finished_recving / finished_sending。

vLLM-Ascend 的 `AscendStoreConnector` 更明确：

- 非 layerwise：`start_load_kv()` 中直接触发一次 `m_store.get(...)` 或异步 recv thread。
- layerwise：`start_load_kv()` 为每个请求创建 `retrieve_layer()` generator，先拉第一层；`wait_for_layer_load()` 每层推进一次 generator。

这意味着在 layerwise 模式下，真实系统可以做到：

```text
load layer 0
compute layer 0
load layer 1
compute layer 1
...
```

因此 KV load 与 prefill compute overlap 是真实系统想要利用的能力。

## 3. 什么情况下无法 overlap

以下情况会让 overlap 变弱或消失：

1. 同步 load 模式。
   - vLLM-Ascend `KVPoolWorker.start_load_kv()` 在 `load_async=false` 且非 layerwise 时，会直接构造 key/address/size 列表并调用 `m_store.get(...)`。
   - 这种路径更像 forward 前同步完成 load。

2. request 的当前执行完全依赖远端/CPU KV。
   - vLLM scheduler 在 `load_kv_async=True` 时会把 request 置为 `WAITING_FOR_REMOTE_KVS`，本轮不分配新计算 token。
   - 如果本轮 batch 中没有其他可计算请求，compute 没有东西可 overlap。

3. 需要的 layer KV 没及时到达。
   - layerwise 路径虽然可以流水，但 attention layer 前仍要 `wait_for_layer_load()`。
   - 如果 load 慢于 compute 推进，就会在对应 layer 等待。

4. 传输和 compute 争用同一硬件资源。
   - CPU offload 使用 PCIe/DMA、NPU/Ascend 使用 HCCL / HIXL / Mooncake backend 等。
   - 如果链路、DMA engine、host memory bandwidth、NPU fabric 或线程池饱和，overlap 会被带宽/调度拖慢。

5. CUDA graph / graph replay 限制。
   - vLLM connector 接口中提到 layer-by-layer Python hooks 与 CUDA graph replay 有冲突，需要 piecewise 模式保障同步。

6. preemption / eviction 需要 flush。
   - SimpleCPUOffloadWorker 在 preemption 时会同步 in-flight transfers，避免 block 被覆盖。

## 4. 如何通过实验观测 overlap

建议把观测拆成三类，不要只看端到端 TTFT。

### 4.1 对比实验

固定 trace、模型、cache hit 分布，分别运行：

```text
A. HBM-only 或 DDR disabled
B. DDR/CPU pooling + synchronous load
C. DDR/CPU pooling + async load
D. DDR/CPU pooling + layerwise load
```

看以下指标：

- TTFT / P90 TTFT。
- prefill compute time。
- KV load total time。
- request 在 `WAITING_FOR_REMOTE_KVS` 或 connector waiting 状态的时间。
- NPU/GPU compute utilization。
- PCIe / HCCL / RDMA / fabric bandwidth。

如果 `kv_load_time` 很大但 TTFT 增量明显小于 `kv_load_time`，说明存在 overlap。

### 4.2 时间线实验

在 connector 侧打点：

```text
lookup start/end
start_load_kv
per request load submit
per layer wait_for_layer_load enter/exit
model forward layer begin/end
finished_recving
```

判断：

- load submit 是否早于 forward。
- 某些 layer 的 load 是否与前面 layer compute 重叠。
- wait_for_layer_load 是否真正阻塞。

### 4.3 带宽压力实验

逐步增大同时 load 的请求数或 DDR hit tokens：

- 如果 TTFT 增量近似线性增加，说明带宽共享/排队成为主要瓶颈。
- 如果 TTFT 增量小于线性模型，说明 compute 覆盖了部分 load。
- 如果 compute utilization 降低且 waiting 状态增加，说明 overlap 被打破。

## 5. 源码确认：真实传输路径

### 5.1 vLLM CPU offload 路径

源码路径：

- `/home/zhangxiyue/vllm/vllm/v1/simple_kv_offload/worker.py`
- `/home/zhangxiyue/vllm/vllm/v1/simple_kv_offload/copy_backend.py`
- `/home/zhangxiyue/vllm/vllm/v1/simple_kv_offload/cuda_mem_ops.py`

确认结果：

- CPU KV tensor 分配在 CPU。
- 如果可用，会通过 `cudaHostRegister` pin CPU memory。
- 使用 `DmaCopyBackend` 后台线程提交 `cuMemcpyBatchAsync`。
- 分 load stream 和 store stream。
- load/store 都按 block id 列表批量提交。

链路口径：

```text
CPU pinned memory -> GPU paged KV buffer
  via CUDA cuMemcpyBatchAsync / DMA / PCIe or platform equivalent
```

这是 GPU vLLM 路径，不是 Ascend NPU 路径。

### 5.2 vLLM MooncakeConnector 路径

源码路径：

- `/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py`

确认结果：

- connector 初始化 `TransferEngine`。
- `mooncake_protocol` 默认是 `rdma`。
- register 阶段记录每个 KV tensor 的 base address 与每 block byte length。
- receiver 通过 bootstrap / ZMQ 拿到远端 worker 地址。
- sender 用 block ids 生成 `src_ptrs` / `dst_ptrs` / `lengths`。
- contiguous block 可以 coalesce 成更大的 transfer descriptor。
- 最终调用 `engine.batch_transfer_sync_write(remote_session, src_ptrs, dst_ptrs, lengths)`。

链路口径：

```text
producer paged KV buffer
  -> Mooncake TransferEngine
  -> protocol=rdma by default
  -> consumer paged KV buffer
```

这里不是 DDR/CPU 单实例池化，而是 P/D 或跨 engine KV transfer。

### 5.3 vLLM-Ascend CPU / Store pooling 路径

源码路径：

- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/ascend_store_connector.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/pool_worker.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/kv_transfer.py`
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/backend/mooncake_backend.py`

确认结果：

- `AscendStoreConnector` 是 vLLM connector 接口实现。
- `KVPoolWorker` 根据模型、TP、PCP、DCP、PP、MLA/sparse 信息构造 key metadata。
- backend 可以是 mooncake / memcache / yuanrong。
- Mooncake backend 使用 `MooncakeDistributedStore`。
- protocol 默认来自 `MOONCAKE_CONFIG_PATH`，vLLM-Ascend 示例里常见 `protocol=ascend`。
- 若没有 `ASCEND_ENABLE_USE_FABRIC_MEM=1`，会通过全局 transfer engine 注册 buffer。
- `put/get` 使用 `batch_put_from_multi_buffers` / `batch_get_into_multi_buffers`。

链路口径：

```text
Ascend/NPU KV buffer address list
  -> AscendStoreConnector / KVPoolWorker
  -> MooncakeDistributedStore backend
  -> protocol=ascend or configured backend protocol
  -> registered buffer / Mooncake store segment
  -> get into local KV buffer
```

这里更像“池化存储接口”，底层可能是 Ascend fabric/HIXL/Mooncake TransferEngine，而不是普通 CPU memcpy。

### 5.4 HCCL 是否是 KV load 主链路

本次没有看到 HCCL 作为 AscendStore KV get/put 的主 API。HCCL 主要出现在 vLLM-Ascend 的分布式通信、MoE、环境变量与 process group 相关代码中。

当前判断：

- 单实例 DDR/CPU pooling 的 KV load 主链路更接近 backend get/put 或 DMA copy。
- P/D 或跨实例 KV transfer 可能使用 Mooncake TransferEngine / RDMA / Ascend protocol。
- HCCL 可能影响并行通信、MoE、集体通信和部分部署，但不是本次源码中 AscendStore KV load 的直接调用接口。

## 6. 源码确认：load 粒度

结论：逻辑 lookup 是 prefix token/block 级；真实传输提交通常是 batch of blocks / regions；layerwise 模式可细化到 layer。

### 6.1 vLLM CPU offload

- lookup：按 block hash 找连续 prefix hit。
- scheduler state：`num_external_tokens` 转成 `num_blocks_to_load`。
- metadata：把多个 request 的 `gpu_block_ids` / `cpu_block_ids` 合并到一次 load event。
- copy：`copy_blocks(src_block_ids, dst_block_ids, params)` 生成 `n * num_layers` 个 memcpy descriptor。

因此：

```text
业务语义：block / token-prefix
传输提交：一个 scheduler step 内的 batch of block copies
底层 copy descriptor：block x layer/tensor
```

### 6.2 vLLM MooncakeConnector

- request metadata 里有 `local_block_ids`。
- `_build_transfer_params()` 按 request、block group、KV tensor region 生成 src/dst/length。
- 连续 block 可 coalesce。

因此：

```text
业务语义：request prefix blocks
传输提交：多个 request 的 transfer descriptors
底层 descriptor：region pointer + length
```

### 6.3 vLLM-Ascend AscendStoreConnector

非 layerwise：

- `process_tokens(token_len, block_hashes, mask_num)` 把 token prefix 拆为 cache keys。
- `prepare_value(start, end, block_ids)` 生成地址与 size。
- `m_store.get(key_list, addr_list, size_list)` 批量 get。

layerwise：

- key 可以 `split_layers(num_layers)`。
- 每层生成 layer-major keys。
- `wait_for_layer_load()` 推进 `retrieve_layer()`。

因此：

```text
非 layerwise：batch of token-block/chunk keys
layerwise：layer x block/chunk keys
```

你提出的“load 粒度很可能是 batch”是正确的，但应精确表述为：

```text
上层命中统计是 prefix block/token；
传输提交通常把一个 step 内的多个 request、多个 block、多个 region 合并成 batch；
某些实现再细分到 layer。
```

## 7. 多请求同时 load 时：共享带宽、独立 stream，还是优先级调度

源码中能确认的部分：

1. vLLM CPU offload：
   - 有独立 `load_stream` / `store_stream`。
   - stream priority 设为 low priority，让 KV I/O 让位于 compute stream。
   - 后台线程从 queue 中取任务，提交 `cuMemcpyBatchAsync`。
   - 多请求 load 在 `build_connector_meta()` 中合并到同一个 `load_event`。

2. vLLM MooncakeConnector：
   - sender 侧有 `ThreadPoolExecutor(max_workers=num_sender_workers)`。
   - `num_sender_tasks = num_sender_workers * 2`，用于保持线程池饱和。
   - receiver 侧按 remote engine / TP rank 创建 async task。
   - 每个 transfer 最终通过同一个 `TransferEngine` 提交。

3. vLLM-Ascend AscendStore：
   - `KVTransferThread` 使用 request queue。
   - thread 内部有 `ThreadPoolExecutor(max_workers=32)`，但具体 get/put 是否使用 executor 取决于子类路径。
   - 非 layerwise + load_async 使用 recv thread；非 async 直接同步 get。
   - layerwise 通过 generator 和 event 在层间推进。

当前无法从这些源码完全确认底层带宽分配策略。更保守的判断是：

- 应用层有独立线程/stream/queue。
- 底层链路带宽大概率是共享资源。
- 有些路径有 low-priority stream，但没有看到跨 request 的显式优先级调度策略。
- Mooncake / Ascend backend 内部可能还有自己的调度和限流，需要完整 Mooncake / backend 源码或运行指标确认。

因此 InferTwin Step8 v1 不应该假设每个 request 独享带宽。推荐先采用：

```text
shared_link_sum:
  一个 iteration 内的 ddr_hit_bytes 求和后计算 kv_load_ms
```

后续如果要做更真实的并发带宽建模，再新增：

```text
KVLoadQueueModel / SharedBandwidthModel / PerTierTransferScheduler
```

## 8. 完整通信链路整理

### 8.1 单实例 CPU/DDR pooling

```text
scheduler lookup external/CPU prefix hit
  -> allocate GPU/HBM target blocks
  -> build connector metadata
  -> worker start_load_kv
  -> CPU/DDR/backend buffer -> GPU/NPU KV buffer
  -> worker reports finished_recving
  -> scheduler moves request from WAITING_FOR_REMOTE_KVS to WAITING
  -> request can run prefill/decode with loaded KV
```

### 8.2 P/D 或跨 engine Mooncake transfer

```text
router / prefill output gives kv_transfer_params
  -> decoder scheduler sees do_remote_prefill
  -> allocate local target blocks
  -> request enters WAITING_FOR_REMOTE_KVS
  -> worker queries remote bootstrap / side channel
  -> producer prepares src ptrs, consumer provides dst ptrs
  -> Mooncake TransferEngine batch transfer
  -> worker finished_recving
  -> scheduler caches blocks and resumes request
```

### 8.3 vLLM-Ascend AscendStore pooling

```text
lookup key server / KVPoolScheduler computes kvpool_cached_tokens
  -> scheduler allocates request blocks
  -> AscendConnectorMetadata carries ReqMeta/load_spec
  -> KVPoolWorker.start_load_kv
  -> token_database maps token range + block ids -> keys + addrs + sizes
  -> MooncakeDistributedStore / backend get into local KV buffer
  -> sync or async finished_recving
```

## 9. 对 InferTwin Step8 的设计影响

### 9.1 Step8 v1 可以先做线性 KV-load component

源码说明真实路径很复杂，但对于离线仿真器，第一版不应引入真实 backend。更稳妥的抽象是：

```text
kv_load_ms =
  fixed_overhead_ms
  + ddr_hit_tokens * ms_per_token
```

或：

```text
kv_load_ms =
  fixed_overhead_ms
  + ddr_hit_bytes * ms_per_byte
```

其中 `ms_per_token` / `ms_per_byte` 可通过真实实验、Ramulator2 或 Mooncake 压测拟合。

### 9.2 Step8 v1 不建议默认建模 overlap

原因：

- 真实 overlap 与 async/sync、layerwise、stream priority、链路带宽、batch composition 有关。
- 当前 InferTwin prefill latency 仍是 fitted TTFT function，不是真实 layer-level compute timeline。
- 如果没有 compute chunk timeline，强行扣除 overlap 容易制造虚假精度。

建议 Step8 v1 先做保守口径：

```text
iteration_duration_ms =
  prefill_compute_ms
  + kv_load_ms
```

并在 result details 中保留：

```text
kv_load_overlap_mode = "none_v1"
```

后续可新增：

```text
overlap_mode:
  none_v1
  max_compute_or_load_v1
  layerwise_pipeline_v2
  measured_profile_v2
```

### 9.3 zero-miss DDR request 不能 immediate finish

如果 `miss_tokens == 0` 但 `ddr_hit_tokens > 0`，真实系统仍需要把 KV load 到可用的 local KV buffer，或者至少等待外部 connector 报告完成。

所以 Step8 必须把它作为 load-only path：

```text
finish_time = now + kv_load_ms
```

HBM-only zero-miss 才能继续 immediate finish。

### 9.4 load 粒度应从 iteration batch shape 开始

结合源码，Step8 v1 的最合适仿真粒度是：

```text
BatchShape.kv_load_tokens
BatchShape.kv_load_bytes
BatchShape.kv_load_request_count
```

而不是一开始就做：

```text
per layer transfer descriptor
per page memory request
per DMA stream queue
```

原因：

- vLLM / vLLM-Ascend 最终确实会批量提交多个 request/block 的 load。
- InferTwin 的 scheduler iteration 是当前稳定的 replay 时间单位。
- 这能避免 Step8 过早绑定某个底层 backend。

## 10. Mooncake 源码二次确认

已将 Mooncake 开源源码 clone 到：

```text
/home/zhangxiyue/Mooncake
```

本节只沉淀对 Step8 KV load 建模有影响的结论，不表示 InferTwin 要直接调用 Mooncake。

### 10.1 `batch_get_into_multi_buffers()` 如何切分 request

源码路径：

- `/home/zhangxiyue/Mooncake/mooncake-integration/store/store_py.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-store/src/real_client.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-store/src/client_service.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-store/src/transfer_task.cpp`

确认结果：

1. Python binding `MooncakeDistributedStore.batch_get_into_multi_buffers(keys, all_buffer_ptrs, all_sizes, ...)` 释放 GIL 后调用 `RealClient::batch_get_into_multi_buffers(...)`。
2. `RealClient` 先对所有 key 做 `BatchQuery(keys)`，拿到每个 key 的 replica list。
3. 每个 key 独立选择 best replica，优先级是：

```text
local MEMORY -> remote MEMORY / NOF -> LOCAL_DISK -> DISK
```

4. memory replica 会被整理成 `valid_operations`，每个 key 的多 buffer 目标会变成一组 `Slice{ptr, size}`，再进入 `Client::BatchGet(...)`。
5. `Client::BatchGet(...)` 会对每个 key 提交 `TransferFuture`；`prefer_alloc_in_same_node=true` 时会按 segment 聚合后提交 batch。
6. `TransferSubmitter` 会把每个 slice 变成 `TransferRequest`，再通过 `TransferEngine.submitTransfer(batch_id, requests)` 提交。
7. RDMA transport 还会继续按 `globalConfig().slice_size` 切 slice，并受 `fragment_limit`、`max_wr * num_qp_per_ep` watermark、worker shard、peer NIC path queue 影响。
8. LOCAL_DISK 不是普通 DISK：
   - LOCAL_DISK 先通过 offload RPC 在 owner 侧 `BatchGet` 出 offload object buffer。
   - 然后调用 `Client::BatchGetOffloadObject(...)`，仍通过 TransferEngine 把 owner 侧临时 buffer scatter 到用户传入的多 buffer。
9. DISK 路径会批量 `BatchGet` 到 CPU temp buffer，再 scatter 到用户 buffer；如果目标是 device pointer，会走 host-to-device copy。

因此，Mooncake 内部的真实粒度不是“一个 request 一次 load”，而是：

```text
batch keys
  -> per-key replica selection
  -> per-key multi-buffer slices
  -> TransferRequest list
  -> transport-specific slice / fragment / queue
```

对 InferTwin 的影响：

- Step8 v1 可以先按 iteration 汇总 `kv_load_bytes` / `kv_load_tokens`。
- 后续如果做细粒度通信仿真，应把 load shape 扩展到 request-slice 或 block-slice，而不是直接绑定 request 粒度。

### 10.2 TransferEngine 是否有显式带宽共享、队列优先级、限流

源码路径：

- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/multi_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/rdma_transport/rdma_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/rdma_transport/worker_pool.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/include/config.h`

确认结果：

- `MultiTransport.submitTransfer()` 只负责按 target segment protocol 选择 transport，并把 task list 交给对应 transport。
- RDMA transport 会按 `slice_size` / `fragment_limit` 拆分，并按 `(target_id, device_id)` hash 到 worker shard。
- RDMA worker 内部维护 `slice_queue_` / `collective_slice_queue_`，再按 peer NIC path 提交到 endpoint。
- 配置中存在 `fragment_limit`、`slice_timeout`、`max_wr`、QP/worker 数、IB traffic class / service level 等参数。
- 没看到面向 request、tenant、key 的显式带宽共享模型、优先级队列或 token-bucket 限流。
- HCCL / Ascend direct / RDMA 的真实带宽共享主要来自底层链路、worker queue、QP、stream、fabric 和硬件竞争，而不是 Store client 暴露的统一调度策略。

对 InferTwin 的影响：

- Step8 v1 不应假设“每个请求独享带宽”。
- 也不应假设 Mooncake 有一个可直接照搬的跨 request 公平调度器。
- 第一版适合做 conservative shared-link model：

```text
iteration 内按 tier 汇总 bytes，再用 fitted bandwidth / ms_per_byte 得到 kv_load_ms
```

后续若需要更真实，应新增：

```text
KVLoadQueueModel
SharedBandwidthModel
TransferScheduler
```

### 10.3 `protocol=ascend` 的真实路径

源码路径：

- `/home/zhangxiyue/Mooncake/mooncake-wheel/mooncake/mooncake_config.py`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/multi_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/hccl_transport/hccl_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/hccl_transport/ascend_transport_c/hccl_transport_mem_c.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/heterogeneous_rdma_transport/heterogeneous_rdma_transport.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_allocator.cpp`

确认结果：

- Python config 文档把 `ascend` 定义为 Huawei Ascend NPU communication，包含 HCCL 和 direct transport。
- `MultiTransport.installTransport("ascend")` 取决于编译宏：

```text
USE_ASCEND_DIRECT       -> AscendDirectTransport
USE_ASCEND              -> HcclTransport
USE_ASCEND_HETEROGENEOUS -> HeterogeneousRdmaTransport
```

- `AscendDirectTransport` 注册的 segment protocol 是 `ascend`，内部使用 ADXL engine；TENT 目录中也存在 HIXL 相关 Ascend direct transport，但不能据此把主 Store 路径一概说成 HIXL。
- `AscendDirectTransport` 只有在 `ascend_use_fabric_mem && ascend_store_te_init` 时才使用 fabric memory。
- `ubshmem` 是单独 protocol，且源码中明确要求 fabric memory，不等同于 `protocol=ascend`。
- `HcclTransport` 也注册为 `protocol=ascend`，内部使用 HCCL `TransportMem`；跨 HCCS 走 `ROCE`，同 HCCS 走 `IPC`。
- `HeterogeneousRdmaTransport` 会包装 RDMA transport，用于异构路径。

因此，`protocol=ascend` 不是单一路径。更准确的表达是：

```text
protocol=ascend
  -> build/config dependent
  -> Ascend direct / HCCL / heterogeneous RDMA
  -> may use fabric memory only under explicit fabric config
```

对 InferTwin 的影响：

- Step8 profile 不应只有 `protocol=ascend` 一个字符串。
- 至少需要把 `transfer_path` / `transport_kind` / `fabric_enabled` 作为未来 profile 字段。
- Step8 v1 可以先用 fitted `ms_per_byte` 抽象掉这些底层差异。

### 10.4 local DDR/CPU、remote DRAM、SSD fallback 是否共用同一个 TransferEngine 队列

确认结果：

- local same-process memory replica 会走 `LOCAL_MEMCPY` worker pool，不走 TransferEngine remote queue。
- remote MEMORY replica 走 TransferEngine。
- LOCAL_DISK 先通过 offload RPC 在 owner 侧读 SSD，再通过 TransferEngine 做 scatter-gather transfer。
- DISK replica 走 `BatchGet` 到 CPU temp buffer，再 scatter；如果目标是 device pointer，会 host-to-device copy。
- NoF/NVMe-oF 走 SPDK/NVMe-oF strategy，不等同于普通 TransferEngine memory transfer queue。

因此，不应认为 local DDR/CPU、remote DRAM、SSD fallback 都共用一个 TransferEngine 队列。更准确的抽象是：

```text
LOCAL_MEMCPY path
TRANSFER_ENGINE memory path
LOCAL_DISK offload-RPC + TRANSFER_ENGINE path
DISK file-read + scatter / H2D path
NOF / SPDK path
```

对 InferTwin 的影响：

- Step8 v1 的 `KVLoadLatencyComponent` 应按 tier/path 预留字段，而不是只建一个全局 `kv_load_ms_per_token`。
- 但第一版可以先只实现 DDR/CPU tier 的线性 load latency，并明确不建模 path-level queue。

### 10.5 put/get 是否有对象级 pin、lease、replica placement、eviction 影响 load latency

源码路径：

- `/home/zhangxiyue/Mooncake/mooncake-store/include/replica.h`
- `/home/zhangxiyue/Mooncake/mooncake-store/include/client_service.h`
- `/home/zhangxiyue/Mooncake/mooncake-store/include/master_service.h`
- `/home/zhangxiyue/Mooncake/mooncake-store/src/master_service.cpp`
- `/home/zhangxiyue/Mooncake/mooncake-store/include/master_config.h`

确认结果：

- `ReplicateConfig` 支持：
  - `replica_num`
  - `nof_replica_num`
  - `with_soft_pin`
  - `with_hard_pin`
  - `preferred_segments`
  - `preferred_nof_segments`
  - `prefer_alloc_in_same_node`
  - `group_ids`
- `ObjectMetadata` 维护 hard lease、soft pin timeout、hard pinned、replica list。
- `QueryResult` 带 lease timeout；`Client::BatchGet` 在 transfer 结束后仍会检查 lease 是否过期。
- `Replica` 有 refcnt；upsert 时如果 replica busy，会返回 `OBJECT_REPLICA_BUSY`，避免读写冲突。
- Master 侧 allocation strategy 会受 preferred segment、replica count、NoF replica、SSD-aware allocation 等影响。
- Master config 中有 offload-on-evict、promotion-on-hit、offloading queue limit、promotion queue limit。
- 读 LOCAL_DISK-only key 可触发 promotion-on-hit，使后续 get 变快。
- eviction 时会考虑 lease、soft pin、hard pin、refcnt、LOCAL_DISK/offload 状态。

因此，真实 load latency 会受到对象生命周期与 placement 影响：

```text
replica 在哪里
是否 local / remote
lease 是否还有效
是否 hard/soft pinned
是否正在被读写
是否触发 offload / promotion
eviction 是否改变可见 replica
```

对 InferTwin 的影响：

- Step8 v1 不应试图一次建模全部对象级生命周期。
- Step8 应只把 `kv_load_ms` 加进 replay timeline。
- placement、pin、lease、promotion、offload-on-evict 应作为未来更细粒度 cache manager / storage simulator 的扩展项。

## 11. Step8 后续建议

Step8 仍建议先按当前技术路线推进：

1. 先把 KV-load shape schema 做清楚。
2. 新增 token-linear / byte-linear KVLoadLatencyComponent。
3. 保守采用 no-overlap 加和模型。
4. 输出 `kv_load_ms`、`kv_load_tokens`、`kv_load_bytes`。
5. 把 Ramulator2 / Mooncake / 实机压测作为 calibration source，而不是 replay 主路径。

后续当 Step9 引入 progressive chunk/block visibility 和 chunk-level prefill timeline 后，再评估：

```text
compute/load overlap model
layerwise pipeline load model
shared bandwidth queue model
promotion after load completion
```
