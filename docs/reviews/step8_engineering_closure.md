# Step8 Engineering Closure

日期：2026-06-29

任务类型：核心仿真器工程收口。

结论：Step8 已完成工程收口，可以进入 Step9：progressive chunk/block visibility 技术路线设计。

## 1. 完成内容

Step8 已完成 DDR/CPU hit 的 KV load latency accounting：

- `ScheduledSlice` / `BatchShape` 显式携带 `kv_load_tokens`、`kv_load_bytes`、`kv_load_request_count`。
- `ShapeKey` 纳入 KV load dimensions，避免不同 KV load shape 复用错误 latency。
- `KVLoadLatencyProfile` 支持 `zero`、`token_linear_v1`、`byte_linear_v1`。
- `ServingLatencyProfile` 组合口径为：

```text
iteration_duration_ms =
  queue_ms
  + uncached_prefill_compute_ms
  + kv_load_ms
```

- DDR hit request 第一次被 scheduler 选中时收取 KV load latency，后续 chunk 不重复收费。
- `miss_tokens == 0 and ddr_hit_tokens > 0` 进入 load-only finish。
- HBM-only zero-miss 仍保持 immediate finish。
- request / iteration / streaming / capacity sweep typed result 已输出 KV load 字段。
- Ramulator2 / Mooncake 只作为 calibration source / adapter boundary，不进入默认在线 replay。

## 2. 验收结果

Step8 review 初次全量 pytest 发现 1 个旧 resolver E2E 接口断言失配；用户确认后已执行小型 repair。

repair 内容：

- `tests/integration/test_instance_runtime_resolver_e2e.py` 显式断言 `ServingLatencyProfile`。
- fitted TTFT 参数改为从 `latency_backend.ttft_backend.ms_per_uncached_token` 读取。
- model default fallback profile 名对齐为 `model__default_latency`。
- repair 只修改测试断言，不修改核心 replay 业务代码。

最终验证：

```text
Step8 targeted + resolver E2E: 88 passed
Full pytest: 367 passed
ruff check src tests scripts: passed
git diff --check: passed
```

## 3. 对核心 Replay 语义的影响

Step8 改变：

- DDR/CPU hit request 的 `finish_time_ms` / `ttft_ms` 可因 `kv_load_ms` 增加。
- iteration duration 从纯 prefill compute 扩展为 `queue + compute + kv_load`。
- DDR hit zero-miss request 不再 immediate finish，而是 load-only finish。

Step8 不改变：

- trace schema guard。
- request build。
- tokenizer / chat template。
- prefix block hash。
- `cached_tokens`。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`。
- HBM / DDR lookup 顺序。
- finish-time materialization。
- HBM / DDR eviction policy。
- cache event 顺序。
- fixed-routing multi-instance isolation。

## 4. 遗留问题

Step8 收口后仍未实现：

- progressive chunk/block visibility，进入 Step9。
- compute/load overlap。
- KV load queue、shared bandwidth backpressure、priority。
- load completion event。
- DDR hit promotion。
- layer / page / chunk 级 KV load 拆分。
- Ramulator2 / Mooncake online replay。
- remote KV load、SSD tier、cross-instance pooling。
- Decode / TPOT，V2 pending。
- complex Hybrid cache group。

这些不是 Step8 blocker；后续必须通过新 replay mode、latency component、cache backend、policy 或 adapter 接入，不得静默改变 Step8 默认语义。

## 5. Step9 准入判断

具备进入 Step9 的条件。

判断依据：

- Step8 已把 DDR/CPU hit 的 KV load latency 纳入 replay-facing TTFT。
- typed metrics 已能解释 `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms`。
- report/export 未污染核心 replay，只消费 typed result。
- full pytest、ruff、diff check 均通过。
- Step9 的核心问题是 block/chunk 何时可见，能够通过新增 progressive replay/cache mode 承接。

## 6. 风险与控制

风险：

- Step9 若直接修改 `batch_aware_hbm_ddr_lru` 的 finish-time materialization，会破坏 V1 默认语义。
- request-level `kv_load_ms` 是 iteration shared-link latency 的确定性归因，不是硬件真实逐请求测量值。
- 更细粒度 KV load 若按 layer/page/chunk 展开，可能带来事件量和内存压力。

控制：

- Step9 必须声明为核心仿真器能力。
- Step9 必须新增 mode，例如 `batch_aware_hbm_ddr_lru_progressive`。
- Step9 不应混入 gateway、remote pooling、Decode / TPOT 或 complex Hybrid cache group。
- 外围能力继续只消费 typed result，不重算 cache hit、TTFT 或 KV load。

## 7. 归档状态

`docs/step8/` 已在 S8-G 阶段移动到：

```text
docs/archive/step8/
```

本轮收口未重复移动目录。
