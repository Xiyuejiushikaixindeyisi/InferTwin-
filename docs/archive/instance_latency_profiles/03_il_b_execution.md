# Batch IL-B 执行记录：InstanceLatencyBackendResolver

执行时间：2026-06-26

任务类型：核心仿真器能力开发。

状态：已完成。

## 1. 本批目标

Batch IL-B 实现 `InstanceLatencyBackendResolver`。

本批只解决 latency backend 的选择接口：

```text
no instance_latency config
-> fallback to global latency backend

with instance_latency.profile_path
-> instance_uuid -> InstanceLatencyProfile -> fitted TTFT backend
```

本批不接入：

- `StreamingCapacitySweepRunner`。
- `BatchAwareReplayEngine`。
- `CapacitySweepRunner`。
- 动态 TTFT 重新拟合。

因此本批不会改变现有 replay、streaming sweep 或 capacity sweep 的运行结果。

## 2. 代码修改

新增：

```text
src/hitfloor/latency/instance_resolver.py
tests/unit/latency/test_instance_resolver.py
```

修改：

```text
src/hitfloor/latency/__init__.py
```

## 3. 新增接口

新增：

- `InstanceLatencyConfig`
- `InstanceLatencyBackendResolver`
- `build_instance_latency_config()`
- `build_instance_latency_backend_resolver()`

配置入口：

```yaml
instance_latency:
  profile_path: configs/instances/local-fixed-route-latency-example.yaml
  require_all_trace_instances: true
```

`instance_latency` 缺失时：

```text
resolver.backend_for(instance_uuid) -> global backend
```

`instance_latency.profile_path` 存在时：

```text
resolver.backend_for(instance_uuid)
-> lookup InstanceProfile.latency_profile_by_instance
-> build FittedTTFTLatencyBackend
```

## 4. 核心语义

第一版只支持实例级 `fitted_ttft` backend。

实例级 backend 与 deployment 解耦：

```text
same deployment can map to different latency profiles
```

原因：

- deployment 表示启动参数、模型配置和硬件/并行等配置。
- latency profile 表示某个实例自己的 TTFT 拟合参数。
- 多个实例共享配置时，由于实例侧请求分布和请求数量不同，拟合超参数不同是合理的。

`InstanceLatencyBackendResolver` 内部缓存已构造的实例 backend：

```text
same instance_uuid -> same backend object
```

这样后续 runner 可以稳定复用实例侧 backend，不需要每个 capacity 或每个 batch 反复构造。

## 5. Fail-fast 规则

已实现：

- `instance_latency` 必须是 mapping。
- `instance_latency.profile_path` 必须是非空字符串。
- `instance_uuid` 必须是非空字符串。
- 表存在但 trace instance 缺失时 fail-fast。
- `require_all_trace_instances=false` 暂时显式失败。
- unsupported instance latency backend 显式失败。

`require_all_trace_instances=false` 暂不实现 fallback 语义，原因是实例表一旦出现，就表示用户希望该实验使用实例级 TTFT 口径；静默退回全局 backend 会污染 TTFT 结果。

## 6. 测试覆盖

新增：

```text
tests/unit/latency/test_instance_resolver.py
```

覆盖：

- 无 `instance_latency` 时 fallback 到全局 fitted TTFT backend。
- 有实例表时，按 `instance_uuid` 返回不同 fitted TTFT backend。
- 同一 `instance_uuid` 重复 lookup 返回同一个 backend object。
- 表存在但实例缺失时 fail-fast。
- `instance_latency.profile_path` 缺失时 fail-fast。
- `require_all_trace_instances=false` 显式失败。

## 7. 收口结论

Batch IL-B 已完成。

当前能力：

```text
config + instance_uuid -> BatchLatencyBackend
```

仍未完成：

```text
StreamingCapacitySweepRunner uses resolver per instance shard
```

下一批建议进入 Batch IL-C：

```text
Streaming runner integration
```
