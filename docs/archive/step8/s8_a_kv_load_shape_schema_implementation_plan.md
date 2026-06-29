# S8-A 实施方案：KV-load Shape Schema

状态：待用户评审，尚未进入代码开发。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-A：KV-load Shape Schema。

本 Batch 假设：Step8 技术路线已通过，按照 `05_technical_route.md` 中的推荐顺序先开发 S8-A。

## 1. 类型与改动等级

本 Batch 属于核心仿真器。

改动等级：L3。

原因：

- 本 Batch 修改 replay-facing `BatchShape`。
- 本 Batch 修改 latency memoization 的 `ShapeKey`。
- 本 Batch 不直接改变 replay 行为，但为后续 S8-B / S8-D 改变 `finish_time` / `ttft_ms` 建立核心接口。

本 Batch 是 schema/interface 先行，不接入真实 KV load latency 计算。

## 2. 本 Batch 做什么

S8-A 只做 KV load shape schema：

1. 在 `ScheduledSlice` 上增加 request-slice 级 KV load 字段。
2. 在 `BatchShape` 上增加 iteration 级聚合属性。
3. 在 `ShapeKey` 上增加 KV load 维度，避免后续 latency memoization 错误复用。
4. 增加非负校验。
5. 保持默认值为 0，确保现有 HBM-only / DDR-accounting-only 路径兼容。
6. 增加单测，证明 shape aggregation 与 shape key 区分逻辑正确。

目标语义：

```text
ScheduledSlice.kv_load_tokens:
  本 request slice 在本 iteration 需要 load 的非 HBM cached tokens。

ScheduledSlice.kv_load_bytes:
  本 request slice 在本 iteration 需要 load 的 KV bytes。

BatchShape.kv_load_tokens:
  sum(slice.kv_load_tokens)

BatchShape.kv_load_bytes:
  sum(slice.kv_load_bytes)

BatchShape.kv_load_request_count:
  count(slice where kv_load_tokens > 0 or kv_load_bytes > 0)
```

S8-A 不负责决定哪些 request slice 应该带 KV load；这个行为在 S8-D replay integration 中实现。

## 3. 本 Batch 不做什么

S8-A 不做：

- 不实现 `KVLoadLatencyComponent`。
- 不升级 `KVLoadLatencyProfile`。
- 不修改 `InstanceLatencyBackendResolver`。
- 不修改 cache lookup / materialization / eviction。
- 不把 `ddr_hit_tokens` 写入 `ScheduledSlice.kv_load_tokens`。
- 不修正 zero-miss DDR load-only path。
- 不修改 request / iteration / sweep metrics。
- 不修改 report/export。
- 不接 Ramulator2 / Mooncake。
- 不建模 compute/load overlap。
- 不建模 DDR promotion、load completion event、load queue/backpressure。

如果开发中发现必须修改上述内容，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/scheduler/batch_shape.py`

职责：

- 定义 scheduler iteration 输出。
- 承载 replay-facing shape schema。

计划修改：

- `ScheduledSlice` 新增字段：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
```

- `ScheduledSlice.__post_init__` 新增非负校验：

```text
kv_load_tokens >= 0
kv_load_bytes >= 0
```

- `BatchShape` 新增聚合属性：

```python
kv_load_tokens
kv_load_bytes
kv_load_request_count
```

边界：

- 不改变 `scheduled_prefill_tokens`、`computed_tokens_before`、`cached_prefix_tokens` 的既有含义。
- 不在 scheduler 中填充非零 KV load 字段。

### 4.2 `src/infertwin/latency/schema.py`

职责：

- 定义 latency backend memoization schema。

计划修改：

- `ShapeKey` 新增字段：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
kv_load_request_count: int = 0
```

- `ShapeKey.from_shape()` 从 `BatchShape` 读取新增聚合属性。
- `ShapeKey.__str__()` 增加稳定字符串片段：

```text
kvload_tokens=<...>
kvload_bytes=<...>
kvload_reqs=<...>
```

边界：

- 新字段放在 dataclass 尾部并给默认值，降低对直接构造 `ShapeKey(...)` 的测试和旧调用方影响。
- 不改变 `LatencyResult` schema。

### 4.3 `tests/unit/scheduler/test_batch_shape_kv_load.py`

职责：

- 覆盖 `ScheduledSlice` 和 `BatchShape` 的 KV load shape 行为。

计划新增测试：

1. 默认 KV load 字段为 0。
2. `BatchShape.kv_load_tokens` / `kv_load_bytes` 正确聚合。
3. `kv_load_request_count` 只统计有 load 的 slice。
4. 负数 `kv_load_tokens` 报错。
5. 负数 `kv_load_bytes` 报错。

### 4.4 `tests/unit/latency/test_shape_key_kv_load.py`

职责：

- 覆盖 `ShapeKey` 对 KV load shape 的区分。

计划新增测试：

1. `ShapeKey.from_shape()` 包含 `kv_load_tokens`。
2. `ShapeKey.from_shape()` 包含 `kv_load_bytes`。
3. `ShapeKey.from_shape()` 包含 `kv_load_request_count`。
4. prefill shape 相同但 KV load shape 不同，得到不同 `ShapeKey`。
5. `str(shape_key)` 包含 KV load 片段，便于 metrics/debug。

### 4.5 `tests/unit/latency/test_shape_memo.py`

职责：

- 覆盖 `ShapeMemo` 复用逻辑。

计划修改：

- 在现有 helper `_key()` 中保留默认 KV load 0。
- 可新增一个测试，证明同 prefill shape、不同 KV load shape 不会复用 memo。

如果 `test_shape_key_kv_load.py` 已充分覆盖该行为，可以只做 helper 兼容，不额外修改此文件。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 `ScheduledSlice`

新增字段：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
```

