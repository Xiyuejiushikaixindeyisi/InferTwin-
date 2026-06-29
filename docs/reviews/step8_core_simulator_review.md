# Step8 Core Simulator Review

日期：2026-06-29

范围：Step8 KV load latency，覆盖 S8-A 到 S8-F 的核心仿真器改动与 S8-G 收口验证。

结论：Step8 已完成工程收口，可以进入 Step9 技术路线设计。Step8 review 期间发现 1 个 resolver E2E 旧接口断言失配，并已通过小型 repair 修复；全量 pytest、ruff 和 diff check 均已通过。下一阶段应聚焦 progressive chunk/block visibility，并以新 replay/cache mode 承接，不应修改当前 `batch_aware_hbm_ddr_lru` 的 finish-time materialization 默认语义。

## 1. Review Scope

本次 review 只评估核心仿真器 Step8 相关能力：

- KV load shape / schema。
- KV load latency component。
- instance / model resolver 对 KV load profile 的解析。
- replay 中 DDR hit 到 KV load latency 的接入。
- typed request / iteration / streaming / sweep metrics。
- Ramulator2 / Mooncake calibration boundary。

本次 review 不评估新的外围产品能力；report/export 只作为 typed result 消费方检查。

## 2. Step8 能力摘要

Step8 已完成以下能力：

- `ScheduledSlice` / `BatchShape` 显式携带 `kv_load_tokens`、`kv_load_bytes` 和 `kv_load_request_count`。
- `ShapeKey` 将 KV load shape 纳入 memo key，避免不同 KV load 形状复用错误 latency。
- `KVLoadLatencyProfile` 支持显式 mode：
  - `zero`：默认兼容模式，不增加 KV load latency。
  - `token_linear`：按 DDR cached tokens 估算。
  - `byte_linear`：按 DDR cached bytes 估算，缺少 bytes 时 fail-fast。
- `ServingLatencyProfile` 组合口径已变为：

```text
iteration_duration_ms =
  queue_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

- DDR hit request 在第一次被 scheduler 选中时收取 KV load latency，后续 chunk 不重复收费。
- `miss_tokens == 0 and ddr_hit_tokens > 0` 不再走 HBM-only immediate finish，而是进入 load-only finish。
- `miss_tokens == 0 and ddr_hit_tokens == 0` 的 HBM-only zero-miss 仍保持 immediate finish。
- request / iteration / streaming / capacity sweep typed result 已输出 KV load 字段。
- Ramulator2 / Mooncake 目前只作为 calibration source / adapter boundary，不进入在线 replay 主路径。

## 3. 核心链路影响评审

| 链路 | Step8 影响 | 结论 |
| --- | --- | --- |
| trace schema guard | 不改变 trace 输入 schema | 安全 |
| request build | 不改变 tokenizer、chat template、prefix hash 构造 | 安全 |
| tokenizer / chat template | 不改变 | 安全 |
| prefix block hash | 不改变 hash-only block 语义 | 安全 |
| scheduler replay | 增加 load-only slice 和 first-schedule KV load charging | 符合 Step8 设计 |
| cache lookup | 不改变 HBM / DDR / miss token accounting | 安全 |
| materialization | 不改变 finish-time materialization | 安全 |
| eviction | 不改变 HBM LRU / DDR LRU policy state transition | 安全 |
| latency backend | `ServingLatencyProfile` 接入 KV load component | Step8 核心改动 |
| per-instance isolation | 每个 instance 仍使用独立 replay/cache/latency backend | 安全 |
| typed metrics/result | 新增 KV load metrics，report 不重算语义 | 安全 |
| external adapter boundary | calibration helper 独立于 replay 主路径 | 安全 |

Step8 会改变 DDR hit 请求的 `finish_time_ms` 和 `ttft_ms`，这是本 Step 的目标行为。Step8 不改变 `cached_tokens`、`hbm_hit_tokens`、`ddr_hit_tokens`、`miss_tokens`、cache event 顺序或 materialization timing。

## 4. 与真实 vLLM / vLLM-Ascend / Mooncake 的差异

Step8 后 InferTwin 与真实系统仍有以下差异：

- 不保存真实 KV tensor，只保存 block hash 和 metadata。
- 不建模 physical KV slot、pin/refcount、fragmentation。
- 默认 `overlap_mode=none_v1`，即 compute 和 KV load 在 iteration duration 中相加；真实系统可能存在 compute/load overlap。
- 不建模 KV load queue、shared bandwidth backpressure、stream priority 或 load completion event。
- DDR hit 当前不做 promotion；Step8 只计算 load latency，不改变 cache residency。
- 仍使用 finish-time materialization；长 prefill 期间的 progressive block reuse 可能被低估。
- Ramulator2 / Mooncake 不作为在线 replay 依赖，只作为未来校准来源。
- KV load v1 粒度是 scheduler iteration 聚合，不拆 layer / page / chunk；更细粒度建议放到 Step9 或后续专项。

这些差异已作为显式边界记录，不影响 Step8 收口，但会影响更高精度仿真。

## 5. 质量评审

功能完善度：

- Step8 v1 的目标已完成：非 HBM DDR hit 能产生 KV load latency，并进入 TTFT。
- 支持 zero / token-linear / byte-linear 三种 profile mode，足够支撑 v1 fitted/static latency。
- 外部 simulator 通过 calibration boundary 解耦，没有把巨型工具接入 replay 主循环。

代码结构：

- shape schema、latency component、resolver、replay integration、metrics、external calibration 分层清晰。
- report/export 消费 typed result，没有重算 replay 语义。
- Step8 未把 KV load 逻辑塞进 cache backend，避免把 hit accounting 和 latency accounting 混在一起。

测试覆盖：

- 覆盖 shape/schema、component、resolver、replay load-only path、metrics、streaming E2E 和 external adapter boundary。
- 覆盖 HBM-only zero-miss 与 DDR hit load-only 的分歧。
- 覆盖 byte-linear 缺少 bytes 时 fail-fast。

性能：

- 主路径仍是 hash-only cache metadata，不引入真实 KV tensor 存储。
- KV load 在 iteration 级聚合，避免 request 内 layer/page 级事件爆炸。
- request-level `kv_load_ms` 是按 bytes 或 tokens 对 iteration shared-link latency 的确定性归因，不是硬件观测值。

可维护性与可扩展性：

- 新的 latency component 可以扩展 overlap、queue/backpressure 或 calibrated model。
- Ramulator2 / Mooncake calibration 作为外围校准边界，后续可以 opt-in，不影响默认 replay。
- Step9 可以在新的 progressive mode 中改变可见性，而不破坏当前默认 mode。

## 6. 验证结果

已运行 Step8 targeted 测试：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/scheduler/test_request_state_kv_load.py \
  tests/unit/latency/test_shape_key_kv_load.py \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/unit/replay/test_step8_kv_load_replay.py \
  tests/unit/replay/test_step8_latency_contribution_metrics.py \
  tests/unit/streaming/test_metrics.py \
  tests/unit/experiment/test_sweep_metrics.py \
  tests/unit/external/test_kv_load_calibration.py \
  tests/unit/external/test_adapter_boundaries.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_step7_report_metrics_e2e.py \
  tests/integration/test_batch_d_runner.py

87 passed in 7.28s
```

