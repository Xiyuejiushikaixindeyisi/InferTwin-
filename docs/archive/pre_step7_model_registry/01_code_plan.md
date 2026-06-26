# Pre-Step7 代码方案：Model Registry & Instance Model Binding

执行时间：2026-06-26

状态：方案已评审通过；MR-1、MR-2、MR-3、MR-4、MR-5、MR-6、MR-7 已完成；专项已收口并归档。

任务类型：核心仿真器开发，工程优化 / 配置治理 / 兜底能力。

## 1. 背景与目标

进入 Step7 前，需要把 HitFloor 的模型、实例和 latency 默认值关系收紧。

当前已有基础：

- `configs/models/<model>.yaml`：单模型 `ModelProfile`。
- `configs/instances/<cluster>.yaml`：`InstanceProfile`，目前主要表达 `instance_uuid -> deployment / latency_profile`。
- `InstanceLatencyBackendResolver`：streaming replay 中按 `instance_uuid` 选择 fitted TTFT backend。
- `RunSpec / ModelProfile / DeploymentProfile / InstanceProfile / ConfigGuard` foundation。

当前缺口：

- 没有一张全局 model registry 记录“已登记模型”。
- `InstanceProfile.items[*]` 尚未强制记录 `model_name`。
- 如果实例没有专属 `latency_profile`，无法从 model 默认 TTFT 超参数构建 backend。
- 外部 TTFT calibration / simulator 失败时，没有显式 fallback 到模型默认超参数的配置语义。
- 缺少 `instance_uuid -> model -> tokenizer / default latency` 的统一解析入口。

本专项目标：

```text
instance_uuid
-> InstanceProfile item
-> model_name
-> ModelRegistry entry
-> ModelProfile / tokenizer profile / default TTFT profile
-> latency backend fallback
```

## 2. 产品与技术边界

本专项属于核心仿真器开发中的工程优化与兜底能力，不是新 replay mode。

负责：

- 维护已登记模型表。
- 维护 instance 到 model 的绑定。
- 校验 instance / model / latency profile 一致性。
- 在无实例专属 TTFT profile 时，使用 model 默认 TTFT backend。
- 在外部 calibration 失败且显式允许 fallback 时，使用 model 默认 TTFT backend，并暴露 fallback 状态。

不负责：

- 不实现 gateway routing。
- 不改变 trace reader。
- 不改变 tokenizer 行为本身。
- 不改变 cache lookup / scheduler / replay / materialization 语义。
- 不实现外部 AIConfigurator / MkSim calibration harness。
- 不实现每 500 条请求动态 refit，只保留 schema / 状态字段。
- 不把所有错误都 fallback；request build、tokenizer、scheduler、cache replay 失败仍必须 fail-fast。

## 3. 核心语义

### 3.1 默认 TTFT fallback

允许 fallback 的场景：

- 外部 TTFT simulator 不可用。
- 外部 calibration harness 超时。
- 外部 calibration 结果不满足 schema。
- 外部拟合失败。

不允许 fallback 的场景：

- trace schema 错误。
- request JSON parse 失败。
- tokenizer profile 缺失。
- `request_params.model` 与 instance 绑定模型不一致。
- instance 绑定了未知 model。
- cache / scheduler / replay 内部不变量失败。

fallback 必须显式配置，不允许静默发生：

```yaml
latency_fallback:
  on_calibration_failure: use_model_default
```

结果中必须暴露：

```text
latency_source=model_default
calibration_status=fallback_after_failure
```

### 3.2 优先级规则

按实例解析 latency backend 时使用以下优先级：

1. `InstanceProfile.items[*].latency_profile` 指向的实例专属 profile。
2. `InstanceProfile.items[*].model_name` 对应的 model registry 默认 TTFT profile。
3. 旧 config 全局 `latency` backend。

v1 建议：

- 如果配置了 `instance_latency.profile_path` 和 `model_registry.profile_path`，优先使用 1 或 2。
- 如果只配置了 `instance_latency.profile_path`，保持当前行为。
- 如果两者都没有配置，保持当前全局 backend 行为。
- 不在 v1 中支持 `require_all_trace_instances=false` 的模糊 fallback。

### 3.3 一致性规则

必须校验：

- `InstanceProfile.items[*].model_name` 必须存在于 model registry。
- `InstanceProfile.items[*].latency_profile.model_name` 必须等于该 instance 的 `model_name` 或命中 model aliases。
- trace 中 `request_params.model` 必须匹配该 instance 绑定的 model name 或 aliases。
- `ModelRegistryEntry.model_profile_path` 指向的 `ModelProfile.name` 必须等于 registry key。
- `ModelRegistryEntry.default_latency.model_name` 必须匹配 registry key 或 aliases。
- `ModelRegistryEntry.tokenizer_profile` 必须与 `ModelProfile.tokenizer_profile` 一致，除非后续显式新增 override 语义。

