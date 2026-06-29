# S8-C 实施方案：Instance / Model Resolver Integration

状态：待用户评审，尚未进入代码开发。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-C：Instance / Model Resolver Integration。

前置条件：

- S8-A 已完成 `BatchShape` / `ShapeKey` 的 KV load shape。
- S8-B 已完成 `KVLoadLatencyComponent` 和 `KVLoadLatencyProfile` 显式 schema。

## 1. 类型与改动等级

本 Batch 属于核心仿真器。

改动等级：L3。

原因：

- 本 Batch 修改 `InstanceLatencyBackendResolver.backend_for(instance_uuid)` 返回的 concrete backend。
- instance/model profile 的 `kv_load` 将正式进入 replay-facing latency backend composition。
- true streaming path 会继续通过 resolver 获取 per-instance backend，因此本 Batch 会影响大 trace 主路径的 backend 类型。

但本 Batch 不修改 scheduler/replay/cache，也不让真实 replay 产生非零 `kv_load_tokens`。非零 KV load shape 接入仍属于 S8-D。

## 2. 本 Batch 做什么

S8-C 只做 instance/model latency resolver 集成：

1. 将 `InstanceLatencyProfile` 构造成 `ServingLatencyProfile`，而不是直接构造成 `FittedTTFTLatencyBackend`。
2. `ServingLatencyProfile.ttft_backend` 继续使用原来的 fitted TTFT 参数。
3. `ServingLatencyProfile.kv_load_component` 由 `InstanceLatencyProfile.kv_load` 构建。
4. instance profile 和 model default latency 使用同一构造逻辑。
5. 保持 resolver 优先级不变：

```text
instance profile -> model default -> legacy global backend
```

6. 保持 global backend fallback 不变：未配置 `instance_latency` 时，仍直接使用 `build_batch_latency_backend(config)`。
7. 更新 resolver 测试，验证：
   - instance profile 可以返回带 KV load component 的 serving profile。
   - model default 可以返回带 KV load component 的 serving profile。
   - 缺失实例仍 fail-fast。
   - legacy global fallback 仍保持现有行为。

S8-C 完成后，手动构造非零 KV load shape 并调用 `resolver.backend_for(instance).estimate_iteration(shape)` 时，实例级或模型默认的 KV load 超参数会生效。

## 3. 本 Batch 不做什么

S8-C 不做：

- 不修改 scheduler。
- 不修改 replay event loop。
- 不把 `ddr_hit_tokens` 写入 `ScheduledSlice.kv_load_tokens`。
- 不修正 zero-miss DDR load-only path。
- 不修改 cache lookup / materialization / eviction。
- 不修改 request metrics / iteration metrics / capacity sweep typed result。
- 不修改 report/export。
- 不接 Ramulator2 / Mooncake online replay。
- 不建模 load queue / backpressure。
- 不建模 compute/load overlap。
- 不建模 layerwise KV load。
- 不建模 DDR hit promotion、load completion event。
- 不建模 remote KV / SSD / cross-instance pooling。
- 不修改 `streaming/sweep.py`，除非测试证明现有 `BatchLatencyBackend` contract 无法承载 `ServingLatencyProfile`。

如果开发中发现必须修改上述内容，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/latency/instance_resolver.py`

职责：

- 根据 `instance_uuid` 解析 per-instance latency backend。
- 处理 instance profile、model default 和 global fallback。

计划修改：

- 引入：

```python
from infertwin.latency.kv_load import build_kv_load_component
from infertwin.latency.profile import ServingLatencyProfile
```

- 将 `_build_instance_latency_backend(profile)` 的返回值从 `FittedTTFTLatencyBackend` 调整为 `ServingLatencyProfile`：

```text
ServingLatencyProfile(
  profile=<profile.name or fitted profile-derived name>,
  ttft_backend=FittedTTFTLatencyBackend(...),
  kv_load_component=build_kv_load_component(profile.kv_load),
  calibrated_from=<profile.fitted_ttft.calibrated_from>,
  calibration_window_requests=<profile.fitted_ttft.calibration_window_requests>,
)
```

建议命名：

```text
ServingLatencyProfile.profile = f"{profile.name}_serving"
```

或直接使用 `profile.name`，但 result details 中应可追踪原 latency profile。代码开发前建议选择简单口径：使用 `profile.name`，因为它是用户配置的 instance/model latency profile 名称。

边界：

- 不修改 `_latency_profile_for_instance()` 优先级。
- 不修改 `LatencyResolutionMetadata` 字段。
- 不改变 global fallback。

### 4.2 `tests/unit/latency/test_instance_resolver.py`

职责：

- 覆盖 explicit instance profile path。

计划修改：

- 将返回类型断言从 `FittedTTFTLatencyBackend` 更新为 `ServingLatencyProfile`。
- 继续检查：
  - `backend.profile`
  - `backend.model_name`
  - `backend.hardware_name`
  - `backend.ttft_backend.ms_per_uncached_token`
  - `resolver.metadata_for(...).source`
  - resolver cache identity。
- 新增/扩展测试，写入显式 `kv_load`：

```yaml
kv_load:
  mode: token_linear_v1
  ddr_fixed_overhead_ms: 1.0
  ddr_ms_per_cached_token: 0.1
  calibrated_from: instance-profile-kv-load
