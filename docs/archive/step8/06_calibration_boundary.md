# Step8 Calibration Boundary：Ramulator2 / Mooncake 到 KVLoadLatencyProfile

状态：S8-F 开发文档。

阶段类型：核心仿真器的非 replay 支撑边界。

## 1. 结论

S8-F 不把 Ramulator2 或 Mooncake 接进 InferTwin 默认 replay 主路径。

推荐关系是：

```text
Ramulator2 / Mooncake benchmark / production measurement
  -> KV load calibration observations
  -> token-linear or byte-linear fit
  -> KVLoadLatencyProfile
  -> InferTwin replay uses the fitted profile
```

不推荐关系是：

```text
InferTwin replay
  -> online call Ramulator2 / Mooncake for every iteration
```

原因：

- Ramulator2 是 DRAM simulator，不理解 KV block、prefix cache、scheduler 或 TTFT。
- Mooncake 是真实 KV transfer / store 体系，包含 placement、lease、replica、transport、队列等复杂状态。
- InferTwin V1 的大 trace 主路径必须保持 deterministic、可测试、无外部重依赖。
- Step8 v1 的目标是把非 HBM hit 的 load latency 加入 replay timeline，而不是做完整存储通信仿真。

## 2. Calibration Observation

S8-F 使用 `KVLoadCalibrationObservation` 表示一个离线校准点：

```text
source
model_name
hardware_name
transfer_path
kv_load_tokens
kv_load_bytes
kv_load_request_count
batch_size
duration_ms
note
```

含义：

- `source`：数据来源，例如 `ramulator2_git`、`mooncake_benchmark`、`production_measurement`。
- `model_name`：模型名，防止把不同模型的 KV bytes 口径混合。
- `hardware_name`：硬件名，防止跨硬件混合拟合。
- `transfer_path`：传输路径口径，例如 `local_ddr_cpu`、`mooncake_rdma`、`mooncake_ascend`。
- `kv_load_tokens`：该校准点加载的 KV token 数。
- `kv_load_bytes`：该校准点加载的 KV bytes。
- `kv_load_request_count`：该校准点中参与 load 的 request 数。
- `batch_size`：该校准点的 benchmark batch size 或 scheduler slice 数。
- `duration_ms`：观测到的 KV load latency。

fit helper 会要求同一组 observation 的 `model_name`、`hardware_name`、`transfer_path` 一致。

## 3. Token-Linear 与 Byte-Linear

### Token-Linear

适合场景：

- 当前只能稳定拿到 `ddr_hit_tokens`。
- `kv_bytes_per_token` 口径暂时不可靠。
- 同一模型和部署形态下做快速近似。

输出 profile：

```yaml
kv_load:
  mode: token_linear_v1
  aggregation: shared_link_sum
  overlap_mode: none_v1
  transfer_path: local_ddr_cpu
  ddr_fixed_overhead_ms: 1.0
  ddr_ms_per_cached_token: 0.02
  calibrated_from: ramulator2_git:example_run
```

### Byte-Linear

适合场景：

- 已经能稳定计算 `kv_load_bytes`。
- 需要区分不同模型 KV tensor 大小。
- 参数来自存储 / 通信 benchmark，天然以 bytes 或 bandwidth 表达。

输出 profile：

```yaml
kv_load:
  mode: byte_linear_v1
  aggregation: shared_link_sum
  overlap_mode: none_v1
  transfer_path: mooncake_ascend
  ddr_fixed_overhead_ms: 1.0
  ddr_ms_per_byte: 0.0000002
  calibrated_from: mooncake_benchmark:example_run
```

`byte_linear_v1` 在 replay 中遇到需要 load tokens 但缺少 bytes 时会 fail-fast，不能静默猜测。

## 4. `calibrated_from` 命名

S8-F 不把 `calibrated_from` 改成 enum，仍保持 string，避免破坏现有 profile。

推荐命名：

