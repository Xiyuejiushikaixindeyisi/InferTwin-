# EO-E 执行记录

## 范围

本轮开发对象是：

```text
核心仿真器工程优化
```

本轮目标是 `Materialization policy interface`。

重要边界：

- `batch_aware_hbm_lru` 默认仍绑定 finish-time materialization。
- 本轮没有启用 progressive block visibility。
- 本轮没有改变 HBMCache / InfiniteHBMCache 的物化语义。
- 本轮没有改变 Step1-Step6 replay / sweep 输出路径。

## 新增代码

新增：

```text
src/hitfloor/cache/materialization.py
tests/unit/cache/test_materialization_policy.py
```

核心接口：

```text
MaterializationPolicy
FinishTimeMaterializationPolicy
```

`FinishTimeMaterializationPolicy` 语义：

```text
request prefill finish
-> materialize all miss blocks at finish_time_ms
-> blocks become visible to later cache lookup
```

## 修改代码

修改：

```text
src/hitfloor/replay/event_loop.py
src/hitfloor/cache/__init__.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
```

`BatchAwareReplayEngine` 新增可选参数：

```text
materialization_policy: MaterializationPolicy | None = None
```

默认：

```text
FinishTimeMaterializationPolicy()
```

因此未传入 policy 的所有现有 runner、capacity sweep 和测试仍保持原语义。

## 测试覆盖

新增 / 扩展测试覆盖：

- finish-time policy 在 request finish time 调用 cache materialize。
- policy 名称稳定为 `finish_time`。
- `BatchAwareReplayEngine` 确实通过 materialization policy 进行物化。
- 默认 finite HBM replay 仍能让后续重复请求命中。
- golden regression 继续通过。

## Progressive Block Visibility 状态

本轮只提供接口，不提供 progressive 实现。

未来如果要实现，应新增 policy 和 replay/cache mode，例如：

```text
ProgressiveChunkMaterializationPolicy
batch_aware_hbm_lru_progressive
```

并继续保持：

```text
batch_aware_hbm_lru -> FinishTimeMaterializationPolicy
```

## 验证

本轮验证通过：

```text
pytest: 135 passed
ruff check src tests scripts: passed
ruff format --check src tests scripts: passed
```

## 后续

建议下一轮进入 EO-F：

```text
ServingLatencyProfile interface
```

EO-F 应保持当前 fitted TTFT 默认 backend 结果不变。

## 收口 Review 约束

当前 progressive block visibility 仍未启用。后续如果要做，应该新增：

```text
ProgressiveChunkMaterializationPolicy
```

以及新的 replay/cache mode，例如：

```text
batch_aware_hbm_lru_progressive
```

不得直接改变现有：

```text
batch_aware_hbm_lru
```

的 finish-time materialization 默认语义。
