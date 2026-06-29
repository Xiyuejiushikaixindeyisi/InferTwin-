# S8-B 实施方案：KVLoadLatencyComponent

状态：待用户评审，尚未进入代码开发。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-B：KVLoadLatencyComponent。

前置条件：

- S8-A 已完成 `ScheduledSlice.kv_load_tokens` / `kv_load_bytes` 和 `ShapeKey` KV load 维度。
- Step8 技术路线中关于 fitted/static KV load component、`overlap_mode=none_v1`、`aggregation=shared_link_sum` 的决策已通过。

## 1. 类型与改动等级

本 Batch 属于核心仿真器。

改动等级：L3。

原因：

- 本 Batch 新增 replay-facing latency component。
- 本 Batch 扩展 `KVLoadLatencyProfile` schema。
- 本 Batch 会让手动构造的 `ServingLatencyProfile` 在非零 `BatchShape.kv_load_*` 下返回非零 `kv_load_ms`。

但本 Batch 不接入 replay event loop，不会让真实 trace replay 产生非零 KV load；这一步属于 S8-D。

## 2. 本 Batch 做什么

S8-B 只实现 KV load latency component 与 profile schema：

1. 新增 `KVLoadLatencyComponent` 实现模块。
2. 支持三种 mode：
   - `zero`
   - `token_linear_v1`
   - `byte_linear_v1`
3. 固定支持：
   - `aggregation=shared_link_sum`
   - `overlap_mode=none_v1`
4. 将 `KVLoadLatencyProfile` 从旧的占位字段升级为显式 mode schema。
5. 让 `ServingLatencyProfile` 可以组合真实 KV load component。
6. 允许 legacy/global `latency.backend=serving_latency_profile` 通过 config factory 构建 KV load component，用于单测和小型手动实验。
7. 增加单测覆盖 component 公式、schema guard 和 serving profile composition。

S8-B 完成后，可以独立验证：

```text
shape.kv_load_tokens / kv_load_bytes
-> KVLoadLatencyComponent
-> LatencyComponentResult(duration_ms=kv_load_ms)
-> ServingLatencyProfile.duration_ms = ttft_ms + queue_ms + kv_load_ms
```

## 3. 本 Batch 不做什么

S8-B 不做：

- 不修改 scheduler。
- 不修改 replay event loop。
- 不把 `ddr_hit_tokens` 写入 `ScheduledSlice.kv_load_tokens`。
- 不修正 zero-miss DDR load-only path。
- 不修改 `InstanceLatencyBackendResolver`。
- 不接入 instance/model latency profile 到 streaming runner。
- 不修改 request / iteration / sweep metrics。
- 不修改 report/export。
- 不接 Ramulator2 / Mooncake online replay。
- 不建模 load queue / backpressure。
- 不建模 compute/load overlap。
- 不建模 layerwise KV load。
- 不建模 DDR hit promotion、load completion event。
- 不建模 remote KV / SSD / cross-instance pooling。

