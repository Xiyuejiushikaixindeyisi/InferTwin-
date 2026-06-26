# InstanceProfile / InstanceLatencyProfile 代码开发方案

## 1. 阶段定位

本阶段开发的是核心仿真器能力。

目标不是新增一个报表，而是改变 streaming replay 中 latency backend 的选择方式：

```text
before:
  all instances share one latency backend from config.latency

after:
  instance_uuid -> InstanceLatencyProfile -> per-instance latency backend
```

当前 HitFloor 已具备：

```text
fixed-routing
multi-instance isolated replay
true streaming capacity sweep
```

但当前仍是 homogeneous config：

```text
all instances share scheduler/cache/latency config
```

本阶段第一版只解决 per-instance TTFT backend：

```text
fixed-routing
multi-instance isolated replay
per-instance fitted TTFT backend
```

## 2. 产品语义

用户可以维护一个实例表，记录本次 replay 中每个实例的 `instance_uuid` 和对应 TTFT 超参数。

第一版表只影响 latency，不影响：

- request routing。
- scheduler config。
- HBM capacity。
- eviction policy。
- cache sharing。
- deployment physical topology。

也就是说，同一个 CSV 中：

```text
request.instance_uuid = instance-a
-> instance-a 的 HBM cache
-> instance-a 的 latency backend

request.instance_uuid = instance-b
-> instance-b 的 HBM cache
-> instance-b 的 latency backend
```

实例之间仍互不干扰。

重要约束：

- 多个实例可以共享同一套 `deployment` / scheduler / cache 配置。
- 即使多个实例共享同一套配置参数，也允许它们拥有不同的 TTFT 超参数。
- 原因是不同实例上的请求数、请求形态、负载时间段和校准样本可能不同。
- TTFT 拟合窗口的请求计数器属于实例侧，不属于 deployment 侧或全局 trace 侧。

因此不能做下面的隐式合并：

```text
same deployment -> same TTFT hyperparameters
```

正确语义是：

```text
same deployment -> may share scheduler/cache startup parameters
same instance_uuid -> owns its latency profile and calibration counter
```

## 3. 配置形态

### 3.1 文件位置

第一版复用现有实例配置目录：

```text
configs/instances/<cluster_name>.yaml
```

原因：

- 该目录已经用于 `instance_uuid -> deployment` 映射。
- 用户关心的是“本次参与 replay 的实例表”。
- 将 `deployment` 和 `latency_profile` 放在同一张表中，最容易 review。

### 3.2 推荐 schema

示例：

```yaml
instances:
  name: local-fixed-route-example

  latency_profiles:
    glm-v5.1-a3-fast:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-fast
      fitted_ttft:
        profile: glm-v5.1-a3-fast
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.010
        calibrated_from: manual
        calibration_window_requests: 500

    glm-v5.1-a3-slow:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-slow
      fitted_ttft:
        profile: glm-v5.1-a3-slow
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.018
        calibrated_from: manual
        calibration_window_requests: 500

  items:
    instance-a:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: glm-v5.1-a3-fast

    instance-b:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: glm-v5.1-a3-slow
```

实验 config 引用：

```yaml
instance_latency:
  profile_path: configs/instances/local-fixed-route-example.yaml
  require_all_trace_instances: true
```

### 3.3 Backward Compatibility

如果实验 config 没有 `instance_latency`：

```text
StreamingCapacitySweepRunner
-> 继续使用 config.latency 构造全局 backend
```

如果存在 `instance_latency.profile_path`：

```text
StreamingCapacitySweepRunner
-> 必须按 instance_uuid 找到 InstanceLatencyProfile
```

第一版不做模糊 fallback：

- 表存在但实例缺失：fail-fast。
- 表存在但 `latency_profile` 缺失：fail-fast。
- `backend` 不是 `fitted_ttft`：fail-fast。
- TTFT 超参数非法：fail-fast。

原因：latency profile 直接影响 TTFT 结果，不能静默退回全局 backend。

## 4. 数据模型设计

### 4.1 现有基础

当前已有：

```text
src/hitfloor/config/profiles.py
  InstanceDeployment
  InstanceProfile
```

当前 `InstanceProfile` 只支持：

```text
instance_uuid -> deployment
```

需要扩展为：

