# Pre-Step7 执行记录：Model Registry & Instance Model Binding

执行时间：2026-06-26

任务类型：核心仿真器开发，工程优化 / 配置治理 / 兜底能力。

状态：MR-1、MR-2、MR-3、MR-4、MR-5、MR-6、MR-7 已完成；专项已收口并归档。

## MR-1：Schema / Parser

完成内容：

- 新增 `ModelRegistryEntry` / `ModelRegistry` schema。
- 新增 `configs/models/registry.yaml` 示例。
- 新增 `load_model_registry()`。
- `InstanceDeployment` 新增可选 `model_name` 字段。
- 更新 instance 示例配置，为实例补充 `model_name`。
- 保持 legacy instance profile 兼容：没有 `model_name` 时仍可解析。

新增文件：

```text
src/hitfloor/config/model_registry.py
tests/unit/config/test_model_registry.py
configs/models/registry.yaml
```

修改文件：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/validation.py
src/hitfloor/config/__init__.py
configs/instances/local-fixed-route-example.yaml
configs/instances/local-fixed-route-latency-example.yaml
tests/unit/config/test_instance_latency_profiles.py
```

验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_instance_latency_profiles.py \
  tests/unit/config/test_profiles_and_guard.py
```

结果：

```text
21 passed
```

## MR-1 边界

本批未接入 resolver，不改变 replay。

未修改：

```text
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/tokenizer_registry.py
```

## MR-2：Registry Validation / Consistency Guard

完成内容：

- 新增 model registry 与 `ModelProfile` 的一致性校验。
- 新增 instance model binding 校验。
- 支持 registry profile 路径基于 `base_dir` 解析。
- 校验 registry key 与 `ModelProfile.name` 一致。
- 校验 registry tokenizer profile 与 `ModelProfile.tokenizer_profile` 一致。
- 校验 default latency model 落在 `ModelProfile.name / aliases` 中。
- 校验启用 model registry 后每个 instance 必须声明 `model_name`。
- 校验 instance 绑定的 model 必须存在于 registry。
- 校验 instance latency profile 的 `model_name` 与 instance model 或 aliases 一致。
- 调整 `InstanceProfile` parser：允许 instance 缺少 `latency_profile`，为 MR-3 model default fallback 留出语义空间。

新增文件：

```text
src/hitfloor/config/model_binding.py
tests/unit/config/test_model_binding.py
```

修改文件：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/__init__.py
tests/unit/config/test_instance_latency_profiles.py
```

验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_instance_latency_profiles.py \
  tests/unit/config/test_profiles_and_guard.py
```

结果：

```text
29 passed
```

## MR-2 边界

本批仍未接入 resolver，不改变 replay。

未修改：

```text
src/hitfloor/latency/instance_resolver.py
src/hitfloor/streaming/sweep.py
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/tokenizer_registry.py
```

## MR-3：InstanceLatencyBackendResolver Default Fallback

完成内容：

- `InstanceLatencyBackendResolver` 支持可选 `ModelRegistry`。
- 新增 `ModelRegistryConfig`。
- 新增 `LatencyResolutionMetadata`。
- 新增 `build_model_registry_config()`。
- `backend_for()` 保持返回 `BatchLatencyBackend`，不改变 replay engine 接口。
- 优先级：
  1. instance 专属 `latency_profile`。
  2. instance `model_name` 对应的 model registry default latency。
  3. 无 instance profile 时继续使用全局 backend。
- 新增 `metadata_for(instance_uuid)`，用于解释 source。
- 新增 `latency_source_by_instance`，为 MR-4 streaming metadata 做准备。
- `model_registry.profile_path` 一旦配置就会加载并校验 registry 本身。
- 没有 model registry 且 instance 缺少 `latency_profile` 时仍 fail-fast。

新增文件：

```text
tests/unit/latency/test_instance_resolver_model_defaults.py
```

修改文件：

```text
src/hitfloor/config/profiles.py
src/hitfloor/latency/instance_resolver.py
src/hitfloor/latency/__init__.py
tests/unit/latency/test_instance_resolver.py
```

验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_instance_latency_profiles.py
```

结果：

```text
35 passed
```

## MR-3 边界

本批仍未修改 streaming runner 的 `config_details`，未修改 replay。

未修改：

```text
src/hitfloor/streaming/sweep.py
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/tokenizer_registry.py
```

## MR-4：Streaming Runner Metadata Integration

完成内容：

- `StreamingCapacitySweepRunner` 的 `config_details` 输出 resolver metadata。
- 新增 `model_registry_enabled`。
- 新增 `model_registry_profile_path`。
- 新增 `latency_source_by_instance`。
- `latency_source_by_instance` 只进入 `config_details` / summary，不进入 `capacity_sweep.csv`。
- `CapacitySweepRow` 不新增字段，CSV 仍保持纯指标表。
- summary 只渲染已有 metadata，不重新计算 latency source。
- 集成测试覆盖 instance-a 使用 instance 专属 latency profile、instance-b 使用 model registry default latency。

修改文件：

```text
src/hitfloor/streaming/sweep.py
src/hitfloor/report/sweep.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

结果：

```text
17 passed
```

静态检查：

