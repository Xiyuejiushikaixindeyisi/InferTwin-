# S7-A：Config / Schema Guard 开发方案与执行记录

状态：已完成。

阶段类型：核心仿真器开发。

## 1. Batch 目标

S7-A 只做 Step7 的配置 schema 和 config guard，不实现 DDR cache，不改 replay，不改事件 schema。

目标是让 InferTwin 可以用稳定、严格、可测试的配置表达：

```text
同一个 fixed-routed instance 内启用 HBM + DDR/CPU 单实例 pooling。
```

S7-A 完成后，后续 S7-B / S7-C / S7-D 可以基于同一套 schema 实现：

- tier-aware `CacheEvent`。
- DDR LRU tier。
- `TieredPrefixCache`。
- streaming runner 的 `batch_aware_hbm_ddr_lru` mode。

## 2. 为什么需要 S7-A

当前代码已经有 model registry、instance runtime resolver 和 model-owned default cache，但它只能表达 HBM-only cache：

```python
ModelCacheDefaults(
    hbm_capacity_blocks: int,
    block_size_tokens: int,
    eviction_policy: "lru",
)
```

当前 `validate_model_registry()` 还会直接拒绝：

```text
deployment.cache_features.pooling = true
```

这与 Step7 的目标冲突。Step7 需要允许“单实例 DDR/CPU pooling”，但仍要 fail-fast 拒绝 V1 不支持的能力：

- multi-instance pooling。
- remote KV hit。
- SSD tier。
- kv_transfer / external connector。
- 非 LRU eviction policy。

如果没有 S7-A，后续 DDR cache 代码会缺少明确边界，容易把“单实例 DDR pooling”“跨实例 pooling”“external KV transfer”“多级 cache latency”混在一起。

## 3. 当前代码现状

相关文件：

```text
src/infertwin/config/model_runtime.py
src/infertwin/config/model_registry.py
src/infertwin/config/model_binding.py
src/infertwin/config/instance_runtime.py
src/infertwin/config/profiles.py
configs/models/registry.yaml
configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml
```

当前能力：

- `ModelRegistry` 可以解析 `default_cache` 和 `default_latency`。
- `ModelRuntimeTable` 可以把 model profile、deployment profile、default cache、default latency 组合成 `ResolvedModelRuntimeProfile`。
- `InstanceRuntimeResolver` 可以按 `instance_uuid -> model_name` 找到模型默认 cache / scheduler / deployment 配置。
- streaming runner 已经能使用 model-owned `hbm_capacity_blocks`、`block_size_tokens`、scheduler startup args 和 TTFT defaults。

当前限制：

- `default_cache` 没有 DDR capacity。
- `default_cache` 没有 pooling flags。
- `deployment.cache_features.pooling=true` 被直接拒绝。
- `ResolvedModelRuntimeProfile.pooling_enabled` 目前读取 deployment profile 的 bool，不能表达 Step7 的 runtime default cache policy。

## 4. S7-A 目标 schema

### 4.1 HBM-only 旧配置继续有效

旧配置仍然有效，避免破坏现有 HBM-only replay：

```yaml
default_cache:
  hbm_capacity_blocks: 4096
  block_size_tokens: 128
  eviction_policy: lru
```

解析后语义：

```text
pooling.enabled = false
ddr_capacity_blocks = None
```

### 4.2 Step7 单实例 DDR/CPU pooling 配置

Step7 新增配置形态：

```yaml
default_cache:
  hbm_capacity_blocks: 4096
  ddr_capacity_blocks: 65536
  block_size_tokens: 128
  eviction_policy: lru
  pooling:
    enabled: true
    single_instance: true
    multi_instance: false
    ddr_enabled: true
    remote_enabled: false
    ssd_enabled: false
```

含义：

- `hbm_capacity_blocks`：模型默认 HBM prefix cache 容量。capacity sweep 可覆盖它。
- `ddr_capacity_blocks`：模型默认 DDR/CPU prefix cache 容量。Step7 v1 不 sweep 它。
- `block_size_tokens`：requested block size，仍由 deployment runtime block size / CP / MTP conversion 共同决定最终 accounting。
- `eviction_policy`：V1 只支持 `lru`。
- `pooling.enabled`：是否启用 Step7 单实例 pooling。
- `pooling.single_instance`：必须为 `true`。
- `pooling.multi_instance`：必须为 `false`。
- `pooling.ddr_enabled`：必须为 `true`。
- `pooling.remote_enabled`：必须为 `false`。
- `pooling.ssd_enabled`：必须为 `false`。