如果开发中发现必须修改上述内容，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/latency/kv_load.py`

职责：

- 承载 KV load latency component 实现。
- 把 `BatchShape.kv_load_*` 转成 `LatencyComponentResult`。
- 保持 Ramulator2 / Mooncake 只作为 calibration source，不进入默认 replay。

计划新增：

```python
ZeroKVLoadLatencyComponent
TokenLinearKVLoadLatencyComponent
ByteLinearKVLoadLatencyComponent
build_kv_load_component(...)
```

边界：

- 不读取 trace。
- 不读取 cache。
- 不修改 replay state。
- 不进行 external simulator 调用。

### 4.2 `src/infertwin/config/profiles.py`

职责：

- 定义 model / instance profile schema。
- 解析 `InstanceLatencyProfile.kv_load` 和 model default latency 中的 `kv_load`。

计划修改 `KVLoadLatencyProfile`：

```python
mode: Literal["zero", "token_linear_v1", "byte_linear_v1"] = "zero"
aggregation: Literal["shared_link_sum"] = "shared_link_sum"
overlap_mode: Literal["none_v1"] = "none_v1"
transfer_path: str = "local_ddr_cpu"
ddr_fixed_overhead_ms: float = 0.0
ddr_ms_per_cached_token: float = 0.0
ddr_ms_per_byte: float = 0.0
remote_ms_per_cached_token: float = 0.0
calibrated_from: str = "manual_default"
```

兼容策略：

- `kv_load` 缺失：解析为 `mode=zero`。
- `kv_load` 存在但 `mode` 缺失且所有系数为 0：解析为 `mode=zero`，兼容当前配置。
- `kv_load` 存在但 `mode` 缺失且存在非零系数：fail-fast，要求用户显式写 `mode`。
- `mode=zero` 时出现非零 DDR 系数：fail-fast，避免用户以为已启用 KV load。
- `remote_ms_per_cached_token` 继续保留为未来 remote load 超参数，但 S8-B component builder 不消费 remote load。

### 4.3 `src/infertwin/latency/factory.py`

职责：

- 从 legacy/global latency config 构建 `BatchLatencyBackend`。

计划修改：

- 当 `latency.backend=serving_latency_profile` 时，支持读取：

```yaml
latency:
  serving_latency_profile:
    profile: glm-v5_serving_v1
    ttft_backend: fitted_ttft
    kv_load:
      mode: token_linear_v1
      aggregation: shared_link_sum
      overlap_mode: none_v1
      ddr_fixed_overhead_ms: 1.0
      ddr_ms_per_cached_token: 0.001
      calibrated_from: unit-test
```

- 使用 `build_kv_load_component(...)` 构造 `ServingLatencyProfile.kv_load_component`。

边界：

- 只支持 global/legacy config path。
- 不修改 `InstanceLatencyBackendResolver`；instance/model profile 接入属于 S8-C。

### 4.4 `src/infertwin/latency/profile.py`

职责：

- 组合 TTFT、queue、KV load component。

计划修改：

- 原则上不需要大改。
- 可仅更新 docstring / 默认 reason，使其不再写死 `hbm_only_replay`。
- 不从 `profile.py` 反向 import `kv_load.py`，避免循环依赖。

### 4.5 `tests/unit/latency/test_kv_load_latency.py`

职责：

- 覆盖 KV load component 公式和 guard。

计划新增测试：

1. `zero` mode 返回 0，`modeled=False`。
2. token-linear 无 load 时返回 0。
3. token-linear 有 load 时：

```text
duration = fixed_overhead + kv_load_tokens * ddr_ms_per_cached_token
```

4. byte-linear 有 load 时：

```text
duration = fixed_overhead + kv_load_bytes * ddr_ms_per_byte
```

5. byte-linear 遇到 `kv_load_tokens > 0 and kv_load_bytes == 0` 时 fail-fast。
6. unsupported aggregation / overlap mode fail-fast。
7. remote coefficient 非零在 component builder 中 fail-fast，避免误以为 Step8 支持 remote load。

### 4.6 `tests/unit/latency/test_serving_latency_profile.py`

职责：

- 覆盖 serving profile 对 KV load component 的组合。

计划修改：

- 保留现有 `StaticLatencyComponent` 测试。
- 新增或调整一个测试，使用真实 token-linear KV load component，证明：

```text
duration_ms = fitted_ttft_ms + queue_ms + kv_load_ms
details["kv_load_ms"] == expected
details["kv_load_modeled"] is True
```

### 4.7 `tests/unit/latency/test_backend_factory.py`

职责：

- 覆盖 legacy/global latency factory。

计划修改：

- 新增 `serving_latency_profile.kv_load` config 测试。
- 确认 factory 生成的 `ServingLatencyProfile` 在非零 KV load shape 上返回非零 `kv_load_ms`。

### 4.8 `tests/unit/config/test_instance_latency_profiles.py`

职责：

- 覆盖 `KVLoadLatencyProfile` schema。

计划修改：

- 更新已有 `kv_load` 示例为显式 mode，或增加显式 mode 新测试。
- 保留 `kv_load` 缺失默认为 zero 的测试。
- 增加：
  - missing mode + all zero coefficients 兼容。
  - missing mode + nonzero coefficient fail-fast。
  - unsupported mode fail-fast。
  - unsupported aggregation fail-fast。
  - unsupported overlap mode fail-fast。
  - `mode=zero` + nonzero DDR coefficient fail-fast。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 Component interface

S8-B 继续使用现有接口：

```python
class IterationLatencyComponent(Protocol):
    name: str

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        ...
```

新增 component 都实现该接口，不新增 replay engine 依赖。

### 5.2 `ZeroKVLoadLatencyComponent`

语义：

```text
mode = zero
duration_ms = 0
modeled = False
```

用途：

- 默认兼容。
- HBM-only / Step7 baseline。
- 未启用 KV load latency 的 profile。

### 5.3 `TokenLinearKVLoadLatencyComponent`

输入：

```text
shape.kv_load_tokens
shape.kv_load_request_count
```

公式：

```text
if shape has no KV load:
  kv_load_ms = 0