```text
manual_default
synthetic:<name>
ramulator2_git:<run_id>
mooncake_benchmark:<run_id>
production_measurement:<run_id>
company_AIConfigurator:<run_id>
aiconfigurator_git:<run_id>
```

说明：

- `aiconfigurator_git` 表示开源项目测试口径。
- `company_AIConfigurator` 表示公司内修改版口径。
- `ramulator2_git` 表示本地或外部 Ramulator2 校准实验。
- `mooncake_benchmark` 表示 Mooncake benchmark 或实测数据。

## 5. Ramulator2 边界

Ramulator2 可用于：

- 生成 DRAM latency / bandwidth 参考。
- 标定 `ddr_ms_per_byte`。
- 对不同 memory config 做离线 sweep。

S8-F 不做：

- 不生成 memory address trace。
- 不解析 Ramulator2 stats。
- 不执行 Ramulator2。
- 不把 `Ramulator2Adapter.estimate_kv_restore()` 接入 replay。

如果未来要做 Ramulator2 calibration harness，应另开 opt-in 工具：

```text
representative KV load shape
  -> memory trace generation
  -> run Ramulator2
  -> parse stats
  -> emit KVLoadCalibrationObservation
```

## 6. Mooncake 边界

Mooncake 可用于：

- 通过 benchmark 或实测日志得到 KV get/load duration。
- 区分 `protocol=rdma`、`protocol=ascend` 等传输口径。
- 为未来 remote KV / multi-instance pooling 建模提供数据来源。

S8-F 不做：

- 不 import Mooncake SDK。
- 不调用 `TransferEngine`。
- 不建模 replica placement、lease、pin、eviction。
- 不建模 local/remote/SSD fallback 的真实队列。

这些都属于 Step8 之后的存储/通信专项或 V2 能力。

## 7. 与 Replay 的关系

S8-F 不改变：

- `cached_tokens`。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`。
- `finish_time` / `ttft_ms` 计算方式。
- cache event 顺序。
- finish-time materialization。
- 实例隔离。
- true streaming 大 trace 主路径。

S8-F 只提供 profile 参数来源。只有当用户把拟合后的 `kv_load` profile 写入 model / instance latency config 后，Step8 已有的 `KVLoadLatencyComponent` 才会影响 replay TTFT。

## 8. 示例流程

```python
from infertwin.external.kv_load_calibration import (
    KVLoadCalibrationObservation,
    fit_byte_linear_v1,
    to_kv_load_profile_mapping,
)

observations = [
    KVLoadCalibrationObservation(
        source="mooncake_benchmark",
        model_name="glm-v5.1",
        hardware_name="ascend910c",
        transfer_path="mooncake_ascend",
        kv_load_tokens=1024,
        kv_load_bytes=92_012_544,
        kv_load_request_count=1,
        batch_size=1,
        duration_ms=6.5,
    ),
    KVLoadCalibrationObservation(
        source="mooncake_benchmark",
        model_name="glm-v5.1",
        hardware_name="ascend910c",
        transfer_path="mooncake_ascend",
        kv_load_tokens=2048,
        kv_load_bytes=184_025_088,
        kv_load_request_count=1,
        batch_size=1,
        duration_ms=12.0,
    ),
]

fit = fit_byte_linear_v1(
    observations,
    calibrated_from="mooncake_benchmark:glm-v5.1_ascend910c_2026-06-29",
)
profile_mapping = to_kv_load_profile_mapping(fit)
```

`profile_mapping` 可以人工写入 model default latency 或 instance latency profile。

## 9. 后续扩展

未来如果要继续细化，可以新增独立 batch：

- CSV/JSON calibration observation parser。
- opt-in calibration CLI。
- Ramulator2 stats parser。
- Mooncake benchmark parser。
- profile yaml writer。
- queue/backpressure model。
- overlap model。
- layerwise / chunkwise KV load。

这些扩展不得反向修改 Step8 v1 默认 replay 语义。