### 4.3 Deployment cache features 一致性

deployment profile 仍保留 vLLM / vLLM-Ascend 启动特性：

```yaml
deployment:
  cache_features:
    prefix_caching: true
    multi_tier_cache: true
    pooling: true
    kv_transfer: false
    runtime_block_size: 128
```

S7-A 建议 guard：

- `default_cache.pooling.enabled=false` 时，deployment 不应声明 `pooling=true`。
- `default_cache.pooling.enabled=true` 时，deployment 应声明 `pooling=true`。
- `deployment.cache_features.kv_transfer=true` 一律 fail-fast，因为 Step7 不做 external KV transfer。
- `deployment.cache_features.multi_tier_cache=true` 只能和 `default_cache.pooling.enabled=true` 一起出现。

这让 model-owned cache capacity 和 deployment startup features 保持一致。

## 5. Guard 规则

### 5.1 必须接受

1. 旧 HBM-only registry。
2. 新 HBM + DDR single-instance pooling registry。
3. 多个 instance 共享同一个 pooling model。
4. 多个 model 中，一个 HBM-only、一个 pooling，且 instance 分别绑定。

### 5.2 必须 fail-fast

1. `pooling.enabled=true` 但缺少 `ddr_capacity_blocks`。
2. `pooling.enabled=true` 但 `ddr_capacity_blocks <= 0`。
3. `pooling.enabled=true` 但 `single_instance=false`。
4. `pooling.enabled=true` 但 `multi_instance=true`。
5. `pooling.enabled=true` 但 `ddr_enabled=false`。
6. `pooling.enabled=true` 但 `remote_enabled=true`。
7. `pooling.enabled=true` 但 `ssd_enabled=true`。
8. deployment `kv_transfer=true`。
9. deployment `pooling=true` 但 model default cache pooling 未启用。
10. model default cache pooling 启用但 deployment `pooling=false`。
11. `eviction_policy != lru`。

## 6. 代码编写方案

### A1. 扩展 model runtime schema

修改：

```text
src/infertwin/config/model_runtime.py
```

新增 dataclass：

```python
@dataclass(frozen=True, slots=True)
class ModelCachePoolingDefaults:
    enabled: bool = False
    single_instance: bool = True
    multi_instance: bool = False
    ddr_enabled: bool = False
    remote_enabled: bool = False
    ssd_enabled: bool = False
```

扩展：

```python
@dataclass(frozen=True, slots=True)
class ModelCacheDefaults:
    hbm_capacity_blocks: int
    block_size_tokens: int
    eviction_policy: Literal["lru"] = "lru"
    ddr_capacity_blocks: int | None = None
    pooling: ModelCachePoolingDefaults = field(
        default_factory=ModelCachePoolingDefaults
    )
```

解析规则：

- `ddr_capacity_blocks` 可选。
- `pooling` 可选，缺省为 disabled。
- 新增 `_optional_positive_int()` 和 `_bool()` helper。
- 继续保持旧 `default_cache` schema 可解析。

### A2. 扩展 runtime profile 只读属性

修改：

```text
src/infertwin/config/model_runtime.py
```

建议新增属性：

```python
ResolvedModelRuntimeProfile.pooling_enabled
ResolvedModelRuntimeProfile.ddr_capacity_blocks
ResolvedModelRuntimeProfile.single_instance_pooling_enabled
```

`pooling_enabled` 应读取：

```text
default_cache.pooling.enabled
```

不再把 deployment bool 当成最终 runtime cache policy。deployment bool 只参与一致性 guard。

### A3. 修改 model registry validation guard

修改：

```text
src/infertwin/config/model_binding.py
```

替换当前直接拒绝 pooling deployment 的逻辑：

```python
if deployment_profile.cache_features.pooling:
    raise ValueError(...)
```

改为调用：

```python
_validate_step7_cache_features(entry, deployment_profile)
```

建议 helper 规则：

- HBM-only：不允许 deployment `pooling=true` / `multi_tier_cache=true` / `kv_transfer=true`。
- pooling enabled：要求 `ddr_capacity_blocks` 存在且为正数。
- pooling enabled：只允许 `single_instance=true`、`multi_instance=false`、`ddr_enabled=true`、`remote_enabled=false`、`ssd_enabled=false`。
- pooling enabled：要求 deployment `pooling=true`。
- pooling enabled：允许 deployment `multi_tier_cache=true`。
- 所有场景：deployment `kv_transfer=true` fail-fast。

