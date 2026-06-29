# S7-B：CacheEvent Tier Schema 开发方案与执行记录

状态：已完成。

阶段类型：核心仿真器开发。

## 1. Batch 目标

S7-B 只扩展 cache event schema、event stats 和 CSV writer 测试，不实现 DDR cache，不修改 replay，不接 streaming runner。

目标是让 InferTwin 的事件系统可以稳定表达 Step7 之后的两级 cache 信号：

```text
HBM lookup / materialize / evict
DDR lookup / store / evict
```

S7-B 完成后，后续 S7-C / S7-D 可以直接复用同一套 `CacheEvent` 字段，不需要再改 CSV event schema。

## 2. 为什么需要 S7-B

当前 `CacheEvent` 只能表达 HBM 视角：

```python
CacheEvent(
    event_type,
    timestamp_ms,
    instance_uuid,
    request_id,
    block_key,
    block_index,
    token_count,
    cache_tier,
    reason,
    eviction_policy,
    hbm_used_blocks,
    hbm_capacity_blocks,
)
```

Step7 引入 DDR/CPU tier 后，需要把以下事件区分清楚：

- HBM hit。
- DDR hit。
- HBM materialize。
- DDR store。
- HBM evict。
- DDR evict。

如果仍然只用 HBM 字段，会导致两个问题：

1. report / benchmark / review 无法知道 hit 或 eviction 来自哪个 tier。
2. Step8 接 KV load latency 时，无法从事件流里找到 DDR load / store 的 token 基础。

S7-B 先把事件 schema 做稳，能避免 S7-C/S7-D 在实现 DDR cache 时顺手修改事件结构，降低后续 batch 的耦合。

## 3. 当前代码现状

相关文件：

```text
src/infertwin/cache/events.py
src/infertwin/cache/event_sink.py
src/infertwin/report/cache_events.py
src/infertwin/cache/hbm_lru.py
tests/unit/cache/test_cache_events.py
tests/unit/cache/test_cache_event_sink.py
tests/unit/report/test_cache_event_writer.py
tests/integration/test_step5_hbm_lru_runner.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
tests/integration/test_benchmark_streaming_replay_script.py
```

当前设计：

- `CACHE_EVENT_FIELDNAMES` 通过 `dataclasses.fields(CacheEvent)` 自动生成。
- `CsvCacheEventWriter` 使用 `asdict(event)` 写 CSV。
- `CacheEventStats` 统计 total / hit / miss / materialize / evict，以及 HBM peak/final used blocks。
- `HBMCache` 构造的所有事件都设置 `cache_tier=hbm`。
- streaming path 已经默认使用 stats-only 或 selected capacity CSV dump，事件大文件风险已被控制。

## 4. S7-B 目标 schema

### 4.1 新增常量

修改：

```text
src/infertwin/cache/events.py
```

新增：

```python
STORE = "store"
CACHE_TIER_DDR = "ddr"
```

暂不新增 `CACHE_TIER_NONE`。

原因：

- S7-B 只扩展 schema，不定义 tiered lookup miss 的最终语义。
- 现有 HBM lookup miss 仍保持 `cache_tier=hbm`。
- 如果 S7-D 的 `TieredPrefixCache` 需要 `none`，应在 S7-D 方案中单独评审。

### 4.2 扩展 CacheEvent

在现有字段末尾追加默认字段，保证旧构造点不需要修改：

```python
@dataclass(frozen=True, slots=True)
class CacheEvent:
    ...
    hbm_used_blocks: int
    hbm_capacity_blocks: int
    ddr_used_blocks: int = 0
    ddr_capacity_blocks: int = 0
    source_tier: str = ""
    target_tier: str = ""
    load_tokens: int = 0
    store_tokens: int = 0
```

字段含义：

- `ddr_used_blocks`：事件发生后 DDR/CPU tier resident block 数。
- `ddr_capacity_blocks`：DDR/CPU tier capacity。
- `source_tier`：数据来源 tier，预留给 Step8 load / promotion。
- `target_tier`：数据目标 tier，预留给 store / promotion。
- `load_tokens`：KV load 相关 token 数，Step7 仍保持 0。
- `store_tokens`：KV store 相关 token 数；S7-C/S7-D 中 DDR store event 会填入 block token 数。

### 4.3 扩展 CacheEventStats

修改：

```text
src/infertwin/cache/event_sink.py
```

新增字段：

```python
store_events: int = 0
peak_ddr_used_blocks: int = 0
final_ddr_used_blocks: int = 0
```

`record()` 行为：

- `event.event_type == STORE` 时，`store_events += 1`。
- `peak_ddr_used_blocks = max(peak_ddr_used_blocks, event.ddr_used_blocks)`。
- `final_ddr_used_blocks = event.ddr_used_blocks`。

旧 HBM 事件的 DDR 字段默认 0，所以旧统计结果不变。

### 4.4 CSV writer 行为

`CACHE_EVENT_FIELDNAMES` 继续由 `fields(CacheEvent)` 自动生成，不手写 header。

新增字段会自然追加到 CSV header 末尾：

