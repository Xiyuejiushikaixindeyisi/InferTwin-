# Step8 整体 Review：KV Load Latency

日期：2026-06-29

任务类型：核心仿真器 review。

本轮 review 发现 1 个 resolver E2E 接口失配问题；用户确认后已执行小型 Step8 repair batch。repair 只更新测试断言，不新增功能，不修改核心 replay 业务代码。

## 1. Review 结论

Step8 主目标完成：InferTwin 已能把 DDR/CPU hit 的 KV load latency 纳入 batch-aware replay 的 iteration duration、request finish time 和 TTFT，并通过 typed metrics 暴露 `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms`。

本次整体 review 初次跑全量 pytest 时发现 1 个失败：

```text
tests/integration/test_instance_runtime_resolver_e2e.py::test_instance_runtime_and_latency_resolvers_bind_synthetic_trace_instances
AttributeError: 'ServingLatencyProfile' object has no attribute 'ms_per_uncached_token'
```

判断：这是 Step8 后 latency resolver 返回类型从旧 `FittedTTFTLatencyBackend` 变为 `ServingLatencyProfile` 后，旧 E2E 测试仍按旧接口读取 `ms_per_uncached_token` 导致的接口/测试失配。它不影响 Step8 targeted replay 主链路，但会阻塞全量测试通过。

Repair 处理：已将 `tests/integration/test_instance_runtime_resolver_e2e.py` 调整为 Step8 后的组合式 latency backend 口径，显式断言返回 `ServingLatencyProfile`，并通过 `latency_backend.ttft_backend.ms_per_uncached_token` 验证 fitted TTFT 参数。全量 pytest 已恢复通过。

建议：可以进入 Step8 工程收口。

## 2. Review 输入

优先读取：

- `docs/agent_development_context.md`
- `docs/archive/step8/05_technical_route.md`
- `docs/archive/step8/s8_a_*` 到 `s8_g_*` batch 方案与执行记录
- `docs/archive/step8/06_calibration_boundary.md`
- Step8 相关源码和测试
- 本轮重新运行的测试输出

重点源码：

- `src/infertwin/scheduler/batch_shape.py`
- `src/infertwin/scheduler/state.py`
- `src/infertwin/scheduler/vllm_like.py`
- `src/infertwin/replay/event_loop.py`
- `src/infertwin/replay/metrics.py`
- `src/infertwin/latency/schema.py`
- `src/infertwin/latency/profile.py`
- `src/infertwin/latency/kv_load.py`
- `src/infertwin/latency/instance_resolver.py`
- `src/infertwin/config/profiles.py`
- `src/infertwin/streaming/metrics.py`
- `src/infertwin/experiment/sweep.py`
- `src/infertwin/external/kv_load_calibration.py`

## 3. Step8 实际完成了什么

Step8 已完成：

- 在 scheduler shape 层新增 KV load shape：
  - `ScheduledSlice.kv_load_tokens`
  - `ScheduledSlice.kv_load_bytes`
  - `BatchShape.kv_load_tokens`
  - `BatchShape.kv_load_bytes`
  - `BatchShape.kv_load_request_count`
- 在 latency memo key 中纳入 KV load dimensions：
  - `ShapeKey.kv_load_tokens`
  - `ShapeKey.kv_load_bytes`
  - `ShapeKey.kv_load_request_count`
- 新增 KV load latency component：
  - `ZeroKVLoadLatencyComponent`
  - `TokenLinearKVLoadLatencyComponent`
  - `ByteLinearKVLoadLatencyComponent`
- `KVLoadLatencyProfile` 支持显式 mode：
  - `zero`
  - `token_linear_v1`
  - `byte_linear_v1`
- `ServingLatencyProfile` 已按以下口径组合 iteration duration：

