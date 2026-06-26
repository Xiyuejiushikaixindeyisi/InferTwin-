# EO-H 执行记录：vLLM cached_tokens accounting 贯穿 replay lookup

## 开发对象

```text
核心仿真器
```

EO-H 修复的是核心 replay 的 cached token 统计口径，不是外围 report 能力。

## 背景

EO-C 已经实现了 `CacheBlockConversionPolicy`，能够计算：

- `prompt_tokens - 1`。
- runtime / effective block size。
- PCP / DCP 放大后的 effective block size。
- MTP / EAGLE / EAGLE3 的 speculative drop blocks。
- full-block floor 后的 cached tokens。

但在 EO-H 之前，batch-aware replay 的 HBM lookup 仍然直接使用 raw resident blocks：

```text
raw PrefixLookupResult
-> sum(hbm_hit_block.token_count)
-> hbm_hit_tokens / miss_tokens
```

这会让以下场景偏离 vLLM usage cached_tokens：

- prompt 刚好等于一个 block 时被统计成 100% hit。
- final partial block 被计入 hit。
- MTP / EAGLE / EAGLE3 drop one block 没有贯穿 replay metrics。
- raw resident hit 与 usage cached_tokens 混在同一个口径里。

## 本轮实现

### 1. 新增 Cached Token Accounting 层

新增：

```text
src/hitfloor/cache/cached_token_accounting.py
```

核心类型：

```text
AccountedLookupResult
account_prefix_lookup()
```

处理关系：

```text
Raw PrefixLookupResult
+ prompt_tokens
+ CacheBlockConversionResult
-> AccountedLookupResult
```

职责边界：

- `HBMCache` 仍然只负责 hash-only resident lookup、LRU touch 和 cache event。
- `CacheBlockConversionPolicy` 仍然只负责 vLLM-like cached-token 上限。
- `account_prefix_lookup()` 负责把 raw resident lookup 转成 replay-facing metrics。
- `report/`、`cli/`、`scripts/` 不重新计算 cached tokens。

### 2. 贯穿 batch-aware replay metrics

修改：

```text
src/hitfloor/replay/metrics.py
src/hitfloor/replay/event_loop.py
```

变化：

- `LookupMetrics.from_result()` 现在需要 `SimulationRequest`。
- `state.set_cache_lookup()` 使用 accounted `effective_hit_tokens` 和 `miss_tokens`。
- `BatchAwareRequestMetrics.hbm_hit_tokens` / `miss_tokens` / `effective_hit_rate` 使用 accounted 口径。
- `BatchAwareReplayEngine` materialize 时使用 `materialization_blocks`，不再把 usage miss tokens 等同于 raw miss blocks。

新增字段：

```text
LookupMetrics.materialization_blocks
LookupMetrics.raw_hbm_hit_tokens
LookupMetrics.raw_ddr_hit_tokens
LookupMetrics.raw_miss_tokens
LookupMetrics.cached_token_cap
```

### 3. 区分 miss_tokens 和 materialization_blocks

EO-H 后必须区分：

```text
miss_tokens
  = vLLM-like usage 口径下需要计算 / 重算的 tokens。

materialization_blocks
  = raw cache lookup 中不 resident、finish-time 后需要写入 HBM metadata 的 blocks。
```

典型例子：

```text
prompt_tokens = 4
effective_block_size = 4
raw HBM lookup hits one resident block
```

由于 vLLM 会使用 `prompt_tokens - 1`，该请求：

```text
raw_hbm_hit_tokens = 4
hbm_hit_tokens = 0
miss_tokens = 4
materialization_blocks = ()
```

也就是说，raw cache 命中了 block metadata，但 usage cached_tokens 仍为 0；因为这个 block 已经 resident，所以 finish 后不需要再次 materialize。

### 4. 早期 InfiniteHBMReplayEngine 同步口径

修改：

```text
src/hitfloor/instance/replay.py
```

原因：

- `ExperimentRunner` 的部分 legacy path 仍会使用 `InfiniteHBMReplayEngine`。
- 同一条 `SimulationRequest` 不应在 infinite replay 和 batch-aware replay 中出现不同 cached_tokens 统计口径。

变化：

- infinite replay 也调用 `account_prefix_lookup()`。
- materialization 仍使用 raw miss blocks。
- 空 materialization blocks 不再入队。

### 5. Cache event 语义保持不变

EO-H 不改变 cache event 的物理含义：

- `LOOKUP_HIT`：block metadata resident 且 prefix chain 命中。
- `LOOKUP_MISS`：block metadata 不 resident。
- `MATERIALIZE`：finish-time 写入 HBM metadata。
- `EVICT`：HBM metadata eviction。

因此：

```text
cache event raw hit tokens != report usage cached_tokens
```

后续如果要同时展示 raw cache hit 和 usage cached_tokens，应新增字段，不复用现有 `hbm_hit_tokens`。

## 测试覆盖

新增：

```text
tests/unit/cache/test_cached_token_accounting.py
```

覆盖：

- prompt tokens 等于 block size 时，raw hit 但 accounted hit 为 0。
- partial block 不计入 cached tokens。
- speculative drop blocks 使用 actual raw matched blocks。
- PCP / DCP effective block size 控制 accounting。
- HBM eviction 后 raw resident hit 小于 max-matchable 时，以 actual raw matched blocks 为准。

更新：

```text
tests/unit/replay/test_batch_aware_replay.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/golden/test_batch_aware_hbm_lru_golden.py
tests/unit/test_infinite_hbm_replay.py
tests/integration/test_step4_batch_aware_replay.py
tests/integration/test_step5_hbm_lru_e2e.py
```

重要测试口径变化：

- 普通 full-attention prompt 的重复请求不再默认 zero-miss。
- 空 prompt 仍覆盖 zero-miss fast-finish 机制。
- Step5 E2E 使用更小 block size 来观测 capacity 足够时的 vLLM-like cached tokens。

## 验证结果

```text
PYTHONPATH=src .venv/bin/python -m pytest
152 passed

.venv/bin/python -m ruff check src tests
All checks passed

.venv/bin/python -m ruff format --check src tests
122 files already formatted

git diff --check
passed
```

## 不做

EO-H 不实现：

- progressive block visibility。
- decode / TPOT。
- physical KV slots、pinned/refcount。
- DDR / SSD / Mooncake pooling。
- `capacity_sweep.csv` schema 修改。

用户已接受：

- finish-time materialization 可能低估长 prefill 期间的 block reuse，后续通过新 replay/cache mode 处理。
- decode / TPOT 仍未建模，后续通过 decode-aware scheduler / replay mode 处理。

## 收口结论

EO-H 已完成工程优化阶段最关键的 cached_tokens 口径修正。

当前 `batch_aware_hbm_lru` 的 replay-facing hit tokens 已不再直接等于 raw resident block tokens，而是使用 vLLM-like usage cached_tokens：

```text
accounted_hbm_hit_tokens
= full effective blocks actually matched
  capped by prompt_tokens - 1
  adjusted by speculative_drop_blocks
```

核心仿真器仍然是离线近似仿真器，但 cached_tokens accounting 已从“raw block resident 口径”收敛到更贴近 vLLM / vLLM-Ascend usage 的口径。