错误信息必须包含足够上下文，例如：

```text
models.glm-v5.1.default_cache.pooling.multi_instance is not supported in Step7
model registry entry 'glm-v5.1' enables deployment pooling but default_cache.pooling.enabled is false
```

### A4. 更新示例配置

可选改动：

```text
configs/models/registry.yaml
configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml
```

推荐方式：

- 保持主 `registry.yaml` 为 HBM-only baseline，避免默认示例突然进入 Step7 pooling mode。
- 新增 Step7 pooling 示例文件，例如：

```text
configs/models/registry_step7_pooling.yaml
configs/deployments/glm-v5.1-vllm-ascend-prefill-pooling.yaml
```

原因：

- 旧 E2E / streaming sweep 默认仍是 HBM-only。
- Step7 pooling 示例可以被 S7-E / S7-F 复用。
- 避免外围用户误以为当前 `simulate` / non-streaming `sweep` 已支持 DDR replay。

### A5. 更新测试

修改或新增：

```text
tests/unit/config/test_model_registry.py
tests/unit/config/test_model_runtime.py
tests/unit/config/test_model_binding.py
tests/unit/config/test_instance_runtime.py
```

必须覆盖：

- HBM-only 旧 schema 继续可解析。
- `default_cache.pooling` 可解析。
- `ddr_capacity_blocks` 可解析。
- pooling enabled + valid single-instance DDR config 通过。
- pooling enabled + missing DDR capacity 失败。
- pooling enabled + `multi_instance=true` 失败。
- pooling enabled + `remote_enabled=true` 失败。
- pooling enabled + `ssd_enabled=true` 失败。
- pooling enabled + `ddr_enabled=false` 失败。
- deployment `kv_transfer=true` 失败。
- deployment `pooling=true` 但 default cache pooling disabled 失败。
- default cache pooling enabled 但 deployment `pooling=false` 失败。

建议保留一个 integration smoke：

```text
tests/integration/test_streaming_runtime_integration.py
```

目的不是接 DDR replay，而是确认新增 schema 不破坏现有 streaming runtime integration。

### A6. 不修改的文件

S7-A 不修改：

```text
src/infertwin/cache/
src/infertwin/replay/
src/infertwin/streaming/replay.py
src/infertwin/report/cache_events.py
src/infertwin/report/sweep.py
```

如果开发过程中发现必须修改这些文件，应暂停并重新评审，因为那说明 S7-A 越界进入了 S7-B/S7-D/S7-E。

### A7. 验收命令

建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_runtime.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_instance_runtime.py
```

再跑 streaming runtime smoke：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_streaming_runtime_integration.py
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/config tests/unit/config tests/integration/test_streaming_runtime_integration.py
.venv/bin/python -m ruff format --check src/infertwin/config tests/unit/config tests/integration/test_streaming_runtime_integration.py
git diff --check
```

## 7. S7-A 成功标准

S7-A 完成时应满足：

- 旧 HBM-only registry 不需要改配置也能通过。
- 新 Step7 single-instance DDR pooling schema 能解析、能 validate。
- V1 不支持的 pooling 形态全部 fail-fast。
- `ModelRuntimeTable` 能暴露 DDR capacity 和 pooling flags。
- `InstanceRuntimeResolver` 可以按 instance 找到对应 model 的 pooling defaults。
- streaming runner 现有 HBM-only path 不受影响。
- 没有引入 DDR cache 行为，没有改变 replay semantics。

## 8. 对后续 Batch 的影响

S7-B 可以基于本 batch 的 schema 继续扩展 `CacheEvent`，但不需要再讨论 pooling config 格式。

S7-C 可以实现 DDR LRU tier，并使用：

```text
ModelCacheDefaults.ddr_capacity_blocks
```

作为容量来源。

S7-E 可以在 streaming runner 中读取：

```text
runtime_profile.default_cache.pooling.enabled
runtime_profile.default_cache.ddr_capacity_blocks
```

来选择：

```text
batch_aware_hbm_lru
batch_aware_hbm_ddr_lru
```

## 9. 风险与边界

### 9.1 风险

