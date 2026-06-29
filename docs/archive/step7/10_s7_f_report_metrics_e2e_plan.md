# S7-F：Report / Metrics / E2E 开发方案与执行记录

状态：已完成。

阶段类型：核心结果验收 + 外围 report 适配。

## 1. Batch 目标

S7-F 的目标是对 Step7 已经接通的 HBM + DDR replay 做结果收口：

```text
sweep-streaming
-> batch_aware_hbm_ddr_lru
-> request metrics
-> streaming aggregator
-> capacity_sweep.csv
-> summary.md
-> cache_events.csv
```

S7-F 要确认：

- request-level token accounting 正确消费 DDR hit。
- trace / instance scope 聚合结果一致。
- `capacity_sweep.csv` 中 HBM / DDR / miss token 字段口径清楚。
- `summary.md` 不再错误声明 “DDR fields are reserved as 0”。
- selected capacity 的 `cache_events.csv` 与 trace row 的 `cache_event_count` 一致。
- package CLI `sweep-streaming` 可以生成完整 Step7 报告。

S7-F 不修改 replay 语义。

## 2. 为什么需要 S7-F

S7-E 已经让 streaming runner 能在显式 mode 下构造：

```text
TieredPrefixCache = HBMCache + DDRLRUCache
```

并且合成测试已经验证：

- 同实例可以产生 DDR hit。
- 不同实例 DDR cache 隔离。
- `ddr_hit_tokens > 0` 可以进入 capacity sweep rows。

但当前 report 层仍有旧口径：

```text
- DDR / SSD cache hits are not modeled yet; DDR fields are reserved as 0.
```

这在 `batch_aware_hbm_ddr_lru` 下已经不正确。

此外，Step7 需要一个正式端到端验收，证明核心仿真器输出没有被外围 report 扭曲：

- CSV 只导出 typed result。
- summary 只说明 typed result。
- cache event dump 只记录 replay events。
- report 不重新计算 replay / cache / latency。

## 3. 当前代码现状

### 3.1 Metrics 已具备 DDR 字段

相关文件：

```text
src/infertwin/replay/metrics.py
src/infertwin/streaming/metrics.py
src/infertwin/experiment/sweep.py
```

现有字段：

```text
BatchAwareRequestMetrics.hbm_hit_tokens
BatchAwareRequestMetrics.ddr_hit_tokens
BatchAwareRequestMetrics.miss_tokens

CapacitySweepRow.hbm_hit_tokens
CapacitySweepRow.ddr_hit_tokens
CapacitySweepRow.miss_tokens
CapacitySweepRow.total_hit_tokens
CapacitySweepRow.hbm_hit_rate
CapacitySweepRow.ddr_hit_rate
CapacitySweepRow.kv_hit_rate
```

streaming aggregator 已有 request-level invariant：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens
```

### 3.2 CSV 已自然包含 DDR 字段

相关文件：

```text
src/infertwin/report/sweep.py
```

`capacity_sweep.csv` 通过 dataclass rows 输出，因此 `CapacitySweepRow` 中已有字段会自然进入 CSV。

S7-F 不需要新增 CSV schema 字段，重点是测试和口径说明。

### 3.3 Summary 口径需要修正

当前 summary assumptions 中仍写：

```text
- Finite instance-local HBM prefix cache.
- DDR / SSD cache hits are not modeled yet; DDR fields are reserved as 0.
```

在 Step7 DDR mode 下，应改成 mode-aware 文案：

```text
batch_aware_hbm_lru:
  - Finite instance-local HBM prefix cache.
  - DDR fields are reserved as 0 in this mode.

batch_aware_hbm_ddr_lru:
  - Finite instance-local HBM prefix cache.
  - Finite instance-local DDR/CPU prefix cache.
  - DDR hit accounting is modeled.
  - DDR KV load latency is not modeled in Step7.
  - DDR hit promotion to HBM is not modeled in Step7.
  - Cross-instance pooling is not modeled.
```

### 3.4 Cache event dump 已是 streaming writer

相关文件：

```text
src/infertwin/report/cache_events.py
src/infertwin/cache/event_sink.py
```

`CsvCacheEventWriter` 已经 streaming 写文件，并维护 `CacheEventStats`。

S7-F 应补验收：

```text
trace row cache_event_count == cache_events.csv data row count
cache_events.csv contains DDR store / lookup_hit rows in DDR mode
instance row cache_event_count == 0
```

## 4. 核心语义

### 4.1 Token accounting invariant

S7-F 必须持续验证：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens
total_hit_tokens == hbm_hit_tokens + ddr_hit_tokens
kv_hit_rate == total_hit_tokens / total_prompt_tokens
hbm_hit_rate == hbm_hit_tokens / total_prompt_tokens
ddr_hit_rate == ddr_hit_tokens / total_prompt_tokens
```