```text
instance_uuid -> deployment + latency_profile
latency_profile_name -> TTFT hyperparameters
```

### 4.2 新增类型

建议在 `src/hitfloor/config/profiles.py` 中新增：

```python
@dataclass(frozen=True, slots=True)
class FittedTTFTProfile:
    profile: str
    function: Literal["token_linear_v1"]
    intercept_ms: float
    ms_per_uncached_token: float
    calibrated_from: str
    calibration_window_requests: int = 500


@dataclass(frozen=True, slots=True)
class KVLoadLatencyProfile:
    ddr_ms_per_cached_token: float = 0.0
    remote_ms_per_cached_token: float = 0.0


@dataclass(frozen=True, slots=True)
class InstanceLatencyProfile:
    name: str
    backend: Literal["fitted_ttft"]
    model_name: str
    hardware_name: str
    fitted_ttft: FittedTTFTProfile
    kv_load: KVLoadLatencyProfile = field(default_factory=KVLoadLatencyProfile)
```

语义：

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

`queue_waiting_ms` 暂不放入实例表，因为它不是静态超参数，而是由到达、队列策略和负载状态决定。

`kv_load` 放入实例表，但第一版两个超参数默认均为 0：

```text
ddr_ms_per_cached_token = 0.0
remote_ms_per_cached_token = 0.0
```

原因是当前只实现 HBM 命中，暂时没有 DDR hit tokens 或 remote hit tokens。未来多级缓存和跨实例命中完成后，可按下面的稳定口径接入：

```text
kv_load_ms =
  ddr_hit_tokens * ddr_ms_per_cached_token
  + remote_hit_tokens * remote_ms_per_cached_token
```

扩展现有 `InstanceDeployment`：

```python
@dataclass(frozen=True, slots=True)
class InstanceDeployment:
    instance_uuid: str
    deployment: str
    latency_profile: str | None = None
```

扩展现有 `InstanceProfile`：

```python
@dataclass(frozen=True, slots=True)
class InstanceProfile:
    name: str
    instances: tuple[InstanceDeployment, ...]
    latency_profiles: tuple[InstanceLatencyProfile, ...] = ()

    @property
    def latency_profile_by_instance(self) -> dict[str, InstanceLatencyProfile]:
        ...
```

### 4.3 解析规则

`InstanceProfile.from_mapping()` 需要支持：

```yaml
instances:
  latency_profiles:
    <profile_name>: ...
  items:
    <instance_uuid>:
      deployment: ...
      latency_profile: ...
```

校验：

- `instances.name` 必须非空。
- `items` 必须是 mapping。
- 每个 `instance_uuid` 必须非空。
- 每个 item 的 `deployment` 必须非空。
- 如果 item 声明了 `latency_profile`，必须能在 `latency_profiles` 中找到。
- 如果 `latency_profiles` 非空，则每个 instance item 必须声明 `latency_profile`。
- latency profile name 必须与 key 一致，或者以 key 作为 name。
- `backend` 第一版只支持 `fitted_ttft`。
- `intercept_ms >= 0`。
- `ms_per_uncached_token >= 0`。
- `calibration_window_requests > 0`，默认 500。
- `function == token_linear_v1`。

### 4.4 实例侧拟合窗口

TTFT 超参数可以来自人工配置，也可以来自后续校准流程。

如果未来接入“每 N 条请求重新拟合一次 TTFT”：

```text
N = calibration_window_requests
default N = 500
```

计数器必须属于实例侧：

```text
instance-a:
  seen_requests_for_calibration = 0..499
  fit instance-a profile

instance-b:
  seen_requests_for_calibration = 0..499
  fit instance-b profile
```

不能使用全局 trace 计数器：

```text
global seen_requests = 0..499
fit all instances together
```

也不能使用 deployment 计数器：

```text
deployment glm-v5.1-vllm-ascend-prefill
-> merge instance-a and instance-b requests
-> fit one shared profile
```

原因：

- 同一 deployment 下的不同实例可能承载不同租户、不同 prompt 长度分布、不同负载时间段。
- calibration sample count 和 fitting state 是 runtime instance state。
- deployment profile 描述启动参数，不描述实例运行时样本分布。

第一版实现只读取静态 TTFT 超参数，不实现动态重新拟合；但 schema 和文档必须保留 `calibration_window_requests` 语义，避免后续把拟合计数器放错层。