```text
...,hbm_used_blocks,hbm_capacity_blocks,ddr_used_blocks,ddr_capacity_blocks,source_tier,target_tier,load_tokens,store_tokens
```

这样做的原因：

- 保持旧字段顺序不变，减少 CSV 消费方破坏面。
- 通过 dataclass field order 保持 header 稳定。
- 不需要在 writer 里写字段映射逻辑。

## 5. S7-B 不做什么

S7-B 不做：

- 不实现 `DDRLRUCache`。
- 不实现 `TieredPrefixCache`。
- 不修改 `HBMCache` 事件语义。
- 不修改 replay / scheduler。
- 不新增 `batch_aware_hbm_ddr_lru` mode。
- 不让 DDR hit / store / evict 真实出现。
- 不修改 capacity sweep row 的指标计算。
- 不接 Step8 KV load latency。

如果开发中发现必须修改 `src/infertwin/replay/`、`src/infertwin/streaming/replay.py` 或 `src/infertwin/streaming/sweep.py`，应暂停并重新评审，因为那说明 S7-B 已越界。

## 6. 代码编写方案

### B1. 扩展事件常量与 dataclass

修改：

```text
src/infertwin/cache/events.py
```

步骤：

1. 新增 `STORE` 常量。
2. 新增 `CACHE_TIER_DDR` 常量。
3. 在 `CacheEvent` 末尾追加 DDR / source / target / load / store 字段，均提供默认值。

验收点：

- 所有现有 `CacheEvent(...)` 构造点无需新增参数。
- HBM-only 测试继续通过。

### B2. 扩展 event stats

修改：

```text
src/infertwin/cache/event_sink.py
```

步骤：

1. import `STORE`。
2. `CacheEventStats` 新增 store 和 DDR peak/final fields。
3. `record()` 识别 `STORE`。
4. `snapshot()` 复制新增字段。

验收点：

- 旧 HBM event stats 不变。
- 人工构造 DDR store event 时，`store_events`、`peak_ddr_used_blocks`、`final_ddr_used_blocks` 正确。

### B3. 更新 package export

检查并按需修改：

```text
src/infertwin/cache/__init__.py
```

如果当前 cache package export 事件常量，则新增：

```python
STORE
CACHE_TIER_DDR
```

如果未集中 export，则不强行调整。

### B4. 更新 CSV writer 测试

修改：

```text
tests/unit/report/test_cache_event_writer.py
```

新增或更新断言：

- 空 CSV header 包含新增字段。
- 旧 HBM event 行新增字段为默认值：
  - `ddr_used_blocks == "0"`。
  - `ddr_capacity_blocks == "0"`。
  - `source_tier == ""`。
  - `target_tier == ""`。
  - `load_tokens == "0"`。
  - `store_tokens == "0"`。
- 人工 DDR store event 能写出：
  - `event_type == "store"`。
  - `cache_tier == "ddr"`。
  - `ddr_used_blocks` / `ddr_capacity_blocks`。
  - `target_tier == "ddr"`。
  - `store_tokens > 0`。

### B5. 更新 event sink 测试

修改：

```text
tests/unit/cache/test_cache_event_sink.py
```

新增测试：

- `StatsOnlyCacheEventSink` 能统计人工 DDR store event。
- `CacheEventStats.snapshot()` 包含 store / DDR 字段且不会被后续事件污染。

### B6. 更新 cache event schema 测试

修改：

```text
tests/unit/cache/test_cache_events.py
```

新增断言：

- HBMCache 旧事件的新增 DDR / load / store 字段是默认值。
- `cache_tier` 仍是 `hbm`，现有 HBM 行为不变。

### B7. 更新集成测试的 CSV header 断言

检查并按需更新：

```text
tests/integration/test_step5_hbm_lru_runner.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
tests/integration/test_benchmark_streaming_replay_script.py
```

目标：

- 不要求所有集成测试逐字段校验新增 header。
- 至少一个 integration test 确认 `cache_events.csv` 包含 `ddr_used_blocks` 和 `store_tokens`。

## 7. 测试计划

优先运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_cache_events.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/report/test_cache_event_writer.py
```

再运行 cache event 相关集成测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_step5_hbm_lru_runner.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_benchmark_streaming_replay_script.py
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/cache src/infertwin/report tests/unit/cache tests/unit/report tests/integration/test_step5_hbm_lru_runner.py tests/integration/test_true_streaming_capacity_sweep_runner.py tests/integration/test_benchmark_streaming_replay_script.py
.venv/bin/python -m ruff format --check src/infertwin/cache src/infertwin/report tests/unit/cache tests/unit/report tests/integration/test_step5_hbm_lru_runner.py tests/integration/test_true_streaming_capacity_sweep_runner.py tests/integration/test_benchmark_streaming_replay_script.py
git diff --check
```

## 8. S7-B 成功标准

S7-B 完成时应满足：

- `CacheEvent` 支持 DDR tier 字段。
- 旧 HBM event 构造点无需改参数，旧 HBM 行为不变。
- `CacheEventStats` 能统计 `STORE` 和 DDR used block peak/final。
- `cache_events.csv` header 稳定包含新增字段。
- 人工 DDR store event 可以通过 writer/sink 测试。
- S7-B 不引入任何真实 DDR hit / store / evict 行为。