## 4. 建议配置形态

### 4.1 Model Registry

新增：

```text
configs/models/registry.yaml
```

示例：

```yaml
models:
  glm-v5.1:
    model_profile_path: configs/models/glm-v5.1.yaml
    tokenizer_profile: glm-v5
    chat_template_profile: glm-v5
    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-example
      fitted_ttft:
        profile: glm-v5.1_default_ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.01
        calibrated_from: default_registry
        calibration_window_requests: 500
      kv_load:
        ddr_ms_per_cached_token: 0.0
        remote_ms_per_cached_token: 0.0
```

### 4.2 Instance Profile

扩展：

```yaml
instances:
  name: local-fixed-route-latency-example
  items:
    instance-a:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
      latency_profile: instance-a-ttft
    instance-b:
      model_name: glm-v5.1
      deployment: glm-v5.1-vllm-ascend-prefill
```

语义：

- `model_name` 是实例运行的模型。
- `latency_profile` 可选；缺失时可从 model registry default latency fallback。
- legacy instance profile 没有 `model_name` 时仍可解析，但只有在未启用 `model_registry.profile_path` 时允许进入 replay。

### 4.3 Experiment Config

新增可选 section：

```yaml
model_registry:
  profile_path: configs/models/registry.yaml

latency_fallback:
  on_calibration_failure: use_model_default
```

不建议把 registry 塞进 `latency` section。原因：

- registry 描述模型与默认能力，不只是 latency。
- 未来 tokenizer、cache block conversion、GB->block planner 也会读取 registry。

## 5. 代码结构设计

### 5.1 新增 / 修改文件

新增：

```text
src/hitfloor/config/model_registry.py
tests/unit/config/test_model_registry.py
tests/unit/latency/test_instance_resolver_model_defaults.py
configs/models/registry.yaml
docs/pre_step7_model_registry/02_execution.md
```

修改：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/validation.py
src/hitfloor/config/__init__.py
src/hitfloor/latency/instance_resolver.py
src/hitfloor/streaming/sweep.py
configs/instances/local-fixed-route-example.yaml
configs/instances/local-fixed-route-latency-example.yaml
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
README.md
docs/global_memory.md
docs/core_simulator_technical_plan.md
docs/hitfloor_product_design.md
```

### 5.2 不修改文件

原则上不修改：

```text
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/tokenizer_registry.py
```

原因：

- 本专项只补配置解析和 latency backend resolution。
- 不改变 core replay 状态机。
- 不改变 tokenizer 具体实现。

## 6. 数据结构设计

### 6.1 ModelRegistryEntry

建议新增：

```python
@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    name: str
    model_profile_path: Path
    tokenizer_profile: str
    chat_template_profile: str | None
    default_latency: InstanceLatencyProfile
```

说明：

- 复用 `InstanceLatencyProfile` 表达 default latency，避免复制 fitted TTFT / kv_load schema。
- `default_latency.name` 可以自动设为 `<model_name>__default_latency`，也可以要求 YAML 显式声明。
- v1 只支持 `backend: fitted_ttft`。

### 6.2 ModelRegistry

建议新增：

```python
@dataclass(frozen=True, slots=True)
class ModelRegistry:
    entries: tuple[ModelRegistryEntry, ...]

    @property
    def entry_by_name(self) -> Mapping[str, ModelRegistryEntry]:
        ...

    def entry_for(self, model_name: str) -> ModelRegistryEntry:
        ...
```

v1 不做 alias lookup 直接落在 registry 层，alias 校验通过加载 `ModelProfile` 完成。

### 6.3 InstanceDeployment 扩展

当前：

```python
class InstanceDeployment:
    instance_uuid: str
    deployment: str
    latency_profile: str | None = None
```

建议扩展：

```python
class InstanceDeployment:
    instance_uuid: str
    deployment: str
    model_name: str | None = None
    latency_profile: str | None = None
```

兼容规则：

- legacy YAML 可不写 `model_name`。
- 如果启用 model registry，则 `model_name` 必填。
- 如果未启用 model registry，保持现有行为。

### 6.4 LatencyResolutionResult

建议新增内部结果类型：

```python
@dataclass(frozen=True, slots=True)
class LatencyResolutionResult:
    backend: BatchLatencyBackend
    source: Literal["instance_profile", "model_default", "global"]
    calibration_status: str
    model_name: str