else:
  kv_load_ms =
    ddr_fixed_overhead_ms
    + shape.kv_load_tokens * ddr_ms_per_cached_token
```

说明：

- `fixed_overhead_ms` 只在本 iteration 有 load 时生效。
- `aggregation=shared_link_sum` 表示使用 iteration 内汇总 tokens。

### 5.4 `ByteLinearKVLoadLatencyComponent`

输入：

```text
shape.kv_load_tokens
shape.kv_load_bytes
shape.kv_load_request_count
```

公式：

```text
if shape has no KV load:
  kv_load_ms = 0
else if shape.kv_load_tokens > 0 and shape.kv_load_bytes == 0:
  fail-fast
else:
  kv_load_ms =
    ddr_fixed_overhead_ms
    + shape.kv_load_bytes * ddr_ms_per_byte
```

说明：

- byte-linear 是更接近模型/硬件差异的口径。
- bytes 信息缺失时不能静默退回 token-linear。

### 5.5 `KVLoadLatencyProfile`

新 schema：

```yaml
kv_load:
  mode: zero
  aggregation: shared_link_sum
  overlap_mode: none_v1
  transfer_path: local_ddr_cpu
  ddr_fixed_overhead_ms: 0.0
  ddr_ms_per_cached_token: 0.0
  ddr_ms_per_byte: 0.0
  remote_ms_per_cached_token: 0.0
  calibrated_from: manual_default
```

允许 mode：

```text
zero
token_linear_v1
byte_linear_v1
```

S8-B 只允许：

```text
aggregation = shared_link_sum
overlap_mode = none_v1
```

`transfer_path` 在 S8-B 只进入 details，用于解释 profile 来源，不切换真实传输实现。

## 6. 核心算法逻辑

### 6.1 load_active 判定

伪代码：

```text
load_active =
  shape.kv_load_request_count > 0
  or shape.kv_load_tokens > 0
  or shape.kv_load_bytes > 0
```

如果 `load_active` 为 false，所有非 zero component 返回 0，但 `modeled=True`，表示 profile 已启用，只是本 iteration 没有 load。

### 6.2 token-linear

伪代码：

```text
if not load_active:
  duration = 0
else:
  duration =
    ddr_fixed_overhead_ms
    + shape.kv_load_tokens * ddr_ms_per_cached_token
```

details 至少包含：

```text
mode
aggregation
overlap_mode
transfer_path
calibrated_from
kv_load_tokens
kv_load_bytes
kv_load_request_count
ddr_fixed_overhead_ms
ddr_ms_per_cached_token
load_active
```

### 6.3 byte-linear

伪代码：

```text
if not load_active:
  duration = 0
else if shape.kv_load_tokens > 0 and shape.kv_load_bytes == 0:
  raise ValueError("byte-linear KV load requires kv_load_bytes")
else:
  duration =
    ddr_fixed_overhead_ms
    + shape.kv_load_bytes * ddr_ms_per_byte
