# Step7 调研：Mooncake Store 与 KVCache Pooling

状态：调研完成，供 Step7 技术路线评审使用。

## 1. 调研范围

本轮读取了本地 Mooncake 学习材料：

```text
/home/zhangxiyue/mooncake-store/mooncake-kvcache-pooling.md
```

并结合 vLLM MooncakeConnector 文档和源码：

```text
/home/zhangxiyue/vllm/docs/features/mooncake_connector_usage.md
/home/zhangxiyue/vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py
```

## 2. Mooncake 中“池化”的含义

Mooncake 的池化不是“多一块内存”，而是三类资源解耦后形成池：

- 算力池化：Prefill Pool / Decode Pool 分离。
- KVCache 池化：把多台机器的 DRAM / SSD 聚合成分布式 KVCache Store。
- 传输池化：RDMA / TCP / NVMe-of / GPUDirect / 多 NIC 统一成 Transfer Engine。

Mooncake 的核心思想是 KVCache-centric：调度、存储、传输都围绕 KVCache 位置和复用价值展开。

## 3. Mooncake Store 核心对象

Mooncake Store 对上提供类似对象存储接口：

```text
Put / Get / Remove
```

核心角色：

- Master Service：中心化元数据和分配器。
- Client：请求方和存储节点的双重身份。
- Segment：Client 贡献给全局池的一段连续内存。
- ReplicaInfo：对象副本位置。
- BufferAllocator：Segment 内部空间分配。
- AllocationStrategy：决定对象副本放置到哪个 Segment。

关键保护机制：

- Lease：读写期间阻止对象被 evict/remove。
- Soft Pin / Hard Pin：不同保留强度。
- Eviction：近似 LRU，高水位或分配失败时触发。
- Zombie object：PutStart 后长时间未 PutEnd 的对象可被回收。

## 4. Put / Get 与 Transfer Engine

Put 是两阶段：

```text
PutStart
-> Master 分配 ReplicaInfo / BufHandle
-> Client 使用 Transfer Engine 写入目标 Segment
-> PutEnd
-> 对象状态变为 COMPLETE，可读
```

Get：

```text
GetReplicaList
-> 获得副本列表和 lease
-> Transfer Engine 直接读取远端 Segment
-> 内存未命中时可回源 SSD / DFS
```

Transfer Engine 屏蔽底层协议：

- RDMA。
- TCP。
- NVMe-of。
- GPUDirect Storage。
- NVLink / HIP。
- AWS EFA。
- 多 NIC 条带化和 failover。

对 InferTwin 的启示：

- cache tier 与 transport / load latency 应分离。
- Step7 只建 tier hit/store/evict，不建真实 transfer。
- Step8 再把 load latency backend 接入，可参考 Transfer Engine 的“按 bytes / medium / protocol”建模。

## 5. vLLM MooncakeConnector 与 Mooncake Store 的区别

vLLM MooncakeConnector 是 vLLM 与 Mooncake Transfer Engine / P-D disaggregation 的连接层。

它负责：

- scheduler 侧识别 remote prefill tokens。
- worker 侧异步拉取或发送 KV。
- request finish 时决定是否 delay-free 本地 block。
- P/D 两侧用 `transfer_id` 关联同一请求。

它不等同于完整 Mooncake Store：

- Connector 是 vLLM runtime glue。
- Store 是全局分布式 KVCache 池和元数据服务。
- Step7 v1 不做 P/D 分离，不做跨实例 global store，不做 `transfer_id`。

## 6. Step7 与 Mooncake 的关系

Step7 的“pooling”只实现单实例池化：

```text
instance-local HBM
instance-local DDR/CPU KV cache tier
```

它不实现：

```text
cluster-global Mooncake Store
cross-instance hit
remote transfer
global conductor
replica placement
lease / pin
SSD fallback
```

但是 Step7 的抽象必须给未来 Mooncake-like 能力留入口：

- cache tier 不写死为 DDR，允许未来 `remote` / `ssd`。
- cache event 必须记录 tier。
- lookup result 必须区分 HBM / DDR / remote。
- latency profile 必须能按 tier 汇总 load tokens / bytes。
- backend 不应假设所有 hit 都是本地零成本。

## 7. Step7 可借鉴的 Mooncake 设计

可以借鉴：

- Tier 作为一等概念，而不是 capacity 字段。
- Store / lookup / evict 的状态机。
- Store completion 后对象才可读。
- Eviction 独立发生在每个 tier。
- Load 与 store 是不同方向的操作。
- Load 期间对象应受保护，避免被 eviction。

Step7 v1 暂不实现但需预留：

- lease / pin。
- async store/load。
- replica placement。
- remote owner / source instance。
- tier bytes / transfer bytes。
- store failed / load failed。

## 8. 对 Step7 产品边界的建议

Step7 v1 应命名为：

```text
batch_aware_hbm_ddr_lru
```

或类似显式名字，表达：

- batch-aware replay。
- HBM + DDR 两级 cache。
- 单实例隔离。
- LRU policy。
- finish-time materialization。

不建议把它叫 `mooncake` mode，因为它没有实现 Mooncake Store 的全局池、传输和 lease 机制。

未来可以新增：

```text
batch_aware_hbm_ddr_lru_with_kv_load
batch_aware_hbm_ddr_progressive
batch_aware_mooncake_remote_pool
```

每个 mode 都要保持旧 mode 语义不变。
