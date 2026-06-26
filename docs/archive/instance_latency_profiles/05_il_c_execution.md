# Batch IL-C 执行记录：Streaming Runner Integration

执行时间：2026-06-26

任务类型：核心仿真器能力开发。

状态：已完成。

## 1. 本批目标

Batch IL-C 将 `InstanceLatencyBackendResolver` 接入 `StreamingCapacitySweepRunner`。

目标语义：

```text
no instance_latency config
-> streaming runner keeps using global latency backend

with instance_latency.profile_path
-> each instance shard uses backend_for(shard.instance_uuid)
```

这让 true streaming capacity sweep 支持第一版 heterogeneous latency cluster replay。

## 2. 代码修改

修改：

```text
src/hitfloor/streaming/sweep.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

## 3. Replay 接入点

`StreamingCapacitySweepRunner.__init__()` 现在构造：

```text
InstanceLatencyBackendResolver
SchedulerConfig
```

每个 capacity replay 时，runner 对每个 shard 执行：

```text
shard.instance_uuid
-> latency_resolver.backend_for(instance_uuid)
-> StreamingBatchAwareReplayEngine(latency_backend=instance backend)
-> run_instance_stream(...)
```

## 4. Shape Memo 隔离

IL-C 对每个 shard 创建独立 `StreamingBatchAwareReplayEngine`。

原因：

- 同一 deployment 的不同实例允许拥有不同 TTFT 超参数。
- 当前 `ShapeKey` 包含 backend name、model name、hardware name 和 batch shape。
- 如果两个实例 model/hardware 相同但 TTFT slope 不同，跨实例共享 shape memo 会污染 latency 结果。

因此当前设计是：

```text
one instance shard -> one engine -> one shape memo
```

该设计不会改变 cache 隔离；每个 shard 仍使用自己的 HBM cache。

## 5. Config Metadata

`config_details` 新增：

```text
instance_latency_enabled
instance_latency_profile_path
instance_latency_profile_count
instance_latency_require_all_trace_instances
```

这些字段只用于外围 report / review 元数据，不参与 replay 计算。

## 6. Backward Compatibility

无 `instance_latency` 配置时：

```text
latency_resolver.backend_for(instance_uuid) -> global backend
```

旧 streaming sweep 与 batch sweep 对齐测试继续通过。

旧 `CapacitySweepRunner` 未修改。

## 7. Fail-fast 规则

当配置了 `instance_latency.profile_path`，但 trace shard 中出现实例表未声明的 `instance_uuid`：

```text
StreamingCapacitySweepRunner.run()
-> ValueError("instance latency profile missing ...")
```

这保证实例级 TTFT 口径不会静默混入全局 backend。

## 8. 测试覆盖

扩展：

```text
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

覆盖：

- 无 `instance_latency` 时 streaming runner 结果继续与 batch runner 一致。
- 有实例表时，`instance-a` / `instance-b` 使用不同 TTFT slope，instance-level P90 TTFT 按 slope 分开。
- 表存在但 trace 中实例缺失时 fail-fast。

## 9. 收口结论

Batch IL-C 已完成。

当前完成：

```text
true streaming fixed-routing multi-instance replay can select fitted TTFT backend by instance_uuid
```

当前仍不是：

```text
per-instance scheduler config
per-instance cache capacity
dynamic per-500-request refit
DDR / remote KV-load latency materialization
gateway routing simulation
```

这些能力需要后续独立 batch 或新 replay/cache mode 接入。