```text
.venv/bin/python -m ruff check \
  src/hitfloor/streaming/sweep.py \
  src/hitfloor/report/sweep.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py

.venv/bin/python -m ruff format --check \
  src/hitfloor/streaming/sweep.py \
  src/hitfloor/report/sweep.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

结果：

```text
All checks passed
3 files already formatted
```

## MR-4 边界

本批没有修改 replay core，也没有改变 request build、cache lookup、scheduler、materialization、tokenizer 行为。

未修改：

```text
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/
src/hitfloor/streaming/replay.py
```

## MR-5：Calibration Failure Fallback Schema

完成内容：

- 新增 `LatencyFallbackConfig`。
- 新增 `CalibrationFailurePolicy`。
- 新增 `CalibrationStatus`。
- 新增 `build_latency_fallback_config()`。
- 支持可选配置：

```yaml
latency_fallback:
  on_calibration_failure: use_model_default
```

- 默认策略为 `fail`。
- `use_model_default` 必须显式配置。
- 未知策略 fail-fast。
- 非 mapping 的 `latency_fallback` 配置 fail-fast。

新增文件：

```text
src/hitfloor/latency/fallback.py
tests/unit/latency/test_latency_fallback.py
```

修改文件：

```text
src/hitfloor/latency/__init__.py
```

验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_latency_fallback.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py
```

结果：

```text
16 passed
```

静态检查：

```text
.venv/bin/python -m ruff check \
  src/hitfloor/latency/fallback.py \
  src/hitfloor/latency/__init__.py \
  tests/unit/latency/test_latency_fallback.py

.venv/bin/python -m ruff format --check \
  src/hitfloor/latency/fallback.py \
  src/hitfloor/latency/__init__.py \
  tests/unit/latency/test_latency_fallback.py
```

结果：

```text
All checks passed
3 files already formatted
```

## MR-5 边界

本批只定义 calibration failure fallback 的 schema / policy object，不接入真实 calibration harness。

当前不会捕获：

- current fitted TTFT backend 构建错误。
- request build 错误。
- trace schema 错误。
- tokenizer 错误。
- scheduler / cache / replay 不变量错误。

原因：当前 HitFloor 没有真实 external calibration harness，不能把 replay 或配置错误误判成 calibration failure。

未修改：

```text
src/hitfloor/latency/instance_resolver.py
src/hitfloor/streaming/sweep.py
src/hitfloor/replay/
src/hitfloor/cache/
src/hitfloor/scheduler/
src/hitfloor/request/
```

## MR-6：Docs / Examples / Memory

完成内容：

- 更新 streaming instance latency 示例 config。
- 示例 config 新增 `model_registry.profile_path`。
- 示例 config 新增显式 `latency_fallback.on_calibration_failure`。
- README 增加 model registry、instance binding、latency fallback 使用说明。
- 核心技术路线增加 streaming runner latency 解析优先级。
- 产品形态设计文档新增 `ModelRegistry` 章节。
- 产品形态设计文档更新 `InstanceProfile` 语义。
- 全局记忆更新当前专项状态。

修改文件：

```text
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
README.md
docs/core_simulator_technical_plan.md
docs/hitfloor_product_design.md
docs/global_memory.md
docs/pre_step7_model_registry/README.md
docs/pre_step7_model_registry/01_code_plan.md
docs/pre_step7_model_registry/02_execution.md
```

写清的核心边界：

- `model_registry` 是 `model_name -> ModelProfile / tokenizer profile / default_latency` 索引。
- `instance_latency` 是 `instance_uuid -> model/deployment/optional latency_profile` 绑定表。
- latency backend 解析优先级是 instance profile -> model default -> legacy global backend。
- `latency_fallback` 只用于未来 calibration failure，且必须显式配置。
- request build / tokenizer / scheduler / cache / replay 错误不能 fallback。
- 动态每 500 条请求重新拟合 TTFT 尚未实现。

验证：

```text
rg -n "model_registry|latency_fallback|InstanceLatencyBackendResolver" README.md docs
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_latency_fallback.py \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main sweep-streaming \
  --config configs/experiments/streaming_capacity_sweep_instance_latency.yaml
git diff --check
```

结果：

```text
rg check: passed
30 passed
streaming example config: passed
git diff --check: passed
```

## MR-6 边界

本批只更新文档和示例配置，不修改 Python replay / scheduler / cache / tokenizer / request build 代码。

## MR-7：工程收口

完成内容：

- 完整验证该专项没有破坏现有 replay 能力。
- 确认 `src/hitfloor/replay/` 没有 model registry / latency fallback 逻辑。
- 确认 `src/hitfloor/cache/` 没有 model registry / latency fallback 逻辑。
- 确认 `src/hitfloor/scheduler/` 没有 model registry / latency fallback 逻辑。
- 确认 `src/hitfloor/request/` 没有 model registry / latency fallback 逻辑。
- 确认 `src/hitfloor/streaming/replay.py` 没有 model registry / latency fallback 逻辑。
- 确认 model registry 只影响 config validation、request build context 和 latency backend resolution。
- 确认旧 config 没有 `model_registry` 时，现有测试仍通过。
- 确认 streaming sweep 中 `instance_latency.profile_path` 旧语义保持兼容。

完整验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
rg -n "model_registry|ModelRegistry|latency_fallback|LatencyFallback" \
  src/hitfloor/replay src/hitfloor/cache src/hitfloor/scheduler \
  src/hitfloor/request src/hitfloor/streaming/replay.py
git diff --check
```

结果：

```text
235 passed
ruff check: passed
ruff format --check: 150 files already formatted
boundary rg: no matches
git diff --check: passed
```

收口结论：

- Pre-Step7 Model Registry & Instance Model Binding 专项已完成。
- 该专项属于核心仿真器开发中的工程优化 / 配置治理 / 兜底能力。
- 当前核心仿真器具备进入 Step7 的条件。
- 后续进入 Step7 前仍必须声明新阶段是核心仿真器能力还是外围能力。