```

v1 可只在 resolver 内维护 metadata，不一定暴露给 replay engine。`backend_for()` 可保持返回 `BatchLatencyBackend`，新增：

```python
metadata_for(instance_uuid: str) -> LatencyResolutionMetadata
```

这样不破坏 `StreamingBatchAwareReplayEngine` 当前接口。

## 7. 批次开发顺序

### MR-1：Schema / Parser

目标：

- 新增 `ModelRegistryEntry` / `ModelRegistry` parser。
- 扩展 `InstanceDeployment.model_name`。
- 保持 legacy instance schema 兼容。

代码：

```text
src/hitfloor/config/model_registry.py
src/hitfloor/config/profiles.py
src/hitfloor/config/validation.py
src/hitfloor/config/__init__.py
tests/unit/config/test_model_registry.py
tests/unit/config/test_instance_latency_profiles.py
```

测试：

- model registry 可解析多个模型。
- 缺 `model_profile_path` fail-fast。
- 缺 `default_latency` fail-fast。
- default latency backend 非 `fitted_ttft` fail-fast。
- `InstanceProfile.items[*].model_name` 可解析。
- legacy instance profile 没有 `model_name` 仍可解析。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_instance_latency_profiles.py
git diff --check
```

### MR-2：Registry Validation / Consistency Guard

目标：

- 校验 registry entry 与 ModelProfile 一致。
- 校验 instance model binding 与 registry 一致。
- 校验 instance latency profile model 与 instance model 一致。

建议新增：

```text
src/hitfloor/config/model_binding.py
tests/unit/config/test_model_binding.py
```

核心函数：

```python
def validate_model_registry(
    registry: ModelRegistry,
) -> ModelRegistryValidationResult:
    ...

def validate_instance_model_bindings(
    *,
    instance_profile: InstanceProfile,
    model_registry: ModelRegistry,
) -> ModelBindingValidationResult:
    ...
```

v1 可以 fail-fast，暂不返回复杂 warning tree。但错误信息必须包含：

- instance_uuid。
- model_name。
- profile key。
- 失败原因。

测试：

- instance 绑定未知 model fail-fast。
- latency profile model 与 instance model 不一致 fail-fast。
- registry key 与 loaded `ModelProfile.name` 不一致 fail-fast。
- tokenizer profile 不一致 fail-fast。
- model aliases 可用于 request model 校验。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_binding.py
git diff --check
```

### MR-3：InstanceLatencyBackendResolver Default Fallback

目标：

- 当 instance 没有专属 latency profile 时，从 model registry default latency 构建 backend。
- 保持旧行为：无 model registry 时继续使用全局 backend。
- 不实现 `require_all_trace_instances=false`。

修改：

```text
src/hitfloor/latency/instance_resolver.py
tests/unit/latency/test_instance_resolver.py
tests/unit/latency/test_instance_resolver_model_defaults.py
```

建议 resolver 初始化参数：

```python
class InstanceLatencyBackendResolver:
    def __init__(
        *,
        global_backend: BatchLatencyBackend,
        instance_profile: InstanceProfile | None = None,
        model_registry: ModelRegistry | None = None,
        require_all_trace_instances: bool = True,
        profile_path: Path | None = None,
        model_registry_path: Path | None = None,
    ) -> None:
        ...
```

解析规则：

```text
if no instance_profile:
    return global_backend

if instance has latency_profile:
    return instance profile backend

if model_registry and instance.model_name:
    return model default backend

otherwise:
    fail-fast
```

metadata：

```text
instance-a -> source=instance_profile
instance-b -> source=model_default
legacy -> source=global
```

测试：

- instance 专属 latency profile 优先。
- 无实例专属 profile 时使用 model default。
- 无 model registry 且缺 latency profile 时保持旧 fail-fast 或旧 global fallback，按配置分支验证。
- model registry 中模型缺失 fail-fast。
- fallback backend 的 `model_name/hardware_name/ms_per_uncached_token` 来自 model registry default latency。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py
git diff --check
```

### MR-4：Streaming Runner Metadata Integration

目标：

- 在 streaming sweep config 中接受 `model_registry.profile_path`。
- `config_details` 输出 resolver metadata，便于 report 中解释 TTFT 来源。

修改：

```text
src/hitfloor/streaming/sweep.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
configs/models/registry.yaml
configs/instances/local-fixed-route-latency-example.yaml
```

建议 `config_details` 新增：

```text
model_registry_enabled
model_registry_profile_path
latency_source_by_instance
```

注意：

- `latency_source_by_instance` 如果是 dict，不应直接塞入 CSV row；可放 `config_details`，summary 里再按需展示。
- `CapacitySweepRow` 不新增字段。
- replay engine 不感知 source。

测试：