```

- 手动构造 `BatchShape(kv_load_tokens > 0)`，验证 `backend.estimate_iteration(...)` 中 `kv_load_ms` 生效。

### 4.3 `tests/unit/latency/test_instance_resolver_model_defaults.py`

职责：

- 覆盖 model default latency fallback。

计划修改：

- 将返回类型断言从 `FittedTTFTLatencyBackend` 更新为 `ServingLatencyProfile`。
- instance-a 使用 explicit instance profile。
- instance-b 使用 model default latency。
- 在 registry default latency 中加入显式 `kv_load`。
- 手动构造 `BatchShape(kv_load_tokens > 0)`，验证 instance-b 使用 model default KV load 超参数。

### 4.4 `tests/unit/latency/test_kv_load_latency.py`

职责：

- 已覆盖 component 本身。

计划：

- 原则上不修改。
- 如果 S8-C 发现 builder details 需要更稳定断言，可补一条不改变 replay 的 builder 回归。

### 4.5 `tests/integration/test_true_streaming_capacity_sweep_runner.py`

职责：

- 覆盖 streaming capacity sweep 主路径。

计划：

- 原则上不修改 golden 或输出字段。
- 可新增一个小型回归：使用 instance/model profiles 中显式 `mode=zero` 或 all-zero legacy `kv_load`，确认 streaming sweep 仍通过。
- 若现有测试已经覆盖 all-zero profile，不新增。

### 4.6 不计划修改的文件

明确不修改：

```text
src/infertwin/replay/event_loop.py
src/infertwin/streaming/replay.py
src/infertwin/streaming/sweep.py
src/infertwin/scheduler/*
src/infertwin/cache/*
src/infertwin/report/*
```

如果开发中发现必须修改这些文件，应暂停并重新评审。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 Resolver 返回 concrete backend

外部接口不变：

```python
def backend_for(self, instance_uuid: str) -> BatchLatencyBackend:
    ...
```

但 concrete 类型变化：

```text
S8-B 之前:
  instance/model profile -> FittedTTFTLatencyBackend

S8-C 之后:
  instance/model profile -> ServingLatencyProfile
```

global fallback 不变：

```text
无 instance_latency:
  build_batch_latency_backend(config)
```

因此，如果全局 config 是 `fitted_ttft`，`resolver.backend_for()` 仍可返回 `FittedTTFTLatencyBackend`。

### 5.2 `ServingLatencyProfile` composition

对 instance/model profile：

```text
ServingLatencyProfile.ttft_backend = FittedTTFTLatencyBackend(profile.fitted_ttft)
ServingLatencyProfile.kv_load_component = build_kv_load_component(profile.kv_load)
ServingLatencyProfile.queue_component = default zero
```

不新增新的 schema。S8-C 消费 S8-B 已定义的 `KVLoadLatencyProfile`。

### 5.3 Metadata

`LatencyResolutionMetadata` 暂不新增字段。

原因：

- 当前 metadata 只解释 source / calibration_status / model_name。
- KV load details 会进入 `LatencyResult.details`，例如 `kv_load_mode`、`kv_load_calibrated_from`。
- 如果未来 report 需要直接列出每实例 KV load source，可在 S8-E typed metrics/report 阶段扩展。

## 6. 核心算法逻辑

### 6.1 Resolver path

当前逻辑保持：

```text
backend_for(instance_uuid):
  if no instance_profile:
    return global_backend

  if cached backend exists:
    return cached backend

  profile, source = _latency_profile_for_instance(instance_uuid)
  backend = _build_instance_latency_backend(profile)
  cache backend and metadata
  return backend
```

### 6.2 Profile 到 backend 构造

伪代码：

```text
def _build_instance_latency_backend(profile):
  if profile.backend != "fitted_ttft":
    fail

  fitted = profile.fitted_ttft
  ttft_backend = FittedTTFTLatencyBackend(
    profile=fitted.profile,
    function=fitted.function,
    intercept_ms=fitted.intercept_ms,
    ms_per_uncached_token=fitted.ms_per_uncached_token,
    calibrated_from=fitted.calibrated_from,
    model_name=profile.model_name,
    hardware_name=profile.hardware_name,
  )

  return ServingLatencyProfile(
    profile=profile.name,
    ttft_backend=ttft_backend,
    kv_load_component=build_kv_load_component(profile.kv_load),
    calibrated_from=fitted.calibrated_from,
    calibration_window_requests=fitted.calibration_window_requests,
  )
```

### 6.3 Model default path

现有 model default path 不变：

```text
if instance has no latency_profile and model_registry exists:
  profile = model_registry.entry_for(instance.model_name).default_latency
```

然后走同一个 `_build_instance_latency_backend(profile)`。

因此 model default 的 `kv_load` 自动接入，不需要单独分支。

### 6.4 global fallback path

保持不变：

```text
if instance_latency.profile_path is None:
  return global_backend
```

如果用户希望 global fallback 也有 KV load component，可以使用 S8-B 已支持的：

```yaml
latency:
  backend: serving_latency_profile
  serving_latency_profile:
    kv_load:
      mode: token_linear_v1
      ...
```

## 7. 对核心 replay 语义的影响

### 7.1 是否改变 cached_tokens

不改变。

S8-C 不修改 cache lookup、cached token accounting 或 block conversion。

### 7.2 是否改变 hbm_hit_tokens / ddr_hit_tokens / miss_tokens

不改变。

S8-C 只改变 latency backend composition，不产生或修改 tier hit accounting。

### 7.3 是否改变 finish_time / ttft_ms

默认真实 replay不改变。

原因：

- S8-D 尚未把 `ddr_hit_tokens` 写入 `ScheduledSlice.kv_load_tokens`。
- 现有 scheduler 仍生成 `kv_load_tokens=0`、`kv_load_bytes=0`。
- 即便 resolver 返回 `ServingLatencyProfile`，KV load component 在 zero shape 下返回 0。

手动构造非零 KV load shape 时，instance/model profile 的 KV load 参数会影响 duration，这是 S8-C 的预期能力。

### 7.4 是否改变 cache event 顺序

不改变。

S8-C 不修改 cache backend、event sink 或 materialization。

### 7.5 是否改变 materialization timing

不改变。

finish-time materialization 保持不变。

### 7.6 是否改变实例隔离

增强但不改变边界。

每个 instance 的 latency backend cache 仍在 resolver 内按 `instance_uuid` 管理：

```text
_backend_by_instance[instance_uuid]
```

S8-C 只是让每个 instance backend 同时携带 TTFT 与 KV load component。不会引入跨实例共享带宽或跨实例 KV hit。

### 7.7 是否影响 true streaming 大 trace

会影响 true streaming 使用的 concrete latency backend 类型，但默认指标不变。

影响点：

- `StreamingCapacitySweepRunner` 通过 `latency_resolver.backend_for(shard.instance_uuid)` 获取 backend。
- backend concrete type 从 `FittedTTFTLatencyBackend` 变为 `ServingLatencyProfile`。
- 在 zero KV load shape 下，duration 应与原 fitted TTFT 相同。
- 大 trace 不增加内存持有，不改变 request shard / event sink。

需要测试确认：

- streaming sweep 仍通过。
- instance profile / model default fallback 下 TTFT 数值不因 zero KV load 改变。

## 8. 测试计划

### 8.1 单测

修改：

```text
tests/unit/latency/test_instance_resolver.py
tests/unit/latency/test_instance_resolver_model_defaults.py
```

覆盖：

- explicit instance profile 返回 `ServingLatencyProfile`。
- explicit instance profile 的 `ttft_backend` 参数保持原值。
- explicit instance profile 的 `kv_load_component` 在非零 shape 下产生预期 `kv_load_ms`。
- model default fallback 返回 `ServingLatencyProfile`。
- model default fallback 的 KV load 参数生效。
- missing instance 仍 fail-fast。
- no instance profile 时 global fallback 行为不变。

可选新增：

```text
tests/unit/latency/test_instance_resolver_kv_load.py
```

如果现有 resolver 测试已经足够长，建议新增独立测试文件，避免单文件继续膨胀。

### 8.2 集成测试

建议运行现有：

```text
tests/integration/test_step7_streaming_hbm_ddr_integration.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

如需要新增小型集成：

- 使用 instance/model latency profile 显式配置 `kv_load.mode=zero`。
- 跑 `StreamingCapacitySweepRunner`。
- 验证 rows 与 zero KV load 语义一致。

但 S8-C 不强制新增集成测试，因为真实非零 KV load 仍未进入 replay shape。

### 8.3 小 E2E

不新增完整 CLI E2E。

小 E2E 可由单测模拟：

```text
InstanceLatencyBackendResolver
-> backend_for(instance)
-> backend.estimate_iteration(BatchShape(kv_load_tokens > 0))
-> details["kv_load_ms"] == expected
```

真实 trace E2E 延后到 S8-D/S8-E。

### 8.4 是否需要 golden 更新

不需要更新 CSV golden。

原因：

- 不修改 report/export 字段。
- 不修改 capacity sweep row schema。
- streaming replay 的 zero KV load shape 下 TTFT 应保持不变。

可能需要更新的只有 resolver 单测中的 concrete type 断言和 backend name 预期。

### 8.5 建议执行命令

开发后建议执行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_backend_factory.py
```

再运行 streaming 回归：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

如果 `.venv` 中已有 ruff：

```bash
.venv/bin/ruff check \
  src/infertwin/latency/instance_resolver.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py
```

最后执行：

```bash
git diff --check
```

## 9. 风险与回滚边界

### 9.1 风险

1. Resolver concrete backend 类型变化可能影响直接断言 `FittedTTFTLatencyBackend` 的测试或外部脚本。
2. `backend.name` 可能从 `fitted_ttft` 变为 `serving_latency_profile`，这会影响 iteration metrics 的 `backend` 和 `shape_key` 字符串，但 streaming sweep row 默认不暴露这些字段。
3. 如果 `remote_ms_per_cached_token` 非零，`build_kv_load_component()` 会 fail-fast；这符合 S8-B 决策，但可能暴露旧配置中的错误。
4. 如果 global fallback 路径也被强行改成 serving profile，会扩大行为变化；S8-C 明确不这样做。
5. 如果开发时为了修测试修改 streaming runner/report，就会越界。

### 9.2 回滚边界

S8-C 回滚范围：

- 回滚 `src/infertwin/latency/instance_resolver.py` 中 `_build_instance_latency_backend()`。
- 回滚 resolver 测试类型断言和 KV load 相关新增测试。

回滚后：

- S8-B 的 `KVLoadLatencyComponent` 和 profile schema 可以保留。
- S8-A 的 shape schema 可以保留。
- scheduler/cache/replay/report 不应受影响。

## 10. 完成后如何判断可以进入下一个 Batch

可以进入 S8-D 的条件：

1. `InstanceLatencyBackendResolver` 能从 instance profile 构造 `ServingLatencyProfile`。
2. model default fallback 能构造 `ServingLatencyProfile`。
3. instance/model profile 的 KV load component 能在非零 manual shape 下生效。
4. no instance profile 的 global fallback 行为不变。
5. missing instance / missing profile 仍 fail-fast。
6. true streaming 回归通过，确认 zero KV load shape 下结果稳定。
7. 未修改 scheduler、cache、replay event loop、streaming runner、report/export。
8. `ruff` 和 `git diff --check` 通过。

S8-C 完成后，S8-D 可以开始把 cache lookup 的 DDR hit split 写入 first scheduled slice，并修正 zero-miss DDR load-only path。

## 11. 需要用户审批的内容

请用户审批以下决定后，再进入 S8-C 代码开发：

1. 接受 S8-C 属于核心仿真器，改动等级为 L3。
2. 接受 S8-C 只做 resolver 集成，不接 replay 行为。
3. 接受 instance/model profile 的 concrete backend 从 `FittedTTFTLatencyBackend` 改为 `ServingLatencyProfile`。
4. 接受 global fallback 行为不变：未配置 `instance_latency` 时仍返回 global backend。
5. 接受 `ServingLatencyProfile.profile` 使用 `InstanceLatencyProfile.name`。
6. 接受 `ServingLatencyProfile.calibrated_from` 使用 `profile.fitted_ttft.calibrated_from`。
7. 接受 `ServingLatencyProfile.calibration_window_requests` 使用 `profile.fitted_ttft.calibration_window_requests`。
8. 接受 `LatencyResolutionMetadata` 暂不新增 KV load 字段。
9. 接受 backend name 可能从 `fitted_ttft` 变为 `serving_latency_profile`，但 zero KV load shape 下 duration 应保持不变。
10. 接受本 Batch 不修改 streaming runner、replay event loop、scheduler、cache、report/export。
11. 接受本 Batch 允许修改的文件范围仅限第 4 节列出的源码和测试文件。
12. 接受如果发现必须修改方案外文件，则暂停并重新评审。
