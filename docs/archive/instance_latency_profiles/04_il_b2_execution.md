# Batch IL-B2 执行记录：InstanceLatencyProfile kv_load Schema Extension

执行时间：2026-06-26

任务类型：核心仿真器 schema 修复。

状态：已完成。

## 1. 本批目标

Batch IL-B2 在进入 IL-C runner integration 之前，补齐 `InstanceLatencyProfile` 对 KV load latency 超参数的表达。

HitFloor 的请求级 TTFT 长期语义是：

```text
request TTFT =
  queue_waiting_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

当前版本：

```text
queue_waiting_ms = 0
uncached_prefill_compute_ms = fitted_ttft(uncached_tokens)
kv_load_ms = 0
```

其中：

- `queue_waiting_ms` 不是静态配置超参数，暂不进入实例 latency profile。
- `uncached_prefill_compute_ms` 由 `fitted_ttft` 控制。
- `kv_load_ms` 未来由 DDR hit tokens 和 remote hit tokens 决定；当前 HBM-only replay 下固定为 0。

## 2. 代码修改

修改：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/__init__.py
tests/unit/config/test_instance_latency_profiles.py
configs/instances/local-fixed-route-latency-example.yaml
```

## 3. 新增 Schema

新增：

```python
@dataclass(frozen=True, slots=True)
class KVLoadLatencyProfile:
    ddr_ms_per_cached_token: float = 0.0
    remote_ms_per_cached_token: float = 0.0
```

扩展：

```python
@dataclass(frozen=True, slots=True)
class InstanceLatencyProfile:
    ...
    kv_load: KVLoadLatencyProfile = field(default_factory=KVLoadLatencyProfile)
```

配置示例：

```yaml
kv_load:
  ddr_ms_per_cached_token: 0.0
  remote_ms_per_cached_token: 0.0
```

## 4. 核心语义

`kv_load` 表达非 HBM cache hit 的 load latency 超参数。

未来多级缓存和跨实例命中接入后，稳定口径是：

```text
kv_load_ms =
  ddr_hit_tokens * ddr_ms_per_cached_token
  + remote_hit_tokens * remote_ms_per_cached_token
```

当前不接入 replay 计算，原因：

- 目前核心 replay 只实现 HBM 命中。
- 当前 result schema 没有 DDR / remote hit tokens。
- 提前把 `kv_load` 接入 TTFT 会制造永远为 0 的伪链路，不利于后续评审。

## 5. Backward Compatibility

未声明 `kv_load` 的旧实例 latency profile 仍可解析。

默认值：

```text
ddr_ms_per_cached_token = 0.0
remote_ms_per_cached_token = 0.0
```

## 6. Fail-fast 规则

已实现：

- `ddr_ms_per_cached_token < 0` 会失败。
- `remote_ms_per_cached_token < 0` 会失败。
- 非数字值会失败。

## 7. 测试覆盖

扩展：

```text
tests/unit/config/test_instance_latency_profiles.py
```

覆盖：

- 显式 `kv_load` 解析。
- 未声明 `kv_load` 时默认两个超参数均为 0。
- 负值 `kv_load` hyperparameter fail-fast。

## 8. 收口结论

Batch IL-B2 已完成。

当前完成：

```text
InstanceLatencyProfile can express fitted TTFT and future KV load latency knobs
```

仍未完成：

```text
Streaming runner selects per-instance backend
DDR / remote hit tokens participate in TTFT materialization
```

下一批继续进入 Batch IL-C。