语义：

- `kv_load_tokens` 是本 slice 对本 iteration 的 KV load token 数。
- `kv_load_bytes` 是本 slice 对本 iteration 的 KV load byte 数。
- S8-A 中默认永远为 0。
- S8-D 后，request 第一次被 scheduler 选中时，DDR hit 对应的 load 会写入这里。

不变量：

```text
kv_load_tokens >= 0
kv_load_bytes >= 0
```

### 5.2 `BatchShape`

新增只读聚合属性：

```python
kv_load_tokens: int
kv_load_bytes: int
kv_load_request_count: int
```

语义：

- `kv_load_tokens`：本 iteration 内所有 request slice 的 load token 总和。
- `kv_load_bytes`：本 iteration 内所有 request slice 的 load bytes 总和。
- `kv_load_request_count`：本 iteration 内需要 load 的 request slice 数。

这些字段是 latency backend input，不是 report 层临时统计。

### 5.3 `ShapeKey`

新增字段：

```python
kv_load_tokens: int = 0
kv_load_bytes: int = 0
kv_load_request_count: int = 0
```

语义：

- latency memoization 必须区分相同 compute shape、不同 KV load shape。
- S8-B/S8-D 后，`ServingLatencyProfile` 的 `kv_load_component` 会依赖这些字段。

示例：

```text
same scheduled_prefill_tokens = 128
same batch_size = 2
different kv_load_bytes = 0 vs 1048576
=> different ShapeKey
```

## 6. 核心算法逻辑

### 6.1 Slice 校验

伪代码：

```text
ScheduledSlice.__post_init__:
  validate existing token invariants
  if kv_load_tokens < 0:
    raise ValueError
  if kv_load_bytes < 0:
    raise ValueError
```

不在 S8-A 中建立 `kv_load_bytes == kv_load_tokens * kv_bytes_per_token` 的强不变量。原因：

- token-linear 模式可以没有 bytes。
- byte-linear 模式的 bytes 口径会在 S8-B/S8-D 中由 profile/request metadata 明确。
- 过早强绑定会影响后续 layerwise / path-aware 扩展。

### 6.2 Batch 聚合

伪代码：

```text
BatchShape.kv_load_tokens:
  return sum(slice.kv_load_tokens for slice in request_slices)

BatchShape.kv_load_bytes:
  return sum(slice.kv_load_bytes for slice in request_slices)

BatchShape.kv_load_request_count:
  return sum(1 for slice in request_slices
             if slice.kv_load_tokens > 0 or slice.kv_load_bytes > 0)
```

### 6.3 ShapeKey 生成

伪代码：

```text
ShapeKey.from_shape(..., shape):
  return ShapeKey(
    existing fields...
    kv_load_tokens=shape.kv_load_tokens,
    kv_load_bytes=shape.kv_load_bytes,
    kv_load_request_count=shape.kv_load_request_count,
  )
```

### 6.4 Memoization 影响

当前风险：

```text
shape A:
  prefill=128
  kv_load_bytes=0

shape B:
  prefill=128
  kv_load_bytes=1GiB
```

如果 `ShapeKey` 不包含 KV load 字段，S8-B 后两者会错误复用 latency。S8-A 先修正 key schema，避免后续行为接入时产生隐蔽 bug。

## 7. 对核心 replay 语义的影响

### 7.1 是否改变 cached_tokens

不改变。

S8-A 不修改 cache lookup，也不修改 `account_prefix_lookup()`。

### 7.2 是否改变 hbm_hit_tokens / ddr_hit_tokens / miss_tokens

不改变。

S8-A 只增加 shape 字段，默认值为 0。不会改变 Step7 tier accounting。

### 7.3 是否改变 finish_time / ttft_ms

不改变。

S8-A 不实现 KV load component，也不修改 replay event loop。所有现有 `finish_time` / `ttft_ms` 应保持一致。

### 7.4 是否改变 cache event 顺序

不改变。

S8-A 不修改 cache event schema、event sink、HBM/DDR cache backend。

### 7.5 是否改变 materialization timing

不改变。

finish-time materialization 继续保持当前语义。

### 7.6 是否改变实例隔离

不改变。

新增字段在 `BatchShape` 内仍按单实例 iteration 表达，不引入跨实例共享状态。

### 7.7 是否影响 true streaming 大 trace