```text
iteration_duration_ms =
  queue_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

- `InstanceLatencyBackendResolver` 已把 instance/model profile 中的 `kv_load` 接入 `ServingLatencyProfile`。
- replay 中 DDR hit request 第一次被 scheduler 选中时收取 KV load latency，后续 chunk 不重复收费。
- `miss_tokens == 0 and ddr_hit_tokens > 0` 进入 load-only finish，不再走 HBM-only immediate finish。
- HBM-only zero-miss 继续 immediate finish。
- request / iteration / streaming / capacity sweep typed result 已增加 KV load 指标。
- report/export 只消费 typed result，不重算 KV load latency。
- Ramulator2 / Mooncake 边界已作为 calibration source / profile 参数来源沉淀，不进入在线 replay。

## 4. Step8 没有完成什么

Step8 明确未实现：

- compute/load overlap。
- KV load queue、shared bandwidth backpressure、priority。
- load completion event。
- DDR hit promotion 到 HBM。
- layer / page / chunk 级 KV load 拆分。
- Ramulator2 / Mooncake online replay。
- remote KV load。
- SSD tier。
- cross-instance pooling。
- progressive chunk/block visibility。
- Decode / TPOT 建模。
- complex Hybrid cache group。

这些未完成项与 Step8 技术路线一致；其中 progressive chunk/block visibility 是 Step9 核心任务。

## 5. 对仿真器处理逻辑的改变

### 5.1 trace schema guard

无改变。

Step8 不新增 trace 字段，不改变 routed trace 要求，不改变空 `instance_uuid` fail-fast 语义，也不改变 `streaming.require_sorted_trace=false` 的 V1 禁用规则。

### 5.2 request build

无核心语义改变。

Step8 不改 request parser、model resolver、long request rejection、request shard build。KV load bytes 目前来自已构造的 block metadata，例如 `PrefixBlock.size_bytes`，不要求 trace 增加真实 KV tensor 信息。

### 5.3 tokenizer / chat template

无改变。

Step8 不修改 tokenizer registry、chat template、GLM profile 或 request params parsing。

### 5.4 prefix block hash

无改变。

Step8 不改变 block hash、block boundary、runtime/effective block size conversion 或 vLLM-like cached_tokens accounting。`ddr_hit_tokens` 仍由 Step7 tiered cache lookup 与 accounting 决定。

### 5.5 scheduler replay

有改变，属于 Step8 核心 replay 改动。

新增逻辑：

- `RequestState` 持有一次性的 `pending_kv_load_tokens` / `pending_kv_load_bytes`。
- scheduler 为 request 构造首个 `ScheduledSlice` 时调用 `consume_pending_kv_load()`。
- 同一 request 后续 chunk 不再携带 KV load shape。
- zero prefill 但存在 KV load 的 request 可以生成 load-only slice。

影响：

- DDR hit request 的 first scheduled iteration 可能增加 duration。
- HBM-only zero-miss 不受影响。
- admission / chunk selection token budget 规则未改变。

### 5.6 cache lookup / materialization / eviction

不改变状态转移。

Step8 只消费 cache lookup 结果：

- `hbm_hit_tokens`
- `ddr_hit_tokens`
- `ddr_hit_bytes`
- `miss_tokens`

保持：

- HBM lookup first。
- DDR lookup second。
- finish-time materialization。
- HBM / DDR LRU eviction policy。
- materialize 时写 HBM 和 DDR。
- DDR hit 不 promotion。

### 5.7 latency backend

有改变，是 Step8 核心目标。

Step8 后默认 replay-facing latency backend 可以是 `ServingLatencyProfile`，由以下部分组成：

- fitted TTFT component。
- zero queue component。
- KV load component。

`KVLoadLatencyProfile(mode=zero)` 保持旧行为；`token_linear_v1` / `byte_linear_v1` 让 DDR/CPU hit 产生 KV load latency。`byte_linear_v1` 在存在 load tokens 但 bytes 缺失时 fail-fast。

### 5.8 per-instance isolation

保持。

每个 fixed-routed instance 仍独立 replay，独立 cache，独立 latency backend。Step8 的 KV load component 通过 instance/model latency profile 解析，不引入跨实例共享状态。

### 5.9 typed metrics / typed result

有字段扩展。

新增或扩展字段包括：

- request metrics：
  - `kv_load_tokens`
  - `kv_load_bytes`
  - `kv_load_ms`
  - `prefill_compute_ms`
  - `queue_ms`
- iteration metrics：
  - `kv_load_tokens`
  - `kv_load_bytes`
  - `kv_load_request_count`
  - `kv_load_ms`
  - `prefill_compute_ms`
  - `queue_ms`
- capacity sweep row：
  - `total_kv_load_ms`
  - `avg_kv_load_ms`
  - `p50_kv_load_ms`
  - `p90_kv_load_ms`
  - `p99_kv_load_ms`

流式聚合路径只从 `BatchAwareRequestMetrics` 聚合，不重算 replay。

## 6. 是否改变核心 replay 语义

结论：Step8 改变了 latency timeline，但没有改变 cache hit accounting。

改变：

- DDR/CPU hit request 的 `finish_time_ms` / `ttft_ms` 可以因 `kv_load_ms` 增加。
- iteration duration 可以从原来的 prefill compute duration 变为 `queue + compute + kv_load`。
- `miss_tokens == 0 and ddr_hit_tokens > 0` 的 request 需要 load-only finish。

不改变：

- `cached_tokens` 口径。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` 口径。
- HBM / DDR lookup 顺序。
- materialization timing。
- HBM / DDR eviction policy。
- cache event 顺序。
- fixed-routing multi-instance isolation。
- tokenizer / chat template / prefix hash。

