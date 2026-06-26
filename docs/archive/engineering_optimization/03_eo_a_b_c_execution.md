# EO-A / EO-B / EO-C 执行记录

## 范围

本轮开发对象是：

```text
核心仿真器工程优化
```

本轮未开发外围能力，未改变 report/export、capacity sweep 产品形态，也未把新 profile schema 接入默认 runner。

## EO-A：Replay Golden Test

新增：

```text
tests/golden/test_batch_aware_hbm_lru_golden.py
```

覆盖：

- fixed-routing 多实例隔离。
- finite HBM LRU。
- finish-time materialization。
- zero-miss fast-finish。
- deterministic replay output。
- request metrics / iteration metrics / cache event stats。

意义：

- 锁定 `batch_aware_hbm_lru` 当前默认语义。
- 后续工程优化如果造成 replay 输出漂移，会先被 golden test 捕获。

## EO-B：Profile Schema / RunSpec / ConfigGuard

新增：

```text
src/hitfloor/config/run_spec.py
src/hitfloor/config/profiles.py
src/hitfloor/config/guard.py
src/hitfloor/config/validation.py
tests/unit/config/test_profiles_and_guard.py
```

新增示例 profile：

```text
configs/models/glm-v5.1.yaml
configs/hardware/ascend-a3-example.yaml
configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml
configs/instances/local-fixed-route-example.yaml
```

已实现核心类型：

- `RunSpec`
- `ModelProfile`
- `HardwareProfile`
- `DeploymentProfile`
- `InstanceProfile`
- `SchedulerProfile`
- `ParallelProfile`
- `SpeculativeProfile`
- `CacheFeatureProfile`
- `ConfigGuardIssue`
- `ConfigGuardResult`

ConfigGuard 当前覆盖：

- `run.model_name` 与 `ModelProfile` / aliases 不匹配。
- speculative drop blocks 在 block conversion 未启用时阻止 replay。
- CP / PCP / DCP 与 unsupported cache family 组合。
- hybrid cache family 缺少 cache group metadata。
- trace request model 与 RunSpec / ModelProfile aliases 不匹配。

边界：

- 本轮只新增 schema 和 guard。
- 默认 runner 仍走旧 config 路径。
- EO-D 之前不改变 request build 和 replay 行为。

## EO-C：Block Size / Cache Block Conversion

新增：

```text
src/hitfloor/cache/block_size.py
src/hitfloor/cache/cache_block_conversion.py
tests/unit/cache/test_cache_block_conversion.py
```

已实现语义：

- `requested_block_size`
- `runtime_block_size`
- `effective_block_size`
- `max_cache_hit_length = prompt_tokens - 1`
- full-block floor。
- PCP / DCP 放大 full-attention effective block size。
- runtime block size override。
- MTP / EAGLE / EAGLE3 类 speculative drop blocks。
- hybrid cache group LCM 对齐。
- unsupported cache family / CP 组合返回 guarded result。

边界：

- 本轮是纯计算模块。
- 未接入 request builder。
- 未改变 existing `SimulationRequest.prompt_blocks` 构造。
- 未改变 replay / report 输出。

## 验证

本轮验证通过：

```text
pytest: 130 passed
ruff check src tests scripts: passed
ruff format --check src tests scripts: passed
```

## 后续

建议下一轮进入 EO-D：

```text
Profile-aware request build integration
```

EO-D 应在不改变 legacy config 行为的前提下，把 `RunSpec` / profile / block conversion 作为可选上下文接入 request build。