## 5. Latency Backend Resolver

### 5.1 新模块

建议新增：

```text
src/hitfloor/latency/instance_resolver.py
```

职责：

- 从实验 config 中读取 `instance_latency.profile_path`。
- 加载 `InstanceProfile`。
- 构造 per-instance backend。
- 如果未配置 `instance_latency`，返回全局 backend。

不负责：

- 解析 trace。
- 执行 replay。
- 聚合 metrics。
- 写 report。

### 5.2 类型设计

建议新增：

```python
@dataclass(frozen=True, slots=True)
class InstanceLatencyConfig:
    profile_path: Path | None
    require_all_trace_instances: bool = True


class InstanceLatencyBackendResolver:
    def backend_for(self, instance_uuid: str) -> BatchLatencyBackend:
        ...

    @property
    def uses_instance_profiles(self) -> bool:
        ...

    @property
    def profile_name_by_instance(self) -> Mapping[str, str]:
        ...
```

入口函数：

```python
def build_instance_latency_backend_resolver(
    config: Mapping[str, Any],
) -> InstanceLatencyBackendResolver:
    ...
```

### 5.3 行为

无 `instance_latency`：

```text
backend_for(any_instance)
-> build_batch_latency_backend(config)
```

有 `instance_latency.profile_path`：

```text
backend_for(instance-a)
-> load InstanceProfile
-> instance-a.latency_profile
-> InstanceLatencyProfile
-> FittedTTFTLatencyBackend(...)
```

缓存策略：

- resolver 内部缓存 backend。
- 同一个 instance 多次 replay capacity 时复用 backend 对象。
- backend 是 immutable dataclass，可安全复用。

错误：

- profile 文件不存在：ValueError。
- instance missing：ValueError。
- unsupported backend：ValueError。
- invalid TTFT hyperparameters：由 dataclass / parser fail-fast。

## 6. Streaming Runner 集成

### 6.1 当前问题

当前 `StreamingCapacitySweepRunner._run_capacity()` 中：

```python
engine = StreamingBatchAwareReplayEngine(
    scheduler=VllmLikeBatchScheduler(build_scheduler_config_from_config(self.config)),
    latency_backend=build_batch_latency_backend(self.config),
)

for shard in build_result.manifest.shards:
    engine.run_instance_stream(...)
```

这意味着所有实例共享同一个 latency backend。

### 6.2 修改后

建议改为：

```python
latency_resolver = build_instance_latency_backend_resolver(self.config)

for shard in build_result.manifest.shards:
    engine = StreamingBatchAwareReplayEngine(
        scheduler=VllmLikeBatchScheduler(build_scheduler_config_from_config(self.config)),
        latency_backend=latency_resolver.backend_for(shard.instance_uuid),
    )
    engine.run_instance_stream(...)
```

第一版每个 shard 创建一个 engine。

原因：

- 每个 engine 的 `ShapeMemo` 与 latency backend 绑定。
- `ShapeKey` 已包含 `backend`、`model_name`、`hardware_name` 和 shape。
- 不同实例的 hardware / model / TTFT 超参数不能共用同一个 memo。

可选优化：

- 后续可以缓存 `engine_by_instance`。
- 第一版不需要，避免 engine 状态复用带来歧义。

### 6.3 Scheduler 暂不异构

本阶段不做 per-instance scheduler。

当前仍使用全局：

```text
scheduler config from config.scheduler
```

原因：

- 用户本次明确需求是 TTFT 超参数表。
- scheduler 异构会影响 batching / TTFT / iteration 语义，应作为后续独立设计。
- 避免一次性把 deployment profile、scheduler profile 和 latency profile 全部接入 streaming runner。

未来如果需要：

```text
instance_uuid -> DeploymentProfile -> SchedulerConfig
```

应新增 `InstanceRuntimeResolver`，而不是把更多逻辑塞进 latency resolver。

## 7. Report / Metrics 影响

### 7.1 Request / Iteration Metrics

`IterationMetrics` 当前已有：

- `backend`
- `shape_key`

`ShapeKey` 已包含：

- backend name。
- model name。
- hardware name。
- batch shape。

因此第一版不需要修改 metrics schema。

但建议在 `LatencyResult.details` 中保留：