该 invariant 应对 trace row 和每个 instance row 都成立。

### 4.2 Trace / instance aggregation invariant

对同一个 capacity：

```text
trace.request_count == sum(instance.request_count)
trace.iteration_count == sum(instance.iteration_count)
trace.total_prompt_tokens == sum(instance.total_prompt_tokens)
trace.hbm_hit_tokens == sum(instance.hbm_hit_tokens)
trace.ddr_hit_tokens == sum(instance.ddr_hit_tokens)
trace.miss_tokens == sum(instance.miss_tokens)
trace.total_hit_tokens == sum(instance.total_hit_tokens)
```

`cache_event_count` 例外：

```text
trace row 记录本 capacity 的总事件数
instance row 仍为 0
```

这延续 Step6 / streaming runner 的既定口径。未来如果需要 instance-level event stats，应新增字段，例如：

```text
instance_cache_event_count
```

不能改变当前 `cache_event_count` 语义。

### 4.3 Summary 不重新计算

`summary.md` 必须只消费：

```text
CapacitySweepRow
config_details
cache_event_paths
```

禁止在 summary 中重新读取 trace、重新跑 cache lookup、重新计算 TTFT。

### 4.4 DDR hit 不等于 KV load latency

Step7 只完成：

```text
tier hit accounting
```

Step7 summary 必须明确：

```text
DDR hit tokens are accounted.
DDR KV load latency is not modeled.
```

否则读者可能误以为 DDR hit 已经产生通信/加载时延。

## 5. 代码开发顺序

### S7-F1：Summary Mode-Aware Assumptions

职责：

- 修改 `write_capacity_sweep_summary()` 的 assumptions。
- 根据 `config_details["streaming_cache_mode"]` 渲染不同 cache mode 的说明。
- 在 Config section 中显示：
  - `streaming_cache_mode`
  - `streaming_cache_eviction_policy`
- 在 summary 中补充 `DDR hit accounting` 与 `KV load latency` 的区别。

建议新增 helper：

```python
def _cache_assumption_lines(config_details: Mapping[str, object]) -> list[str]:
    ...
```

原因：

- 避免把 mode 判断散落在 summary 主函数里。
- 保持 report 是 render typed result，而不是分析逻辑。

不做：

- 不改 `CapacitySweepRow`。
- 不改 `capacity_sweep.csv` schema。
- 不读 event dump 文件。

### S7-F2：Metrics Invariant Tests

职责：

- 新增或扩展 integration test，验证 trace / instance rows 的 token 和 aggregation invariant。
- 覆盖 HBM-only 和 HBM+DDR 两种 mode。

建议新增测试文件：

```text
tests/integration/test_step7_report_metrics_e2e.py
```

核心断言：

```python
assert row.hbm_hit_tokens + row.ddr_hit_tokens + row.miss_tokens == row.total_prompt_tokens
assert row.total_hit_tokens == row.hbm_hit_tokens + row.ddr_hit_tokens
assert trace_row.ddr_hit_tokens == sum(instance.ddr_hit_tokens)
```

原因：

- Step7 后续还会接 KV load latency 和 progressive visibility，必须先固定现有 token 统计口径。

不做：

- 不把 test helper 写进生产代码，除非确实需要复用。

### S7-F3：Cache Event Dump Consistency

职责：

- 端到端运行 `sweep-streaming`，开启 selected capacity event dump。
- 读取 `cache_events.csv`。
- 验证：

```text
len(data rows) == trace_row.cache_event_count
DDR mode 下存在 cache_tier=ddr 的 store
DDR mode 下存在 cache_tier=ddr 的 lookup_hit
instance rows cache_event_count == 0
```

可选断言：

```text
store rows 的 store_tokens > 0
DDR rows 的 ddr_capacity_blocks == model default ddr_capacity_blocks
```

原因：

- S7-E 已经证明 DDR hit 能进入 metrics。
- S7-F 需要证明 event dump 与 stats 口径一致。

不做：

- 不实现 instance-level event aggregation。
- 不把 cache event dump 变成必开输出。

### S7-F4：Package CLI E2E

职责：

- 使用 package CLI 入口：

```python
from infertwin.cli.main import run_streaming_capacity_sweep
```

或命令等价入口：

```bash
PYTHONPATH=src .venv/bin/python -m infertwin.cli.main sweep-streaming --config ...
```

- 用 Step7 DDR config 合成数据生成：

```text
capacity_sweep.csv
summary.md
capacity_<N>/cache_events.csv
```

断言：