- streaming sweep 中 instance-a 使用专属 profile，instance-b 使用 model default。
- `config_details["model_registry_enabled"] is True`。
- `config_details["latency_source_by_instance"]` 包含 `instance_profile` / `model_default`。
- capacity rows 指标仍正常生成。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
git diff --check
```

### MR-5：Calibration Failure Fallback Schema

目标：

- 先定义 fallback schema 和状态，不接真实 external calibration harness。
- 为未来 AIConfigurator / MkSim calibration harness 失败时 fallback 做接口预留。

新增 / 修改：

```text
src/hitfloor/latency/fallback.py
tests/unit/latency/test_latency_fallback.py
```

建议类型：

```python
@dataclass(frozen=True, slots=True)
class LatencyFallbackConfig:
    on_calibration_failure: Literal["fail", "use_model_default"] = "fail"
```

config：

```yaml
latency_fallback:
  on_calibration_failure: use_model_default
```

v1 行为：

- 解析并校验 config。
- `fail` 是默认值。
- 暂不主动捕获 current fitted TTFT backend 的普通错误。
- 只为后续 calibration harness 提供显式策略对象。

原因：

- 当前 HitFloor 没有真实 calibration harness。
- 不能把 replay 失败误判成 calibration failure。

测试：

- 缺省为 `fail`。
- `use_model_default` 可解析。
- 未知策略 fail-fast。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_latency_fallback.py
git diff --check
```

### MR-6：Docs / Examples / Memory

目标：

- 更新主文档和示例配置。
- 明确这是配置治理，不是 replay 语义变更。

修改：

```text
README.md
docs/global_memory.md
docs/core_simulator_technical_plan.md
docs/hitfloor_product_design.md
docs/pre_step7_model_registry/02_execution.md
```

必须写清：

- model registry 是 model -> profile/default latency 的索引。
- instance binding 是 instance_uuid -> model/deployment/optional latency profile。
- fallback 只用于 calibration failure，且必须显式配置。
- request build / tokenizer / replay 失败不能 fallback。
- 真正动态每 500 条 refit 仍未实现。

验收：

```text
rg -n "model_registry|latency_fallback|InstanceLatencyBackendResolver" README.md docs
git diff --check
```

### MR-7：工程收口

目标：

- 确认该专项没有破坏现有 replay 能力。
- 将临时文档归档。

完整验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
git diff --check
```

收口检查：

- `src/hitfloor/replay/` 没有 model registry 逻辑。
- `src/hitfloor/cache/` 没有 model registry 逻辑。
- `src/hitfloor/scheduler/` 没有 model registry 逻辑。
- model registry 只影响 config validation、request build context 和 latency backend resolution。
- 旧 config 没有 `model_registry` 时，现有测试和 CLI 仍通过。
- streaming sweep 中 `instance_latency.profile_path` 旧语义保持兼容。

归档：

```text
docs/pre_step7_model_registry/
-> docs/archive/pre_step7_model_registry/
```

归档后更新：

```text
README.md
docs/global_memory.md
docs/core_simulator_technical_plan.md
docs/hitfloor_product_design.md
```

## 8. 风险与取舍

### 8.1 为什么不直接把默认 latency 写进 InstanceProfile

不建议。

原因：

- 多个实例可能共享同一个模型默认 TTFT 超参数。
- 模型默认值属于 model 层，不属于 instance 层。
- 如果都写进 instance 表，会产生重复配置和一致性问题。

正确关系：

```text
model registry owns default latency
instance profile owns overrides
```

### 8.2 为什么 fallback 不能静默发生

静默 fallback 会让用户误以为结果来自真实 calibration / simulator。

必须在 `config_details` 或 summary 中暴露：

```text
latency_source=model_default
calibration_status=fallback_after_failure
```

### 8.3 为什么不在本专项做动态每 500 条 refit

当前尚未接入真实 calibration harness。

本专项只建立：

- 每实例计数器属于实例侧的语义。
- 默认 TTFT fallback 的配置和 resolver 能力。

动态 refit 应在后续 calibration harness 专项中实现。

### 8.4 为什么不改变 tokenizer registry

现有 tokenizer registry 已按 profile 工作。

本专项只让 model registry 能指向 tokenizer profile，后续 request build 可以从 model registry 读取目标 profile。实际 tokenizer 编码逻辑不应变化。

## 9. 最终验收标准

专项完成后必须满足：

- 已登记 model 可通过 registry 解析到 `ModelProfile`、tokenizer profile、default TTFT profile。
- instance 可显式绑定 `model_name`。
- streaming replay 可按 instance 选择：
  - instance 专属 TTFT backend。
  - model 默认 TTFT backend。
  - legacy 全局 backend。
- 错误配置 fail-fast。
- calibration failure fallback 有 schema，但不误吞 replay 错误。
- 旧实验配置仍可运行。
- 全量测试和 ruff 通过。