- `profile`
- `intercept_ms`
- `ms_per_uncached_token`
- `calibrated_from`

`FittedTTFTLatencyBackend` 已经具备这些字段。

### 7.2 CapacitySweepResult config_details

建议在 `StreamingCapacitySweepRunner._config_details()` 增加：

```text
instance_latency_enabled: true/false
instance_latency_profile_path: ...
instance_latency_profile_count: N
```

不要在 summary 中展开所有 instance 的 TTFT 超参数，避免 report 过大。

如果需要完整表，应后续新增 export：

```text
instance_latency_profiles.csv
```

这属于外围 report 能力，不是本阶段核心必要项。

## 8. 配置样例

建议新增：

```text
configs/instances/local-fixed-route-latency-example.yaml
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
```

`configs/instances/local-fixed-route-latency-example.yaml`：

```yaml
instances:
  name: local-fixed-route-latency-example
  latency_profiles:
    instance-a-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-fast
      fitted_ttft:
        profile: instance-a-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.010
        calibrated_from: synthetic
        calibration_window_requests: 500
    instance-b-ttft:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-slow
      fitted_ttft:
        profile: instance-b-ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.020
        calibrated_from: synthetic
        calibration_window_requests: 500
  items:
    instance-a:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-b-ttft
```

实验 config 关键部分：

```yaml
simulation:
  mode: capacity_sweep_streaming

instance_latency:
  profile_path: configs/instances/local-fixed-route-latency-example.yaml
  require_all_trace_instances: true
```

## 9. 测试计划

### 9.1 Unit Tests

新增：

```text
tests/unit/config/test_instance_latency_profiles.py
tests/unit/latency/test_instance_resolver.py
```

覆盖：

- parse instance latency profile table。
- missing latency profile fail-fast。
- unsupported backend fail-fast。
- invalid negative `ms_per_uncached_token` fail-fast。
- resolver 在无 `instance_latency` 时 fallback 到 global backend。
- resolver 在有 table 时按 instance 返回不同 backend。
- missing instance 在 `require_all_trace_instances=true` 时 fail-fast。

### 9.2 Integration Tests

新增：

```text
tests/integration/test_streaming_instance_latency_profiles.py
```

合成 trace：

- `instance-a` 和 `instance-b` 都有相同 prompt。
- `instance-a` 和 `instance-b` 共享同一个 `deployment`。
- 配置不同 `ms_per_uncached_token`。
- 使用同一个 capacity。

断言：

- streaming runner 成功。
- `instance-a` row 的 `p90_ttft_ms` 小于 `instance-b` row。
- 多实例共享同一 `deployment` 时，仍允许不同 `latency_profile`。
- trace row 的 p90 由两个实例合并后计算。
- 没有 `instance_latency` 时，旧 streaming runner 行为不变。
- 表存在但 CSV 出现未登记 instance 时 fail-fast。

### 9.3 Regression Tests

必须保持：

```text
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
git diff --check
```

## 10. Batch 开发顺序

### Batch IL-A：Schema / Parser

修改：

```text
src/hitfloor/config/profiles.py
tests/unit/config/test_instance_latency_profiles.py
configs/instances/local-fixed-route-latency-example.yaml
```

内容：

- 新增 `FittedTTFTProfile`。
- 新增 `InstanceLatencyProfile`。
- 扩展 `InstanceDeployment.latency_profile`。
- 扩展 `InstanceProfile.from_mapping()`。

验收：

- profile parse 测试通过。
- 旧 `configs/instances/local-fixed-route-example.yaml` 仍可解析。

### Batch IL-B：InstanceLatencyBackendResolver

新增：

```text
src/hitfloor/latency/instance_resolver.py
tests/unit/latency/test_instance_resolver.py
```

内容：

- 解析 `instance_latency.profile_path`。
- fallback global backend。
- per-instance fitted TTFT backend。
- fail-fast guard。

验收：

- resolver unit tests 通过。
- 不修改 streaming runner。

### Batch IL-B2：InstanceLatencyProfile kv_load schema extension