因此 Step8 属于 L3 核心 replay / latency 改动，但改动范围符合技术路线。

## 7. 与技术路线一致性

总体一致。

技术路线要求：

- Step8 是核心仿真器能力：已满足。
- 不改变 trace schema：已满足。
- 不改变 tokenizer / chat template / prefix block hash：已满足。
- 不改变 HBM / DDR / miss token accounting：已满足。
- 默认 `overlap_mode=none_v1`：已满足。
- 默认 `aggregation=shared_link_sum`：已满足。
- KV load 在 request 第一次被 scheduler 选中时收费：已满足。
- HBM-only zero-miss immediate finish：已满足。
- DDR hit zero-miss load-only finish：已满足。
- 不做 promotion / load completion / load queue / online external replay：已满足。
- report/export 消费 typed result：已满足。

唯一发现的问题不是技术路线偏离，而是旧 E2E 测试仍依赖旧 latency backend 接口，导致全量 pytest 不通过。

## 8. 测试结果

### 8.1 Step8 targeted tests

命令：

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
```

结果：

```text
87 passed in 7.42s
```

### 8.2 全量 pytest

初次 review 运行结果：

命令：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest
```

结果：

```text
367 collected
366 passed
1 failed
```

失败：

```text
tests/integration/test_instance_runtime_resolver_e2e.py::test_instance_runtime_and_latency_resolvers_bind_synthetic_trace_instances
AttributeError: 'ServingLatencyProfile' object has no attribute 'ms_per_uncached_token'
```

原因判断：

- Step8 后 `build_instance_latency_backend_resolver()` 返回的 concrete backend 可以是 `ServingLatencyProfile`。
- `ServingLatencyProfile` 持有 `ttft_backend`，其中包含 `ms_per_uncached_token`。
- 旧 E2E 测试仍把返回值当作 `FittedTTFTLatencyBackend`，直接读取 `latency_backend.ms_per_uncached_token`。

建议修复方向需要用户确认：

1. 更新测试，改为通过 `latency_backend.ttft_backend.ms_per_uncached_token` 验证 fitted TTFT 参数。
2. 或在 `ServingLatencyProfile` 上新增只读 passthrough property，保持旧测试和外部脚本兼容。

从接口清晰度看，方案 1 更符合 Step8 后的组合式 latency 设计；从外部兼容性看，方案 2 更稳。建议单独开小 repair batch 决策。

Repair 后运行结果：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest

