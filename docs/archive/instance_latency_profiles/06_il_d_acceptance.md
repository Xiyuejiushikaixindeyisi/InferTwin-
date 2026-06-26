# Batch IL-D 验收记录：主文档、示例和完整验收收口

执行时间：2026-06-26

任务类型：核心仿真器文档与验收收口。

状态：已完成。

## 1. 本批目标

Batch IL-D 不修改核心 replay 逻辑。

本批目标是把 IL-A 到 IL-C 完成的实例级 latency 能力同步到主文档、示例配置和验收记录中。

当前能力名称：

```text
fixed-routing, multi-instance isolated, heterogeneous fitted TTFT replay for true streaming capacity sweep
```

更短地说：

```text
true streaming per-instance fitted TTFT backend selection
```

## 2. 文档修改

更新：

```text
README.md
docs/hitfloor_product_design.md
docs/core_simulator_technical_plan.md
docs/global_memory.md
docs/archive/instance_latency_profiles/README.md
```

新增：

```text
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
docs/archive/instance_latency_profiles/06_il_d_acceptance.md
```

## 3. 示例配置

新增示例：

```text
configs/experiments/streaming_capacity_sweep_instance_latency.yaml
```

关键配置：

```yaml
simulation:
  mode: capacity_sweep_streaming

instance_latency:
  profile_path: configs/instances/local-fixed-route-latency-example.yaml
  require_all_trace_instances: true
```

运行方式：

```bash
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main sweep-streaming \
  --config configs/experiments/streaming_capacity_sweep_instance_latency.yaml
```

## 4. 当前语义

未配置 `instance_latency`：

```text
streaming runner -> global latency backend
```

配置 `instance_latency.profile_path`：

```text
streaming runner
-> shard.instance_uuid
-> InstanceLatencyBackendResolver.backend_for(instance_uuid)
-> instance fitted TTFT backend
```

实例缺失：

```text
trace instance_uuid not found in instance latency table -> fail-fast
```

TTFT 长期分解：

```text
request TTFT =
  queue_waiting_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

当前实现：

```text
queue_waiting_ms = 0
uncached_prefill_compute_ms = fitted_ttft(uncached_tokens)
kv_load_ms = 0
```

`InstanceLatencyProfile.kv_load` 已有 schema，但当前不参与 TTFT 计算。

## 5. 当前不是

IL-D 收口后，HitFloor 仍不是完整 heterogeneous instance cluster replay。

未实现：

- per-instance scheduler config。
- per-instance cache capacity。
- dynamic per-500-request TTFT refit。
- DDR / remote KV-load latency materialization。
- gateway routing simulation。
- cross-instance pooling。
- progressive block visibility。

## 6. 验收结果

示例 CLI：

```text
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main sweep-streaming --config configs/experiments/streaming_capacity_sweep_instance_latency.yaml
HitFloor capacity_sweep completed.
```

定向回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/config/test_instance_latency_profiles.py
20 passed
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
199 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
138 files already formatted

git diff --check
passed
```

## 7. 收口结论

IL-A 到 IL-D 已完成。

当前完成：

```text
InstanceProfile / InstanceLatencyProfile schema
InstanceLatencyBackendResolver
true streaming runner per-instance fitted TTFT backend selection
kv_load latency knobs reserved in schema
main docs and example config
```

下一步如果继续该专项，可以进入：

```text
dynamic per-instance TTFT refit
```

该专项后续已归档到 `docs/archive/instance_latency_profiles/`。