```

details 至少包含：

```text
mode
aggregation
overlap_mode
transfer_path
calibrated_from
kv_load_tokens
kv_load_bytes
kv_load_request_count
ddr_fixed_overhead_ms
ddr_ms_per_byte
load_active
```

### 6.4 profile guard

伪代码：

```text
if mode missing:
  if all coefficients are zero:
    mode = zero
  else:
    raise ValueError("kv_load.mode is required when coefficients are non-zero")

if mode == zero and ddr coefficients non-zero:
  raise ValueError

if mode == token_linear_v1 and ddr_ms_per_byte non-zero:
  raise ValueError

if mode == byte_linear_v1 and ddr_ms_per_cached_token non-zero:
  raise ValueError

if aggregation != shared_link_sum:
  raise ValueError

if overlap_mode != none_v1:
  raise ValueError
```

`remote_ms_per_cached_token`：

- schema 保留。
- S8-B component builder 如发现非零 remote coefficient，应 fail-fast。
- 远端 KV load 需要未来 `remote_kv_load_tokens / bytes` shape，不能在 Step8 v1 暗中折算到 DDR。

## 7. 对核心 replay 语义的影响

### 7.1 是否改变 cached_tokens

不改变。

S8-B 不碰 cache lookup 和 cached token accounting。

### 7.2 是否改变 hbm_hit_tokens / ddr_hit_tokens / miss_tokens

不改变。

S8-B 只消费 S8-A 的 `BatchShape.kv_load_*`，不会生成或修改 HBM/DDR/miss token。

### 7.3 是否改变 finish_time / ttft_ms

默认真实 replay 不改变。

原因：

- S8-D 尚未把 DDR hit 写入 `ScheduledSlice.kv_load_tokens`。
- 当前 scheduler 生成的 shape 默认 `kv_load_tokens=0`、`kv_load_bytes=0`。

但手动构造非零 KV load shape 并调用 `ServingLatencyProfile` 时，duration 会增加。这是 S8-B 的预期能力。

### 7.4 是否改变 cache event 顺序

不改变。

S8-B 不修改 cache event schema、event sink 或 HBM/DDR cache backend。

### 7.5 是否改变 materialization timing

不改变。

finish-time materialization 保持不变。

### 7.6 是否改变实例隔离

不改变。

S8-B 不修改 per-instance resolver 和 streaming runner。component 本身无共享状态。

### 7.7 是否影响 true streaming 大 trace

默认不影响。

注意：

- 如果用户使用 global `serving_latency_profile.kv_load`，但 replay 仍输出零 KV load shape，则 TTFT 不变。
- S8-B 不引入内存持有和事件缓存。
- S8-D 才会让 streaming replay 真实产生非零 KV load shape。

## 8. 测试计划

### 8.1 单测

新增：

```text
tests/unit/latency/test_kv_load_latency.py
```

修改：

```text
tests/unit/latency/test_serving_latency_profile.py
tests/unit/latency/test_backend_factory.py
tests/unit/config/test_instance_latency_profiles.py
```

覆盖：

- zero component。
- token-linear component。
- byte-linear component。
- fixed overhead 只在有 load 时生效。
- byte-linear missing bytes fail-fast。
- profile schema default zero。
- explicit mode schema。
- invalid mode / aggregation / overlap guard。
- factory 能把 `serving_latency_profile.kv_load` 构造成 component。

### 8.2 集成测试

S8-B 不新增集成测试。

原因：

- S8-B 不接入 replay event loop。
- 非零 KV load 真实进入 streaming replay 属于 S8-D。

建议开发后运行现有相关集成回归：

```text
tests/integration/test_step7_streaming_hbm_ddr_integration.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

### 8.3 小 E2E

S8-B 不新增小 E2E。

可通过 unit-level `ServingLatencyProfile` 测试模拟一轮 iteration：

```text
BatchShape(kv_load_tokens > 0)
-> ServingLatencyProfile
-> duration includes kv_load_ms
```

真实 trace E2E 延后到 S8-D/S8-E。

### 8.4 是否需要 golden 更新

不需要更新 CSV golden。

原因：

- S8-B 不修改 report/export。
- S8-B 不让 streaming replay 产生非零 KV load。