367 passed in 16.87s
```

Repair 内容：

- `tests/integration/test_instance_runtime_resolver_e2e.py` 不再把 resolver 返回值当作旧 `FittedTTFTLatencyBackend`。
- 测试显式断言 `latency_backend` 是 `ServingLatencyProfile`。
- fitted TTFT 参数从 `latency_backend.ttft_backend.ms_per_uncached_token` 读取。
- model default fallback 的 wrapper profile 名调整为 `model__default_latency` 口径。

### 8.3 Ruff

命令：

```text
TMPDIR=/tmp .venv/bin/ruff check src tests scripts
```

结果：

```text
All checks passed!
```

### 8.4 Diff Check

命令：

```text
git diff --check
```

结果：通过。

## 9. 代码质量评审

### 9.1 模块职责

总体良好。

- scheduler 只负责把 pending KV load shape 放进首个 scheduled slice。
- replay event loop 只负责应用 schedule result、记录 latency attribution、finish/materialize。
- cache backend 继续只负责 lookup/materialize/evict，不承担 latency 估算。
- latency module 负责 `ServingLatencyProfile` 和 KV load component。
- streaming/report 只消费 typed metrics。
- external calibration helper 不运行外部工具，不进入 replay 主路径。

### 9.2 Schema 稳定性

总体良好。

- 新字段位于 dataclass 尾部并有默认值。
- `KVLoadLatencyProfile.mode` 显式化，避免非零系数却默认 zero 的隐式行为。
- `byte_linear_v1` 对缺失 bytes fail-fast。
- `remote_ms_per_cached_token` 保留但非零会在 build component 时 fail-fast，避免静默假装支持 remote KV load。

风险：

- `ShapeKey.__str__()` 增加 KV load dimensions，可能影响硬匹配脚本；这是合理变更，因为不同 KV load shape 不应复用 latency memo。
- resolver concrete return type 变化曾导致旧 E2E 失败；已通过小型 repair 更新测试到 `ServingLatencyProfile` 组合式 backend 口径。

### 9.3 可测试性

良好。

已有测试覆盖：

- `ScheduledSlice` / `BatchShape` validation。
- request state pending KV load one-shot。
- `ShapeKey` KV load dimensions。
- zero / token-linear / byte-linear component。
- `ServingLatencyProfile` load-only path。
- instance/model resolver 接入 KV load profile。
- replay DDR hit KV load latency。
- request-level latency contribution split。
- streaming metrics / capacity sweep metrics。
- external calibration fit。

缺口：

- 旧 resolver E2E 已通过 repair 适配 Step8 backend composition。
- 尚无 overlap、queue/backpressure、promotion、progressive visibility 测试，因为这些能力未实现。

### 9.4 可维护性

总体良好。

Step8 使用新 component 和 typed schema 扩展，而不是修改旧 fitted TTFT backend 或把 KV load 写进 cache backend，利于 Step9 / Step10 扩展。

需要注意：

- `ServingLatencyProfile` 逐渐成为 TTFT / queue / KV load / decode 的组合入口，后续扩展时要避免把它变成巨型“万能类”。建议后续继续用 component / policy 分拆。
- request-level `kv_load_ms` 当前是按 bytes 或 tokens 对 iteration latency 做确定性归因，不是硬件真实测量值；文档中已说明，后续不要把它误读为 per-request hardware timing。

### 9.5 性能风险

当前性能风险可控。

- 不保存真实 KV tensor。
- KV load shape 是少量整数，不会造成内存爆炸。
- streaming path 聚合 metrics，不重建完整 trace。
- KV load 按 iteration 聚合，避免 layer/page/chunk 级事件爆炸。

待后续处理：

- `ttft_values` / `kv_load_values` 仍用于 exact percentile，百万级 request 可能需要 quantile policy。
- layer/page/chunk 级 KV load 若在 Step9 或后续引入，需要控制事件数量和 memory footprint。
- online Ramulator2 / Mooncake 如果未来接入，必须 opt-in，不能进入默认大 trace 主路径。

## 10. 是否存在外围能力污染核心仿真器

未发现。

依据：

- `capacity_sweep.csv` / `summary.md` 只是消费 `CapacitySweepRow`。
- streaming metrics 从 `BatchAwareRequestMetrics` 聚合，不重算 cache hit 或 KV load latency。
- report/export 没有反向修改 replay、cache、scheduler 或 latency 语义。
- calibration helper 只产出 profile mapping，不直接参与 replay。

需要继续保持的边界：

- 外围能力不能在 CSV/export 中重算 `kv_load_ms`。
- 未来 hit floor search、dashboard、capacity planner 只能消费 typed result。

## 11. 遗留问题

已修复阻塞：

- 全量 pytest 失败：`ServingLatencyProfile` 与旧 E2E 测试的 `ms_per_uncached_token` 读取方式不兼容。
- 修复方式：更新 E2E，显式检查 `ServingLatencyProfile.ttft_backend.ms_per_uncached_token`。
- 修复后结果：`367 passed in 16.87s`。

Step8 已知非阻塞遗留：

- progressive chunk/block visibility 未实现，进入 Step9。
- compute/load overlap 未实现。
- KV load queue / bandwidth backpressure / priority 未实现。
- DDR hit promotion 未实现。
- load completion event 未实现。
- layer / page / chunk 级 KV load 拆分未实现。
- Ramulator2 / Mooncake 仍是 calibration boundary，未做 online replay。
- remote KV load、SSD tier、cross-instance pooling 未实现。
- Decode / TPOT 未建模，V2 pending。
- complex Hybrid cache group 未实现。

## 12. 是否建议进入工程收口

建议进入 Step8 工程收口。

原因：

- Step8 targeted tests 通过，说明核心 KV load 主链路是通的。
- Ruff 和 diff check 通过，说明代码风格和基础格式没问题。
- 全量 pytest 已通过，resolver / runtime integration 的 E2E 阻塞已解除。

建议下一步进入 Step8 工程收口，收口时记录以下验证基线：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
TMPDIR=/tmp .venv/bin/ruff check src tests scripts
git diff --check
```

结果：

```text
pytest: 367 passed
ruff check src tests scripts: passed
git diff --check: passed
```