理论上不改变 replay 结果。

注意点：

- `BatchShape` 新字段默认值为 0，所以 streaming replay 现有 scheduler 输出不需要立刻修改行为。
- `ShapeKey.__str__()` 字符串会增加 KV load 片段；如果有测试或外部脚本硬匹配完整 shape key 字符串，需要更新预期。
- streaming 大 trace 的内存行为不应变化。

## 8. 测试计划

### 8.1 单测

新增：

```text
tests/unit/scheduler/test_batch_shape_kv_load.py
tests/unit/latency/test_shape_key_kv_load.py
```

可能修改：

```text
tests/unit/latency/test_shape_memo.py
```

覆盖：

- 默认值兼容。
- 聚合正确。
- 非负校验。
- `ShapeKey` 区分 KV load shape。
- `ShapeMemo` 不会跨 KV load shape 复用。

### 8.2 集成测试

S8-A 不新增集成测试。

原因：

- S8-A 不改变 replay 行为。
- 非零 KV load 还没有接入 replay。

但开发完成后建议运行现有相关集成测试作为回归：

```text
tests/integration/test_step7_streaming_hbm_ddr_integration.py
tests/integration/test_step7_report_metrics_e2e.py
```

### 8.3 小 E2E

S8-A 不新增新的小 E2E。

建议运行现有小 E2E，确认默认 0 KV load shape 不破坏已有链路：

```text
tests/integration/test_streaming_runtime_integration.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

### 8.4 是否需要 golden 更新

原则上不需要更新 CSV golden。

可能需要更新：

- 如果测试或输出中硬匹配完整 `shape_key` 字符串，需要补充 `kvload_tokens=0|kvload_bytes=0|kvload_reqs=0`。

当前计划不主动修改 report/export，因此不更新 `capacity_sweep.csv` 字段。

### 8.5 建议执行命令

开发后建议执行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/latency/test_shape_key_kv_load.py \
  tests/unit/latency/test_shape_memo.py \
  tests/unit/latency/test_fitted_ttft_backend.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/scheduler/test_vllm_like_scheduler.py
```

如果 `.venv` 中已有 ruff：

```bash
.venv/bin/ruff check src/infertwin/scheduler/batch_shape.py src/infertwin/latency/schema.py tests/unit/scheduler/test_batch_shape_kv_load.py tests/unit/latency/test_shape_key_kv_load.py
```

最后执行：

```bash
git diff --check
```

## 9. 风险与回滚边界

### 9.1 风险

1. `ShapeKey.__str__()` 变化可能影响硬匹配测试或临时分析脚本。
2. 直接构造 `ShapeKey(...)` 的测试需要默认字段或 helper 更新。
3. 直接构造 `ScheduledSlice(...)` 的测试很多，新增字段必须有默认值，避免连锁修改。
4. 如果把 S8-D 行为提前塞进 S8-A，会模糊 batch 边界。

### 9.2 回滚边界

S8-A 的回滚很清晰：

- 回滚 `ScheduledSlice` 新字段。
- 回滚 `BatchShape` 新属性。
- 回滚 `ShapeKey` 新字段和 `__str__()` 片段。
- 删除新增测试文件。

回滚后不应影响 cache backend、scheduler admission、replay event loop、streaming shard、report/export。

## 10. 完成后如何判断可以进入下一个 Batch

可以进入 S8-B 的条件：

1. `ScheduledSlice` 能表达 slice-level KV load tokens/bytes。
2. `BatchShape` 能稳定聚合 iteration-level KV load shape。
3. `ShapeKey` 能区分同 prefill compute、不同 KV load 的 iteration。
4. 现有 HBM-only / Step7 DDR accounting 测试不受影响。
5. 新增单测通过。
6. `git diff --check` 通过。
7. 没有修改方案外文件；如发生越界修改，已重新评审。

S8-A 完成后，S8-B 可以在不再修改 shape schema 的前提下新增 `KVLoadLatencyComponent`。

## 11. 需要用户审批的内容

请用户审批以下决定后，再进入 S8-A 代码开发：

1. 接受 S8-A 属于核心仿真器，改动等级为 L3。
2. 接受 S8-A 只做 shape schema 和 memo key，不接入 replay 行为。
3. 接受在 `ScheduledSlice` 增加 `kv_load_tokens` / `kv_load_bytes`，默认 0。
4. 接受在 `BatchShape` 增加 `kv_load_tokens` / `kv_load_bytes` / `kv_load_request_count` 聚合属性。
5. 接受在 `ShapeKey` 增加 `kv_load_tokens` / `kv_load_bytes` / `kv_load_request_count`，默认 0。
6. 接受 `ShapeKey.__str__()` 增加 KV load 片段。
7. 接受本 Batch 不修改 replay event loop、不改变 finish time、不改变 TTFT。
8. 接受本 Batch 不更新 report/export 字段。
9. 接受本 Batch 允许修改的文件范围仅限第 4 节列出的源码和测试文件。
10. 接受如果发现必须修改方案外文件，则暂停并重新评审。