repair 后 Step8 targeted 覆盖增加 resolver E2E：

```text
88 passed in 6.00s
```

已运行 ruff：

```text
TMPDIR=/tmp .venv/bin/ruff check <Step8 touched src/tests>

All checks passed!
```

已运行全量 pytest：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest

367 passed in 16.87s
```

已运行全量 ruff：

```text
TMPDIR=/tmp .venv/bin/ruff check src tests scripts

All checks passed!
```

已运行 diff check：

```text
git diff --check

passed
```

Step8 repair 说明：

- 初次全量 pytest 发现 `tests/integration/test_instance_runtime_resolver_e2e.py` 仍按旧 `FittedTTFTLatencyBackend` 接口读取 `ms_per_uncached_token`。
- repair 后该 E2E 显式断言 `ServingLatencyProfile`，并通过 `ttft_backend.ms_per_uncached_token` 验证 fitted TTFT 参数。
- repair 只修改测试断言，不修改核心 replay 业务代码。

## 7. 遗留问题

Step8 收口后仍存在以下遗留问题：

- progressive chunk/block visibility 未实现，放入 Step9。
- compute/load overlap 未实现，当前默认 `overlap_mode=none_v1`。
- KV load queue / bandwidth backpressure / priority 未实现。
- DDR hit promotion 未实现。
- load completion event 未实现。
- layer/page/chunk 级 KV load 拆分未实现。
- Ramulator2 / Mooncake 仍是 calibration boundary，未做在线 replay。
- Decode / TPOT 未建模，V2 pending。
- complex Hybrid cache group、SSD / remote tier、cross-instance pooling 仍未实现。

## 8. Step9 准入判断

结论：具备进入 Step9 技术路线设计条件。

判断依据：

- Step8 已把 DDR/CPU hit 的 KV load latency 纳入 `finish_time_ms` / `ttft_ms`。
- typed metrics 已能解释 `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms`。
- cache hit accounting、materialization、eviction 和 per-instance isolation 未被破坏。
- external calibration boundary 已与 replay 主路径解耦。
- Step9 的核心问题是“什么时候生成的 block 对后续请求可见”，可以通过新增 progressive replay/cache mode 处理。

进入 Step9 前不建议修改 Step8 默认行为。Step9 应明确新增 mode，例如：

```text
batch_aware_hbm_ddr_lru_progressive
```

并单独定义 chunk 完成、block materialization、subsequent lookup、TTFT chunk composition 的时间关系。