可能需要更新的只有 config/profile schema 单测预期。

### 8.5 建议执行命令

开发后建议执行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_backend_factory.py \
  tests/unit/config/test_instance_latency_profiles.py \
  tests/unit/latency/test_shape_key_kv_load.py \
  tests/unit/latency/test_shape_memo.py
```

如果 `.venv` 中已有 ruff：

```bash
.venv/bin/ruff check \
  src/infertwin/latency/kv_load.py \
  src/infertwin/latency/profile.py \
  src/infertwin/latency/factory.py \
  src/infertwin/config/profiles.py \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_backend_factory.py \
  tests/unit/config/test_instance_latency_profiles.py
```

最后执行：

```bash
git diff --check
```

## 9. 风险与回滚边界

### 9.1 风险

1. `KVLoadLatencyProfile` 从旧占位字段升级为显式 mode，可能影响旧测试或示例配置。
2. 如果 missing mode + nonzero coefficient 不 fail-fast，会隐藏用户配置错误。
3. 如果 byte-linear 在缺少 bytes 时静默退回 token-linear，会造成口径混乱。
4. 如果 `profile.py` 直接 import `kv_load.py`，可能引入循环依赖。
5. 如果 S8-B 提前修改 resolver，Batch 边界会和 S8-C 混淆。

### 9.2 回滚边界

S8-B 的回滚范围清晰：

- 删除 `src/infertwin/latency/kv_load.py`。
- 回滚 `KVLoadLatencyProfile` schema 到旧字段。
- 回滚 `latency.factory` 中对 `serving_latency_profile.kv_load` 的读取。
- 回滚 `ServingLatencyProfile` docstring / 测试调整。
- 删除新增 KV load component 单测。

回滚后不应影响 scheduler、cache、replay event loop、streaming shard、report/export。

## 10. 完成后如何判断可以进入下一个 Batch

可以进入 S8-C 的条件：

1. `KVLoadLatencyComponent` 三种 mode 单测通过。
2. `KVLoadLatencyProfile` 显式 mode schema 和 guard 单测通过。
3. `ServingLatencyProfile` 能组合真实 KV load component。
4. `ShapeKey` 已能区分 KV load shape，且 S8-A 测试仍通过。
5. 现有 Step7 / streaming 回归不受影响。
6. 未修改 replay event loop、scheduler、cache backend、report/export。
7. `ruff` 和 `git diff --check` 通过。

S8-B 完成后，S8-C 可以开始把 instance/model profile 中的 `kv_load` 超参数接入 `InstanceLatencyBackendResolver`。

## 11. 需要用户审批的内容

请用户审批以下决定后，再进入 S8-B 代码开发：

1. 接受 S8-B 属于核心仿真器，改动等级为 L3。
2. 接受 S8-B 只实现 KV load component 和 profile schema，不接 replay 行为。
3. 接受新增 `src/infertwin/latency/kv_load.py`。
4. 接受 `KVLoadLatencyProfile` 升级为显式 mode schema。
5. 接受 `kv_load` 缺失或 all-zero legacy 配置解析为 `mode=zero`。
6. 接受 missing mode + nonzero coefficient fail-fast。
7. 接受 `mode=zero` + nonzero DDR coefficient fail-fast。
8. 接受 S8-B 只支持 `aggregation=shared_link_sum`。
9. 接受 S8-B 只支持 `overlap_mode=none_v1`。
10. 接受 byte-linear 遇到 `kv_load_tokens > 0` 但 `kv_load_bytes == 0` 时 fail-fast。
11. 接受 remote coefficient 在 schema 中保留，但 component builder 暂不消费；若非零则 fail-fast，等待未来 remote load shape。
12. 接受 legacy/global `latency.backend=serving_latency_profile` 可在 factory 中读取 `serving_latency_profile.kv_load`。
13. 接受本 Batch 不修改 `InstanceLatencyBackendResolver`，该接入留给 S8-C。
14. 接受本 Batch 允许修改的文件范围仅限第 4 节列出的源码和测试文件。
15. 接受如果发现必须修改方案外文件，则暂停并重新评审。