## 9. 对后续 Batch 的影响

S7-C 可以使用：

```python
CacheEvent(event_type=STORE, cache_tier=CACHE_TIER_DDR, ...)
```

来记录 DDR store。

S7-D 可以用同一套字段合并 HBM 和 DDR events，不需要再改 CSV schema。

S7-F 可以从 `CacheEventStats` 读取：

```text
store_events
peak_ddr_used_blocks
final_ddr_used_blocks
```

来做 Step7 验收和 summary。

## 10. 风险与边界

### 10.1 风险

- CSV header 会新增字段，使用固定列数读取旧 cache event CSV 的外部脚本可能需要更新。
- `CacheEventStats` 字段变多，summary/report 如果未来展示所有 stats，需要避免信息过载。
- 如果 S7-B 提前规定 lookup miss 的 `cache_tier=none`，可能与 S7-D tiered lookup 语义冲突；因此本 batch 暂不新增 `none` tier。

### 10.2 控制方式

- 新字段只追加在 dataclass 末尾。
- 新字段都有默认值。
- 至少一个 writer test 固定 header。
- 不修改 HBMCache 的 `cache_tier=hbm` 行为。

## 11. 执行记录

### 11.1 做了什么

- 扩展 `src/infertwin/cache/events.py`：
  - 新增 `STORE = "store"`。
  - 新增 `CACHE_TIER_DDR = "ddr"`。
  - 在 `CacheEvent` 末尾追加 `ddr_used_blocks`、`ddr_capacity_blocks`、`source_tier`、`target_tier`、`load_tokens`、`store_tokens`，并提供默认值。
- 扩展 `src/infertwin/cache/event_sink.py`：
  - `CacheEventStats` 新增 `store_events`。
  - `CacheEventStats` 新增 DDR resident peak/final fields。
  - `record()` 支持 `STORE` 事件。
  - `snapshot()` 复制新增 stats 字段。
- 更新 `src/infertwin/cache/__init__.py`，导出 `STORE` 和 `CACHE_TIER_DDR`。
- 更新 cache event writer / sink / schema 单测。
- 更新一个 integration header 断言，确认真实 runner 输出的 `cache_events.csv` 包含 DDR / load / store 新字段。

### 11.2 没有做什么

- 没有实现 `DDRLRUCache`。
- 没有实现 `TieredPrefixCache`。
- 没有修改 HBM cache 事件语义。
- 没有修改 replay、scheduler、streaming runner 或 report 聚合语义。
- 没有新增 `batch_aware_hbm_ddr_lru` mode。
- 没有让真实 DDR hit / store / evict 行为出现。

### 11.3 影响

- 旧 HBM event 构造点无需新增参数，旧 HBM-only replay 行为不变。
- `cache_events.csv` header 新增字段，字段追加在末尾。
- `StatsOnlyCacheEventSink` 可以在大 trace 下统计未来 DDR store events 和 DDR resident peak/final。
- 后续 S7-C / S7-D 可以直接用同一套 event schema 发出 DDR tier events。

### 11.4 边界

- S7-B 只完成事件 schema 和 stats 扩展。
- 现有 HBM lookup miss 仍保持 `cache_tier=hbm`。
- 未新增 `CACHE_TIER_NONE`；tiered lookup miss 的最终口径留到 S7-D 评审。
- `load_tokens` 只是预留字段，Step7 仍不计算 KV load latency。

### 11.5 风险

- 外部如果按固定列数读取旧 `cache_events.csv`，需要适配新增 header。
- 事件字段已经预留 `source_tier` / `target_tier`，但 Step7 不做 promotion；后续不能误读为 promotion 已实现。
- 如果 S7-C/S7-D 对 store event 语义有新需求，应新增字段或常量评审，不能复用旧字段改口径。

### 11.6 测试结果

cache event 单测：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_cache_events.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/report/test_cache_event_writer.py
```

结果：

```text
10 passed
```

cache event 相关集成测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_step5_hbm_lru_runner.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_benchmark_streaming_replay_script.py
```

结果：

```text
10 passed
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/cache src/infertwin/report tests/unit/cache tests/unit/report tests/integration/test_step5_hbm_lru_runner.py tests/integration/test_true_streaming_capacity_sweep_runner.py tests/integration/test_benchmark_streaming_replay_script.py
.venv/bin/python -m ruff format --check src/infertwin/cache src/infertwin/report tests/unit/cache tests/unit/report tests/integration/test_step5_hbm_lru_runner.py tests/integration/test_true_streaming_capacity_sweep_runner.py tests/integration/test_benchmark_streaming_replay_script.py
git diff --check
```

结果：

```text
passed
```

### 11.7 是否建议进入下一 Batch

建议进入 S7-C：DDR LRU Tier。

进入方式仍应遵循 Step7 门禁：先提交 S7-C 详细代码开发方案和原因，经用户评审通过后再写代码。
