# Batch IL-A 执行记录：Schema / Parser

执行时间：2026-06-26

任务类型：核心仿真器能力开发。

状态：已完成。

## 1. 本批目标

Batch IL-A 只实现 `InstanceProfile / InstanceLatencyProfile` 的 schema 与 parser。

本批不接入：

- `InstanceLatencyBackendResolver`。
- `StreamingCapacitySweepRunner`。
- per-instance latency backend selection。
- 动态 TTFT 重新拟合。

因此本批不会改变现有 replay、streaming sweep 或 `capacity_sweep` 语义。

## 2. 代码修改

修改：

```text
src/hitfloor/config/profiles.py
src/hitfloor/config/__init__.py
```

新增：

```text
configs/instances/local-fixed-route-latency-example.yaml
tests/unit/config/test_instance_latency_profiles.py
```

## 3. 新增 Schema

新增：

- `FittedTTFTProfile`
- `InstanceLatencyProfile`

扩展：

- `InstanceDeployment.latency_profile`
- `InstanceProfile.latency_profiles`
- `InstanceProfile.latency_profile_by_name`
- `InstanceProfile.latency_profile_by_instance`

第一版 latency backend 只支持：

```text
fitted_ttft
```

第一版 fitted TTFT function 只支持：

```text
token_linear_v1
```

## 4. 核心语义

多个实例可以共享同一套 `deployment` / scheduler / cache 配置，但仍可以拥有不同 TTFT 超参数。

示例：

```text
instance-a -> deployment X -> latency_profile instance-a-ttft
instance-b -> deployment X -> latency_profile instance-b-ttft
```

原因：

- deployment 表示启动和部署参数。
- latency profile 表示实例侧拟合结果。
- 同 deployment 的不同实例可能承载不同请求分布。

`calibration_window_requests` 默认值为 500。

如果后续实现每 500 条请求重新拟合一次 TTFT，该计数器必须属于实例侧，不能属于全局 trace，也不能属于 deployment。

## 5. 校验规则

已实现 fail-fast：

- `latency_profiles` 非空时，每个 instance item 必须声明 `latency_profile`。
- instance item 引用未知 `latency_profile` 会失败。
- `backend != fitted_ttft` 会失败。
- `function != token_linear_v1` 会失败。
- `intercept_ms < 0` 会失败。
- `ms_per_uncached_token < 0` 会失败。
- `calibration_window_requests <= 0` 会失败。

旧 schema 仍兼容：

```yaml
instances:
  name: local-fixed-route-example
  items:
    instance-a:
      deployment: glm-v5.1-vllm-ascend-prefill
```

## 6. 测试覆盖

新增：

```text
tests/unit/config/test_instance_latency_profiles.py
```

覆盖：

- parse instance latency profile table。
- legacy deployment-only schema 仍可解析。
- shared deployment + different TTFT hyperparameters。
- missing latency profile fail-fast。
- unknown latency profile reference fail-fast。
- unsupported backend fail-fast。
- negative `ms_per_uncached_token` fail-fast。
- `calibration_window_requests` 默认 500。

## 7. 验证结果

定向检查：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config
13 passed

.venv/bin/python -m ruff check src/hitfloor/config tests/unit/config
All checks passed!

.venv/bin/python -m ruff format --check src/hitfloor/config tests/unit/config
8 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
190 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
190 passed
TOTAL 3641 statements, 256 missed, 93% coverage
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
136 files already formatted

git diff --check
passed
```

## 8. 收口结论

Batch IL-A 已完成。

当前只具备 schema/parser 能力：

```text
InstanceProfile can express per-instance latency profiles
```

还没有：

```text
instance_uuid -> latency backend resolver
streaming runner per-instance backend selection
```

下一批建议进入 Batch IL-B：

```text
InstanceLatencyBackendResolver
```
