# S9-I Execution Record: E2E

状态：已完成。

本文件记录 S9-I 的端到端验收。S9-I 不做归档、不做工程收口、不更新主文档和全局记忆。

## 1. Batch 定位

S9-I 属于核心仿真器的端到端验收 batch。

改动等级：L3 验收。

本 Batch 的目标不是新增 replay 语义，而是验证 S9-B 到 S9-H 已实现的 Step9
progressive timeline 能通过正式大 trace 主路径稳定工作：

```text
CSV trace
  -> tokenizer / chat template
  -> prefix block hash
  -> streaming shard build
  -> per-instance streaming replay
  -> scheduler chunked prefill
  -> HBM / DDR lookup
  -> progressive full-block materialization
  -> KV load wait
  -> typed metrics
  -> capacity_sweep.csv / summary.md
```

## 2. 本 Batch 做什么

S9-I 新增一个 CLI 级 synthetic E2E：

```text
tests/integration/test_step9_streaming_cli_e2e.py
```

该 E2E 覆盖：

- `infertwin` package CLI：`sweep-streaming`。
- routed CSV trace。
- simple tokenizer profile。
- model registry。
- instance runtime / instance latency profile。
- `cache.mode=batch_aware_hbm_ddr_lru_progressive_timeline`。
- 两档 HBM capacity：`1` 和 `4`。
- instance-local HBM + DDR tiered cache。
- progressive chunk materialization events。
- DDR lookup hit events。
- token-linear KV load latency。
- per-instance cache isolation。
- `capacity_sweep.csv` Step9 timeline fields。
- `summary.md` Timeline Results。

## 3. 本 Batch 不做什么

S9-I 不做：

- 不新增 replay state。
- 不改变 scheduler selection。
- 不改变 cache lookup / materialization / eviction。
- 不改变 TTFT composition。
- 不改变 latency backend。
- 不新增 per-chunk timeline dump。
- 不做 archive。
- 不做工程收口。
- 不更新主文档和全局记忆。

## 4. 合成数据设计

E2E 构造 3 条请求：

| request | instance | prompt | 目的 |
| --- | --- | --- | --- |
| `req-a1` | `instance-a` | long repeated prompt | 产生 miss chunks，并 progressive materialize full blocks |
| `req-a2` | `instance-a` | same prompt | 在 HBM capacity=1 下触发 DDR hit 和 KV load wait |
| `req-b1` | `instance-b` | same prompt | 验证不同实例 cache 隔离，不应命中 instance-a 的 blocks |

关键配置：

```yaml
simulation.mode: capacity_sweep_streaming
cache.mode: batch_aware_hbm_ddr_lru_progressive_timeline
sweep.hbm_capacity_blocks: [1, 4]
scheduler.max_num_batched_tokens: 4
model.default_cache.block_size_tokens: 4
model.default_cache.ddr_capacity_blocks: 64
model.default_latency.kv_load.mode: token_linear_v1
model.default_latency.kv_load.ddr_ms_per_cached_token: 0.5
output.cache_events: true
output.cache_event_capacities: [1]
```

为什么这样设计：

- `max_num_batched_tokens=4` 迫使长 prompt 被切成多个 prefill chunks。
- `block_size_tokens=4` 让 chunk boundary 与 full-block materialization 对齐，便于稳定验收。
- `hbm_capacity_blocks=1` 迫使 earlier blocks 从 HBM 淘汰，但仍在 DDR tier 中可命中。
- `instance-b` 只有一条同 prompt 请求，用来验证固定路由多实例隔离 replay。

## 5. 验收断言

E2E 断言：

1. CLI 返回成功。
2. `capacity_sweep.csv` 存在并包含 trace / instance rows。
3. trace row:
   - `timeline_mode == batch_aware_hbm_ddr_lru_progressive_timeline`
   - `ttft_granularity == chunk`
   - `total_chunk_count > 0`
   - `total_scheduled_chunk_count > 0`
   - `total_progressive_materialized_tokens > 0`
   - `total_kv_load_ms > 0`
   - `total_kv_load_wait_ms > 0`
   - `total_load_event_count > 0`
4. `instance-a`:
   - `ddr_hit_tokens > 0`
   - `total_kv_load_ms > 0`
   - `total_kv_load_wait_ms > 0`
5. `instance-b`:
   - `hbm_hit_tokens == 0`
   - `ddr_hit_tokens == 0`
   - `miss_tokens == total_prompt_tokens`
6. `cache_events.csv`:
   - HBM `MATERIALIZE` reason 为 `progressive_chunk_materialization`。
   - DDR `STORE` reason 为 `progressive_chunk_store`。
   - `instance-a` 有 DDR `lookup_hit`。
   - `instance-b` 没有 DDR `lookup_hit`。
7. `summary.md`:
   - 包含 `Timeline Results`。
   - 包含 progressive mode assumption。
   - 包含 selected cache event dump path。

## 6. 测试结果

已运行：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest tests/integration/test_step9_streaming_cli_e2e.py
```

结果：`1 passed`。

已运行 S9-H / S9-I 相关 targeted tests：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest \
  tests/integration/test_step9_streaming_cli_e2e.py \
  tests/integration/test_step9_streaming_progressive_timeline_e2e.py \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/streaming/test_metrics.py \
  tests/unit/experiment/test_sweep_metrics.py \
  tests/unit/report/test_sweep_summary.py
```

结果：`27 passed`。

已运行 ruff：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m ruff check src tests
```

结果：`All checks passed`。

已对 S9-I 新增 Python E2E 运行 format check：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m ruff format --check tests/integration/test_step9_streaming_cli_e2e.py
```

结果：`1 file already formatted`。

已运行全量测试：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
.venv/bin/python -m pytest
```

结果：`439 passed`。

已运行：

```bash
git diff --check
```

结果：通过，无输出。

## 7. 当前判断

S9-I 的 CLI 级 E2E 已通过，说明 Step9 progressive timeline mode 已能通过大 trace 主路径完成：

- request build。
- per-instance streaming replay。
- progressive materialization。
- DDR tier hit。
- KV load wait typed accounting。
- report/export typed result 输出。

本结论只表示 S9-I E2E 通过；不等价于 Step9 工程收口。Step9 review、主文档更新、全局记忆更新、
归档等工作仍需在后续单独 batch 中执行。