- 如果 guard 过松，后续可能把 remote pooling / kv_transfer 当成单实例 DDR pooling 处理。
- 如果 guard 过严，可能拒绝一些真实部署脚本里字段名不完全一致的配置。
- 如果直接修改主示例 registry 为 pooling enabled，可能误导 legacy CLI 用户以为所有入口都支持 DDR replay。

### 9.2 控制方式

- S7-A 保持旧 schema 兼容。
- 新 pooling 示例独立于默认 HBM-only 示例。
- 所有不支持的 V1 能力 fail-fast，不静默忽略。
- 不改 replay，不改 cache，不改 report。

## 10. 执行记录

### 10.1 做了什么

- 新增 `ModelCachePoolingDefaults`，用于表达 Step7 model-owned pooling flags。
- 扩展 `ModelCacheDefaults`：
  - `ddr_capacity_blocks: int | None`。
  - `pooling: ModelCachePoolingDefaults`。
- 扩展 `ResolvedModelRuntimeProfile` 只读属性：
  - `pooling_enabled`。
  - `ddr_capacity_blocks`。
  - `single_instance_pooling_enabled`。
- 更新 `validate_model_registry()`：
  - 允许 Step7 单实例 DDR/CPU pooling。
  - 拒绝 deployment `kv_transfer=true`。
  - 拒绝 deployment pooling / multi-tier 与 model default cache pooling 不一致。
  - 拒绝 multi-instance / remote / SSD / missing DDR capacity 等 V1 不支持形态。
- 新增独立 Step7 pooling 示例配置：
  - `configs/models/registry_step7_pooling.yaml`。
  - `configs/deployments/glm-v5.1-vllm-ascend-prefill-pooling.yaml`。
- 补充配置层单测和 streaming runtime smoke 测试。

### 10.2 没有做什么

- 没有实现 DDR LRU cache backend。
- 没有修改 HBM cache 行为。
- 没有修改 replay / scheduler / report 语义。
- 没有新增 `batch_aware_hbm_ddr_lru` mode。
- 没有扩展 `CacheEvent` schema。
- 没有让 legacy `simulate` / non-streaming `sweep` 支持 DDR replay。

### 10.3 影响

- HBM-only 旧 `default_cache` schema 继续可用。
- Step7 single-instance DDR/CPU pooling 已可通过 model registry 和 deployment profile 表达。
- `ModelRuntimeTable` 和 `InstanceRuntimeResolver` 已能把 pooling defaults 暴露到 instance runtime 层。
- 新 schema 不改变现有 streaming HBM-only replay 路径。

### 10.4 边界

- S7-A 只完成配置治理和 fail-fast guard。
- DDR capacity 目前只是 model-owned runtime metadata，尚未被 replay 消费。
- `deployment.cache_features.pooling=true` 只有在 `default_cache.pooling.enabled=true` 时才允许。
- `default_cache.pooling.enabled=true` 要求 deployment `cache_features.pooling=true`。
- V1 只允许 single-instance DDR/CPU pooling；remote、SSD、multi-instance pooling 仍然 fail-fast。

### 10.5 风险

- 后续 S7-E 接入 streaming runner 时，需要明确使用 `ddr_capacity_blocks` 的入口，避免把它误当成 HBM capacity sweep candidate。
- 当前新增示例配置只是 schema 示例，不能代表 DDR replay 已经可用。
- deployment `multi_tier_cache=false` 且 default cache pooling enabled 目前不强制失败；当前只要求 deployment `pooling=true`。如后续认为需要更强一致性，应在进入 S7-E 前单独评审。

### 10.6 测试结果

配置层单测：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_runtime.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_instance_runtime.py
```

结果：

```text
42 passed
```

目标测试与 streaming runtime smoke：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_runtime.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_instance_runtime.py \
  tests/integration/test_streaming_runtime_integration.py
```

结果：

```text
44 passed
```

新增示例配置解析验证：

```bash
PYTHONPATH=src .venv/bin/python -c "..."
```

结果：

```text
65536 True True
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/config tests/unit/config tests/integration/test_streaming_runtime_integration.py
.venv/bin/python -m ruff format --check src/infertwin/config tests/unit/config tests/integration/test_streaming_runtime_integration.py
git diff --check
```

结果：

```text
passed
```

### 10.7 是否建议进入下一 Batch

建议进入 S7-B：CacheEvent Tier Schema。

进入方式仍应遵循 Step7 门禁：先提交 S7-B 详细代码开发方案和原因，经用户评审通过后再写代码。