- `capacity_sweep.csv` 中 `ddr_hit_tokens > 0`。
- summary 包含：
  - `batch_aware_hbm_ddr_lru`
  - `DDR hit accounting`
  - `KV load latency is not modeled`
- cache event dump 文件存在。

原因：

- 同事实际使用主要通过 CLI / config。
- S7-F 需要证明用户入口可用，而不是只有 Python runner 单测可用。

### S7-F5：Docs / Memory / Execution Record

职责：

- 更新本文件执行记录。
- 更新 `docs/step7/README.md`。
- 更新 `docs/global_memory.md`。
- 若改动 summary 口径，也可轻量更新相关主文档中 Step7 状态。

不做：

- 不归档 Step7；归档放到 S7-G。
- 不写最终 Step7 review；review 放到 S7-G。

## 6. 预计文件改动

预计修改：

```text
src/infertwin/report/sweep.py
tests/integration/test_step7_report_metrics_e2e.py
docs/step7/10_s7_f_report_metrics_e2e_plan.md
docs/step7/README.md
docs/global_memory.md
```

可能修改：

```text
tests/integration/test_true_streaming_capacity_sweep_runner.py
tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

预计不修改：

```text
src/infertwin/replay/event_loop.py
src/infertwin/streaming/replay.py
src/infertwin/streaming/cache_factory.py
src/infertwin/cache/tiered.py
src/infertwin/cache/ddr_lru.py
src/infertwin/experiment/sweep.py
```

如果开发中发现必须修改 replay、cache backend 或 streaming replay engine，应暂停并重新评审，因为 S7-F 不应改变 replay 语义。

## 7. 测试计划

S7-F 开发完成后，至少运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_step7_report_metrics_e2e.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/unit/report/test_cache_event_writer.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

再运行 Step7 相关回归：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/cache/test_tiered_prefix_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/integration/test_streaming_runtime_integration.py \
  tests/integration/test_v1_review_repair_e2e.py
```

如果耗时可接受，S7-F 结束前建议运行全量：

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

并运行：

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

## 8. 验收标准

S7-F 通过条件：

1. `summary.md` 不再错误声明 DDR 字段恒为 0。
2. HBM-only summary 仍明确 DDR 字段在该 mode 下为 0 / reserved。
3. DDR mode summary 明确：
   - DDR hit accounting 已建模。
   - DDR KV load latency 未建模。
   - DDR hit promotion 未建模。
   - cross-instance pooling 未建模。
4. `capacity_sweep.csv` trace row 和 instance row token invariant 成立。
5. trace row token stats 等于 instance rows 汇总。
6. `cache_events.csv` data row count 等于 trace row `cache_event_count`。
7. DDR mode event dump 中有 DDR `store` 和 DDR `lookup_hit`。
8. package CLI E2E 生成 `capacity_sweep.csv`、`summary.md` 和 selected cache event dump。
9. targeted tests、ruff、`git diff --check` 通过。
10. 如运行全量 pytest，结果记录进执行记录。

## 9. 风险与边界

风险：

- summary 文案如果写得太强，可能让用户误以为 Step7 已经建模 DDR load latency。
- 事件统计如果只看 trace row，不看 raw dump，可能漏掉 writer / stats 不一致。
- 如果为 report 增加过多逻辑，可能违反 “HTML/CLI/report 不重算核心分析” 原则。

边界：

- S7-F 可以适配 report 文案和测试。
- S7-F 不改变 replay、cache、scheduler、latency backend。
- S7-F 不新增外围产品形态。
- S7-F 不归档 Step7；归档在 S7-G。

## 10. 进入 S7-G 的条件

满足以下条件后，可以进入 S7-G：

- Step7 DDR mode 的 CSV / summary / event dump 已完成端到端验收。
- S7-F 执行记录完整。
- 当前 Step7 已无必须在 S7-F 内处理的 report/metrics 口径问题。

S7-G 再做：

- Step7 核心仿真器 review。
- 主文档更新。
- 与 vLLM / vLLM-Ascend / Mooncake 差异最终整理。
- `docs/step7/` 归档。

## 11. 执行记录

状态：已完成。

### 11.1 做了什么

- 修正 `src/infertwin/report/sweep.py` 中 `summary.md` 的 Step7 cache 口径。
- 新增 mode-aware summary assumptions：
  - `batch_aware_hbm_lru`：DDR / SSD hit 在该 mode 下不建模，DDR 字段为 0 / reserved。
  - `batch_aware_hbm_ddr_lru`：DDR hit accounting 已建模，DDR KV load latency 未建模，DDR hit promotion 未建模，cross-instance pooling 未建模。
  - 未知 / legacy mode：保守说明 DDR / SSD hit 只有在 streaming cache mode 显式启用时才建模。