修改：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/__init__.py
tests/unit/config/test_instance_latency_profiles.py
configs/instances/local-fixed-route-latency-example.yaml
```

内容：

- 新增 `KVLoadLatencyProfile`。
- 扩展 `InstanceLatencyProfile.kv_load`。
- 默认 `ddr_ms_per_cached_token = 0.0`。
- 默认 `remote_ms_per_cached_token = 0.0`。
- 负值 fail-fast。
- 不修改 replay / runner。

验收：

- 旧未声明 `kv_load` 的实例 profile 仍可解析。
- 显式 `kv_load` 可以解析。
- 负值 hyperparameter 会失败。
- 现有 replay / sweep 结果不变。

### Batch IL-C：Streaming Runner Integration

修改：

```text
src/hitfloor/streaming/sweep.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

内容：

- 在 `StreamingCapacitySweepRunner` 初始化 latency resolver。
- `_run_capacity()` 按 shard.instance_uuid 构造 per-instance backend。
- `_config_details()` 增加 instance latency metadata。

验收：

- `instance-a` / `instance-b` 不同 TTFT 超参数导致 instance-level P90 不同。
- 无 `instance_latency` 的旧 streaming tests 仍通过。
- 旧 `CapacitySweepRunner` 不受影响。

### Batch IL-D：Docs / Examples / Full Validation

修改：

```text
README.md
docs/core_simulator_technical_plan.md
docs/hitfloor_product_design.md
docs/global_memory.md
```

内容：

- 登记 heterogeneous latency cluster replay。
- 说明当前只是 per-instance latency，不是 per-instance scheduler/cache。
- 更新使用示例和能力边界。

验收：

```text
ruff check / format
pytest
coverage
git diff --check
```

## 11. 非目标

本阶段不做：

- gateway routing。
- per-instance scheduler config。
- per-instance HBM capacity。
- per-instance cache block conversion。
- heterogeneous model tokenizer。
- multi-model trace。
- multi-tier cache。
- KV load latency。
- Decode / TPOT。
- production AIConfigurator adapter。
- parallel instance replay。

这些能力都应该后续通过独立 resolver、profile schema、runner 或 replay mode 实现。

## 12. 风险与取舍

### 12.1 为什么不把 TTFT 超参数写入 request metric

不建议第一版把每条请求的 `intercept_ms` / `ms_per_uncached_token` 写入 request metric。

原因：

- metrics schema 会膨胀。
- TTFT 超参数属于 instance config，不属于 request 事实。
- `IterationMetrics.shape_key` 和 `LatencyResult.details` 已能保留 backend/profile 信息。

后续如果需要完整审计，可以新增外围 export：

```text
instance_latency_profiles.csv
```

### 12.2 为什么第一版只接 streaming runner

用户关心的是大型 CSV 和集群离线 replay。

true streaming path 是大 trace 主入口：

```text
hitfloor sweep-streaming
```

旧 `CapacitySweepRunner` 是小 trace / debug path。第一版不强行改旧 runner，避免增加 blast radius。

如果后续也需要旧 runner 支持 per-instance latency，可以复用同一个 `InstanceLatencyBackendResolver`。

### 12.3 为什么不同时做 per-instance scheduler

per-instance scheduler 会影响：

- batch admission。
- chunked prefill。
- iteration shape。
- TTFT。
- concurrency。

这已经不是“TTFT 超参数表”的小改动，应作为下一阶段 heterogeneous deployment replay 设计。

## 13. 验收标准

代码实现完成后必须满足：

```text
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
PYTHONPATH=src .venv/bin/python -m pytest
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
git diff --check
```

功能验收：

- 大型 CSV streaming replay 可按 `instance_uuid` 选择不同 TTFT backend。
- 没有实例 latency table 时，现有 streaming sweep 行为不变。
- 表存在但实例缺失时 fail-fast。
- 同一 synthetic trace 中，不同实例 TTFT 超参数会反映到 instance-level P90。
- 多实例共享同一 deployment 时，仍允许不同 instance latency profile。
- 旧 `capacity_sweep`、旧 `batch_aware_hbm_lru` 语义不变。

## 14. 收口条件

本阶段完成后：

- 更新主文档和全局记忆。
- 已将该专项移入 `docs/archive/instance_latency_profiles/`。
- 新增 review 文档到 `docs/reviews/`。
- 明确 HitFloor 当前能力可以表述为：

```text
fixed-routing
multi-instance isolated
true streaming capacity sweep
per-instance fitted TTFT latency backend
```