- Summary Config 区域新增：
  - `Streaming cache mode`
  - `Cache eviction policy`
- 新增 `tests/integration/test_step7_report_metrics_e2e.py`。
- 新增 package CLI E2E 验收：
  - 调用 `run_streaming_capacity_sweep(config_path)`。
  - 生成 `capacity_sweep.csv`。
  - 生成 `summary.md`。
  - 生成 selected capacity 的 `cache_events.csv`。
- 新增 token invariant 验收：
  - `hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens`。
  - `total_hit_tokens == hbm_hit_tokens + ddr_hit_tokens`。
  - `kv_hit_rate / hbm_hit_rate / ddr_hit_rate` 与 token 统计一致。
- 新增 trace / instance aggregation invariant 验收：
  - trace row 的 request / iteration / token stats 等于 instance rows 汇总。
  - instance rows 的 `cache_event_count` 保持 0。
- 新增 cache event dump 一致性验收：
  - `len(cache_events.csv data rows) == trace_row.cache_event_count`。
  - DDR mode 下存在 `cache_tier=ddr` 的 `store`。
  - DDR mode 下存在 `cache_tier=ddr` 的 `lookup_hit`。
  - DDR rows 的 `ddr_capacity_blocks` 来自 model default cache。
- 修正 `tests/golden/test_batch_aware_hbm_lru_golden.py` 中 `CacheEventStats` 扩展后的 golden 断言，补齐 HBM-only 下为 0 的 DDR / store stats。

### 11.2 没有做什么

- 没有修改 replay event loop。
- 没有修改 streaming replay engine。
- 没有修改 cache backend。
- 没有修改 scheduler。
- 没有修改 latency backend。
- 没有新增 CSV schema 字段。
- 没有新增外围产品能力。
- 没有实现 DDR KV load latency；该能力仍属于 Step8。
- 没有实现 progressive block visibility；该能力仍属于 Step9。
- 没有实现 cross-instance pooling。

### 11.3 影响

- `summary.md` 不再错误声明 “DDR fields are reserved as 0” 于 DDR mode。
- HBM-only summary 仍保留 DDR fields reserved 的 mode-specific 口径。
- Step7 DDR mode 的 CSV、summary、cache event dump 现在有端到端验收覆盖。
- 全量 pytest 现在覆盖 S7-A 到 S7-F 的主要链路，且全部通过。
- Report 仍只消费 typed result，没有反向修改核心 replay 语义。

### 11.4 边界

- S7-F 属于核心结果验收 + 外围 report 适配，不是新 replay 能力。
- Summary 只基于 `CapacitySweepRow`、`config_details` 和 `cache_event_paths` 渲染。
- `capacity_sweep.csv` 仍由 `CapacitySweepRow` dataclass 导出，不在 report 层重算指标。
- `cache_event_count` 继续保持 trace row 有值、instance row 为 0 的既定口径。
- DDR hit accounting 不代表 DDR KV load latency 已建模。

### 11.5 风险

- Summary 文案需要在 Step8 后再次更新，因为 Step8 会引入 KV load latency。
- Step9 引入 progressive visibility 后，DDR/HBM hit 产生时机可能变化，需要新增 mode 或更新 summary 口径。
- 当前 event dump 验收只检查 selected capacity；如果未来支持多 capacity event dump，需要扩展测试矩阵。
- 当前 instance-level event count 仍不提供；如果用户需要，应新增字段而不是改写 `cache_event_count`。

### 11.6 测试结果

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step7_report_metrics_e2e.py
```

结果：

```text
2 passed
```

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_step7_report_metrics_e2e.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/unit/report/test_cache_event_writer.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/cache/test_tiered_prefix_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/integration/test_streaming_runtime_integration.py \
  tests/integration/test_v1_review_repair_e2e.py
```

结果：

```text
45 passed
```

首次全量 pytest 发现一个 golden 测试未跟上 S7-B 的 `CacheEventStats` 字段扩展：

```text
tests/golden/test_batch_aware_hbm_lru_golden.py
```

修正后已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/golden/test_batch_aware_hbm_lru_golden.py
```

结果：

```text
1 passed
```

全量测试已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

结果：

```text
307 passed
```

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

### 11.7 是否建议进入下一 batch

建议进入 S7-G：Review / Docs / Archive。

原因：

- Step7 DDR mode 的 CSV / summary / event dump 已完成端到端验收。
- 全量测试、ruff、`git diff --check` 通过。
- S7-F 未留下必须在本 batch 内处理的 report/metrics 口径问题。
- 下一步应进行 Step7 整体 review、主文档更新、差异说明收口和 `docs/step7/` 归档。
